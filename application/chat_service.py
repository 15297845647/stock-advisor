"""对话编排 — 统一 LLM 对话（去掉意图分类层）

流程：
1. 快速关键词检测明确动作（关注/取消/自选/大盘）→ 直接执行
2. 推荐类请求走两阶段：LLM选股 → 拉实时数据 → LLM分析验证
3. 其余走单次 LLM 对话（注入市场数据 + 用户画像）
4. 解析 LLM 输出中的动作标记并执行副作用
"""

import logging
import re

from application.analysis_service import AnalysisService
from application.market_data_service import MarketDataService
from application.subscription_service import SubscriptionService
from domain.action_parser import extract_actions
from domain.models.user_context import UserContext
from domain.prompt_builder import _load_template
from infrastructure.minimax_client import MiniMaxClient
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)

# 关键词快速路由（不需要 LLM，秒响应）
_SUBSCRIBE_RE = re.compile(r"(?:关注|加自选|加入自选|订阅)\s*(\d{6})")
_UNSUBSCRIBE_RE = re.compile(r"(?:取消关注|删除自选|移除自选|退订)\s*(\d{6})")
_WATCHLIST_KW = {"自选股", "关注列表", "我的自选", "看看自选", "自选"}
_MARKET_KW = {"大盘", "市场概览", "三大指数", "大盘怎么样", "今天行情"}
_DEEP_RE = re.compile(r"(?:深度分析|详细分析|深入分析)\s*(\d{6})")
_DEEP_NAME_RE = re.compile(r"(?:深度分析|详细分析|深入分析)\s*([^\d\s]{2,6})")
_BACKTEST_KW = {"回测", "胜率", "历史准确率"}

# 推荐意图关键词（无具体代码 + 含这些词 → 走两阶段推荐）
_RECOMMEND_KW = {
    "推荐", "选股", "推几只", "推一只", "推个", "推一个", "来几只",
    "有什么好股", "买什么", "哪只好", "哪些值得", "帮我选", "换一批",
    "再推", "还有吗", "再来几个", "有没有好的",
}

# 从 LLM 输出中提取 [PICKS:...] 标记
_PICKS_RE = re.compile(r"\[PICKS:([\d,]+)\]")


class ChatService:
    def __init__(self):
        self.analysis = AnalysisService()
        self.subscription = SubscriptionService()
        self.minimax = MiniMaxClient()
        self.user_repo = UserRepository()
        self.market_data = MarketDataService()

    async def handle(self, wechat_id: str, message: str, ctx: UserContext) -> str:
        """主入口：快速路由 or LLM 对话"""
        await self.user_repo.append_chat(wechat_id, "user", message)

        # 快速路由：明确动作关键词直接执行，不走 LLM
        quick = await self._try_quick_route(wechat_id, message)
        if quick is not None:
            await self.user_repo.append_chat(wechat_id, "assistant", quick)
            return quick

        # 判断是否为推荐请求（无具体代码 + 含推荐关键词）
        try:
            if self._is_recommend_request(message):
                response = await self._two_stage_recommend(message, ctx)
            else:
                response = await self._unified_chat(wechat_id, message, ctx)
        except Exception as e:
            logger.exception("chat error")
            response = f"服务暂时不可用，请稍后再试。（{type(e).__name__}）"

        await self.user_repo.append_chat(wechat_id, "assistant", response)
        return response

    def _is_recommend_request(self, message: str) -> bool:
        """判断是否为推荐选股请求：无具体股票代码 + 含推荐关键词"""
        has_code = bool(re.search(r"(?<!\d)\d{6}(?!\d)", message))
        if has_code:
            return False
        return any(kw in message for kw in _RECOMMEND_KW)

    async def _two_stage_recommend(self, message: str, ctx: UserContext) -> str:
        """两阶段推荐：LLM选股 → 并行拉数据 → LLM基于数据分析"""
        # 阶段1：让 LLM 推荐候选代码
        pick_prompt = _load_template("pick_stocks.txt")
        profile = self.market_data._format_user_profile(ctx)

        pick_messages = [{"role": "user", "content": f"{profile}\n\n用户需求：{message}"}]
        raw_picks = await self.minimax.chat(
            system_prompt=pick_prompt,
            messages=pick_messages,
            max_tokens=500,
        )

        codes = self._parse_picks(raw_picks)
        if not codes:
            logger.warning("两阶段推荐：LLM未返回有效代码，降级普通对话")
            return await self._unified_chat_with_snapshot(message, ctx)

        logger.info("两阶段推荐：LLM候选 %s", codes)

        # 阶段2：并行拉取实时数据
        stock_data = await self.market_data.fetch_stocks_detail(codes)
        if not stock_data:
            logger.warning("两阶段推荐：实时数据全部拉取失败，降级普通对话")
            return await self._unified_chat_with_snapshot(message, ctx)

        # 阶段3：基于实时数据让 LLM 做最终分析
        system_prompt = _load_template("unified.txt")
        profile_text = self.market_data._format_user_profile(ctx)

        analysis_content = (
            f"{profile_text}\n\n"
            f"【候选股票实时数据】\n\n{stock_data}\n\n"
            f"【用户需求】\n{message}\n\n"
            f"请基于以上实时数据，从候选中筛选最值得推荐的 3-5 只，"
            f"淘汰技术面不佳或资金流出明显的。"
            f"给出每只的操作建议和理由。"
        )
        analysis_messages = [{"role": "user", "content": analysis_content}]

        raw = await self.minimax.chat(
            system_prompt=system_prompt,
            messages=analysis_messages,
        )

        clean_text, actions = extract_actions(raw)
        return clean_text

    @staticmethod
    def _parse_picks(raw: str) -> list[str]:
        """从 LLM 输出中提取 [PICKS:代码1,代码2,...] 中的代码列表"""
        m = _PICKS_RE.search(raw)
        if not m:
            return []
        codes = [c.strip() for c in m.group(1).split(",") if len(c.strip()) == 6]
        # 过滤非法代码
        return [c for c in codes if c[0] in "0136"]

    async def _unified_chat_with_snapshot(self, message: str, ctx: UserContext) -> str:
        """降级：注入 Top50 快照的普通推荐对话"""
        system_prompt = _load_template("unified.txt")
        market_context = await self.market_data.build_market_context(ctx, message)

        messages = []
        for msg in ctx.recent_chat[-6:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": f"{market_context}\n\n【用户消息】\n{message}"})

        raw = await self.minimax.chat(system_prompt=system_prompt, messages=messages)
        clean_text, _ = extract_actions(raw)
        return clean_text

    async def _try_quick_route(self, wechat_id: str, message: str) -> str | None:
        """关键词快速路由，匹配则直接返回结果，不匹配返回 None"""
        msg = message.strip()

        # 关注
        m = _SUBSCRIBE_RE.search(msg)
        if m:
            return await self.subscription.subscribe(wechat_id, m.group(1))

        # 取消关注
        m = _UNSUBSCRIBE_RE.search(msg)
        if m:
            return await self.subscription.unsubscribe(wechat_id, m.group(1))

        # 自选列表
        if any(kw in msg for kw in _WATCHLIST_KW):
            return await self.subscription.show_watchlist(wechat_id)

        # 大盘概览
        if any(kw in msg for kw in _MARKET_KW):
            return await self.analysis.get_market_overview()

        # 深度分析（代码）
        m = _DEEP_RE.search(msg)
        if m:
            return await self.analysis.analyze_stock_deep(m.group(1))

        # 深度分析（名称）
        m = _DEEP_NAME_RE.search(msg)
        if m:
            from infrastructure.akshare_client import AKShareClient
            code = await AKShareClient().resolve_stock_name(m.group(1))
            if code:
                return await self.analysis.analyze_stock_deep(code)
            return f"未找到「{m.group(1)}」对应的股票，请用6位代码重试。"

        # 回测
        if any(kw in msg for kw in _BACKTEST_KW):
            from application.backtest_service import BacktestService
            return await BacktestService().run_backtest(days=30)

        return None

    async def _unified_chat(self, wechat_id: str, message: str, ctx: UserContext) -> str:
        """单次 LLM 调用：注入市场上下文 + 用户画像，由 LLM 理解语意并回复"""
        system_prompt = _load_template("unified.txt")

        # 构建 user 消息：市场上下文 + 对话历史 + 当前问题
        market_context = await self.market_data.build_market_context(ctx, message)

        messages = []
        for msg in ctx.recent_chat[-6:]:
            messages.append({"role": msg.role, "content": msg.content})

        user_content = f"{market_context}\n\n【用户消息】\n{message}"
        messages.append({"role": "user", "content": user_content})

        raw = await self.minimax.chat(
            system_prompt=system_prompt,
            messages=messages,
        )

        # 解析动作标记并执行副作用
        clean_text, actions = extract_actions(raw)
        await self._execute_actions(wechat_id, actions)

        return clean_text

    async def _execute_actions(self, wechat_id: str, actions) -> None:
        """执行 LLM 输出中的动作标记"""
        for act in actions:
            try:
                match act.action:
                    case "SUBSCRIBE":
                        if act.code:
                            await self.subscription.subscribe(
                                wechat_id, act.code
                            )
                            logger.info("LLM触发加自选: %s %s", act.code, act.name)
                    case "UNSUBSCRIBE":
                        if act.code:
                            await self.subscription.unsubscribe(wechat_id, act.code)
                            logger.info("LLM触发取消自选: %s", act.code)
                    case "DEEP_ANALYZE":
                        pass  # 深度分析需要时间，不在当前回复中执行
            except Exception as e:
                logger.warning("执行动作 %s 失败: %s", act.action, e)
