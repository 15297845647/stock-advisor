"""对话编排 — 意图识别 → 路由到对应处理 → 返回结果

持仓录入走 LLM 语境检测 → 用户确认 → 写入，不直接存储。
"""

import logging

from application.analysis_service import AnalysisService
from application.position_service import PositionService
from application.subscription_service import SubscriptionService
from domain.intent_parser import Intent, parse_intent
from domain.models.user_context import UserContext
from domain.prompt_builder import build_chat_prompt, build_recommend_prompt, build_system_prompt
from infrastructure.akshare_client import AKShareClient
from infrastructure.minimax_client import MiniMaxClient
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self):
        self.analysis = AnalysisService()
        self.subscription = SubscriptionService()
        self.position = PositionService()
        self.minimax = MiniMaxClient()
        self.user_repo = UserRepository()
        self.akshare = AKShareClient()

    async def handle(self, wechat_id: str, message: str, ctx: UserContext) -> str:
        await self.user_repo.append_chat(wechat_id, "user", message)

        parsed = parse_intent(message)
        logger.info("用户 %s 意图: %s, 代码: %s", wechat_id, parsed.intent, parsed.stock_code)

        response = await self._dispatch(wechat_id, parsed, ctx, message)

        await self.user_repo.append_chat(wechat_id, "assistant", response)
        return response

    async def _dispatch(self, wechat_id, parsed, ctx, message) -> str:
        # 优先处理确认/取消（有 pending 状态时）
        if parsed.intent == Intent.CONFIRM:
            result = await self.position.confirm_pending(wechat_id)
            if result:
                return result
            # 没有 pending，当自由对话处理
            return await self._handle_free_chat(ctx, message)

        if parsed.intent == Intent.CANCEL:
            result = self.position.cancel_pending(wechat_id)
            if result:
                return result
            return await self._handle_free_chat(ctx, message)

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

            case Intent.RECOMMEND:
                return await self._handle_recommend(ctx)

            case Intent.ADD_POSITION:
                return await self._handle_position_detect(wechat_id, message)

            case Intent.CLOSE_POSITION:
                return await self.position.close_position(
                    wechat_id, parsed.stock_code, parsed.price)

            case Intent.SHOW_POSITIONS:
                return await self.position.show_positions(wechat_id)

            case Intent.FREE_CHAT:
                return await self._handle_free_chat_with_position_detect(wechat_id, ctx, message)

    # ── 持仓语境检测（用 LLM）──

    async def _handle_position_detect(self, wechat_id: str, message: str) -> str:
        """关键词触发的建仓 → 走 LLM 提取 + 确认"""
        result = await self.position.detect_position_context(wechat_id, message)
        if result:
            return result
        return "没有识别到有效的持仓信息。请告诉我股票名称、数量和成本价，如「我有茅台500股，成本1800」。"

    async def _handle_free_chat_with_position_detect(
        self, wechat_id: str, ctx: UserContext, message: str
    ) -> str:
        """自由对话也检测持仓语境 — 命中则走确认流程，否则正常聊天"""
        # 快速判断：消息中含有数字+股票相关词汇才尝试检测
        if self._might_contain_position(message):
            result = await self.position.detect_position_context(wechat_id, message)
            if result:
                return result

        return await self._handle_free_chat(ctx, message)

    @staticmethod
    def _might_contain_position(text: str) -> bool:
        """快速预判消息是否可能含持仓信息（避免每条消息都调 LLM）"""
        import re
        has_number = bool(re.search(r"\d", text))
        if not has_number:
            return False
        position_hints = {"股", "手", "仓", "成本", "均价", "买", "持有", "有"}
        return any(h in text for h in position_hints)

    # ── 原有处理 ──

    async def _handle_analyze(self, stock_code: str | None, ctx: UserContext, message: str) -> str:
        if not stock_code:
            return await self._handle_free_chat(ctx, message)
        return await self.analysis.analyze_stock(stock_code)

    async def _handle_recommend(self, ctx: UserContext) -> str:
        rank_data = await self.akshare.get_stock_rank_list(count=20)
        sector_data = await self.akshare.get_sector_fund_flow(count=10)

        if not rank_data and not sector_data:
            return "暂时无法获取行情数据，请稍后再试。"

        system = build_system_prompt()
        user_prompt = build_recommend_prompt(ctx, rank_data, sector_data)
        messages = [{"role": "user", "content": user_prompt}]
        return await self.minimax.chat(system_prompt=system, messages=messages)

    async def _handle_free_chat(self, ctx: UserContext, message: str) -> str:
        system = build_system_prompt()
        user_prompt = build_chat_prompt(ctx, message)

        messages = []
        for msg in ctx.recent_chat:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_prompt})

        return await self.minimax.chat(system_prompt=system, messages=messages)
