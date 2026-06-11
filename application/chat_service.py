"""对话编排 — 意图识别 → 路由 → 返回结果

持仓录入完全依赖 LLM 语境理解：MiniMax 在对话响应中自动标记
[POSITION] 数据 → 解析 → 询问用户确认 → 写入。
"""

import json
import logging
import re

from application.analysis_service import AnalysisService
from application.position_service import PositionService
from application.stock_picker_service import StockPickerService
from application.subscription_service import SubscriptionService
from domain.intent_parser import Intent, parse_intent
from domain.models.user_context import UserContext
from domain.prompt_builder import build_chat_prompt, build_system_prompt
from infrastructure.akshare_client import AKShareClient
from infrastructure.minimax_client import MiniMaxClient
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)

_POSITION_TAG_RE = re.compile(
    r"\[POSITION\]\s*(\{.*?\})\s*\[/POSITION\]", re.DOTALL
)


class ChatService:
    def __init__(self):
        self.analysis = AnalysisService()
        self.subscription = SubscriptionService()
        self.position = PositionService()
        self.minimax = MiniMaxClient()
        self.user_repo = UserRepository()
        self.akshare = AKShareClient()
        self.picker = StockPickerService()

    async def handle(self, wechat_id: str, message: str, ctx: UserContext) -> str:
        await self.user_repo.append_chat(wechat_id, "user", message)

        parsed = parse_intent(message)
        logger.info("用户 %s 意图: %s, 代码: %s", wechat_id, parsed.intent, parsed.stock_code)

        try:
            response = await self._dispatch(wechat_id, parsed, ctx, message)
        except Exception as e:
            logger.exception("dispatch error for intent %s", parsed.intent)
            response = f"功能暂时不可用，请稍后再试。（{type(e).__name__}: {e}）"

        await self.user_repo.append_chat(wechat_id, "assistant", response)
        return response

    async def _dispatch(self, wechat_id, parsed, ctx, message) -> str:
        # 有 pending 持仓时，优先处理确认/取消
        if self.position.has_pending(wechat_id):
            if parsed.intent == Intent.CONFIRM:
                result = await self.position.confirm_pending(wechat_id)
                if result:
                    return result
            if parsed.intent == Intent.CANCEL:
                result = self.position.cancel_pending(wechat_id)
                if result:
                    return result

        match parsed.intent:
            case Intent.ANALYZE_STOCK:
                return await self._handle_analyze(parsed.stock_code, ctx, message)

            case Intent.SUBSCRIBE:
                if not parsed.stock_code:
                    return "请提供股票代码，如「关注 000001」。"
                return await self.subscription.subscribe(wechat_id, parsed.stock_code)

            case Intent.UNSUBSCRIBE:
                if not parsed.stock_code:
                    return "请提供股票代码，如「取消关注 000001」。"
                return await self.subscription.unsubscribe(wechat_id, parsed.stock_code)

            case Intent.SHOW_WATCHLIST:
                return await self.subscription.show_watchlist(wechat_id)

            case Intent.MARKET_OVERVIEW:
                return await self.analysis.get_market_overview()

            case Intent.BACKTEST:
                from application.backtest_service import BacktestService
                return await BacktestService().run_backtest(days=30)

            case Intent.RECOMMEND:
                return await self.picker.pick(ctx, wechat_id)

            case Intent.SCREEN_STOCKS:
                return await self._handle_screen(parsed.raw_text)

            case Intent.ANALYZE_STOCK_DEEP:
                if not parsed.stock_code:
                    return "请提供股票代码，如「深度分析 600519」。"
                return await self.analysis.analyze_stock_deep(parsed.stock_code)

            case Intent.ANALYZE_FUTURES:
                return await self.analysis.analyze_futures(parsed.raw_text)

            case Intent.CLOSE_POSITION:
                return await self.position.close_position(
                    wechat_id, parsed.stock_code, parsed.price)

            case Intent.SHOW_POSITIONS:
                return await self.position.show_positions(wechat_id)

            case _:
                # FREE_CHAT / ADD_POSITION / CONFIRM(无pending) / CANCEL(无pending) → 通用对话
                return await self._handle_chat_with_position_detect(wechat_id, ctx, message)

    async def _handle_chat_with_position_detect(
        self, wechat_id: str, ctx: UserContext, message: str
    ) -> str:
        """通用对话 — MiniMax 正常回复，同时自动检测持仓语境"""
        response = await self._call_chat(ctx, message)

        # 从响应中提取 [POSITION] 标记
        position_data = self._extract_position_tag(response)
        clean_response = _POSITION_TAG_RE.sub("", response).strip()

        if not position_data:
            return clean_response

        # 代码层硬过滤：用户原文必须满足持仓条件，不管 LLM 输出什么
        if not self._user_message_has_position(message):
            logger.info("LLM 输出了 POSITION 标记但用户原文不符合持仓条件，忽略: %s", message[:50])
            return clean_response

        # 验证股票代码并存入 pending
        confirm_text = await self.position.store_pending_from_llm(wechat_id, position_data)
        if not confirm_text:
            return clean_response

        return f"{clean_response}\n\n{confirm_text}"

    @staticmethod
    def _user_message_has_position(text: str) -> bool:
        """硬校验：用户原文是否真的在描述持仓（不依赖 LLM）

        必须同时满足：
        1. 含具体数字（数量或价格）
        2. 含持仓量词（股/手/份）或成本词（成本/均价/买入价）
        3. 不是纯策略/计划类表述
        """
        import re

        # 排除：纯策略/计划/提问（无论含什么数字）
        strategy_words = {"会", "准备", "打算", "计划", "想要", "应该", "建议",
                          "可以", "能不能", "好不好", "要不要", "是否"}
        if any(w in text for w in strategy_words) and not re.search(r"\d+\s*(?:股|手|份)", text):
            return False

        # 必须含数字
        if not re.search(r"\d", text):
            return False

        # 必须含量词或成本词
        has_unit = bool(re.search(r"\d+\s*(?:股|手|份)", text))
        has_cost = any(w in text for w in ("成本", "均价", "买入价", "成本价"))

        return has_unit or has_cost

    def _extract_position_tag(self, response: str) -> dict | None:
        """从 MiniMax 响应中解析 [POSITION]...[/POSITION] 标记"""
        match = _POSITION_TAG_RE.search(response)
        if not match:
            return None

        try:
            data = json.loads(match.group(1))
            positions = data.get("positions", [])
            if not positions:
                return None
            return data
        except (json.JSONDecodeError, KeyError):
            logger.warning("解析 POSITION 标记失败: %s", match.group(1)[:200])
            return None

    async def _call_chat(self, ctx: UserContext, message: str) -> str:
        """调用 MiniMax 对话（system prompt 已包含持仓检测指令）"""
        system = build_system_prompt()
        user_prompt = build_chat_prompt(ctx, message)

        messages = []
        for msg in ctx.recent_chat:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_prompt})

        return await self.minimax.chat(system_prompt=system, messages=messages)

    # ── 其他处理 ──

    async def _handle_analyze(self, stock_code: str | None, ctx: UserContext, message: str) -> str:
        if not stock_code:
            return await self._handle_chat_with_position_detect(
                ctx.profile.wechat_id, ctx, message)
        return await self.analysis.analyze_stock(stock_code)

    async def _handle_screen(self, text: str) -> str:
        """条件筛选 — 从预设策略或涨幅榜里跑筛选"""
        from domain.stock_screener import PRESET_STRATEGIES, get_preset_names
        from domain.stock_analyzer import analyze_technical

        # 匹配预设策略
        matched_strategy = None
        for name in get_preset_names():
            if name in text:
                matched_strategy = name
                break

        if not matched_strategy:
            presets = "、".join(get_preset_names())
            return f"支持以下筛选策略：{presets}\n\n示例：「金叉选股」「超跌反弹」「强势突破」「均线多头」"

        strategy = PRESET_STRATEGIES[matched_strategy]
        conditions = strategy["conditions"]

        # 从涨幅榜取样本股
        rank_data = await self.akshare.get_stock_rank_list(count=50)
        if not rank_data:
            return "暂时无法获取行情数据。"

        from domain.stock_screener import evaluate_all

        results = []
        for stock in rank_data[:30]:
            code = stock["code"]
            bars = await self.akshare.get_stock_history(code, days=30)
            if len(bars) < 2:
                continue

            tech = analyze_technical(bars)
            if not tech:
                continue

            indicators = {
                "ma5": tech.ma5, "ma10": tech.ma10, "ma20": tech.ma20,
                "macd_hist": tech.macd_hist, "rsi_14": tech.rsi_14,
                "change_pct": stock["change_pct"],
                "price_above_ma20": 1 if bars[-1].close > tech.ma20 else 0,
                "volume_ratio": bars[-1].volume / max(
                    sum(b.volume for b in bars[-6:-1]) / 5, 1
                ) if len(bars) >= 6 else 1,
            }
            prev_indicators = {
                "ma5": tech.ma5, "ma10": tech.ma10, "ma20": tech.ma20,
            }

            if evaluate_all(conditions, indicators, prev_indicators):
                results.append(
                    f"  {stock['name']}（{code}）"
                    f"  价格{stock['price']}  涨跌{stock['change_pct']:+.2f}%"
                    f"  RSI={tech.rsi_14:.0f}"
                )

        if not results:
            return f"「{matched_strategy}」未筛到符合条件的股票，可能当前市场环境不适合该策略。"

        header = f"📋 {matched_strategy}（{strategy['name']}）筛选结果：\n"
        footer = "\n\n回复股票代码可查看详细分析。\n以上仅供参考，不构成投资建议。"
        return header + "\n".join(results[:10]) + footer

