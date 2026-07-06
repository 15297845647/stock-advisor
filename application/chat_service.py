"""对话编排 — 意图识别 → 路由 → 返回结果"""

import logging

from application.analysis_service import AnalysisService
from application.intent_service import IntentService
from application.stock_picker_service import StockPickerService
from application.subscription_service import SubscriptionService
from domain.intent_parser import Intent
from domain.models.user_context import UserContext
from domain.prompt_builder import build_chat_prompt, build_system_prompt
from infrastructure.akshare_client import AKShareClient
from infrastructure.minimax_client import MiniMaxClient
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)

# 中文数字 → 阿拉伯数字（用于解析"再推两个"）
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_count(message: str) -> int | None:
    """从消息中解析期望推荐条数，如"再推两个""推荐3只"，无则返回 None"""
    import re

    m = re.search(r"(\d+)\s*(?:只|个|支|条)?", message)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 20 else None
    for cn, num in _CN_NUM.items():
        if cn in message:
            return num
    return None


class ChatService:
    def __init__(self):
        self.analysis = AnalysisService()
        self.subscription = SubscriptionService()
        self.minimax = MiniMaxClient()
        self.user_repo = UserRepository()
        self.akshare = AKShareClient()
        self.picker = StockPickerService()
        self.intent_service = IntentService()

    async def handle(self, wechat_id: str, message: str, ctx: UserContext) -> str:
        await self.user_repo.append_chat(wechat_id, "user", message)

        parsed = await self.intent_service.parse(message, ctx.recent_chat)
        logger.info("用户 %s 意图: %s, 代码: %s, 数量: %s",
                    wechat_id, parsed.intent, parsed.stock_code, parsed.count)

        try:
            response = await self._dispatch(wechat_id, parsed, ctx, message)
        except Exception as e:
            logger.exception("dispatch error for intent %s", parsed.intent)
            response = f"功能暂时不可用，请稍后再试。（{type(e).__name__}: {e}）"

        await self.user_repo.append_chat(wechat_id, "assistant", response)
        return response

    async def _dispatch(self, wechat_id, parsed, ctx, message) -> str:
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
                return await self.picker.pick(ctx, wechat_id, parsed.count or _parse_count(message))

            case Intent.SCREEN_STOCKS:
                return await self._handle_screen(parsed.raw_text)

            case Intent.ANALYZE_STOCK_DEEP:
                if not parsed.stock_code:
                    return "请提供股票代码，如「深度分析 600519」。"
                return await self.analysis.analyze_stock_deep(parsed.stock_code)

            case Intent.ANALYZE_FUTURES:
                return await self.analysis.analyze_futures(parsed.raw_text)

            case _:
                # FREE_CHAT → 通用对话
                return await self._call_chat(ctx, message)

    async def _call_chat(self, ctx: UserContext, message: str) -> str:
        """调用 MiniMax 通用对话"""
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
            return await self._call_chat(ctx, message)
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

