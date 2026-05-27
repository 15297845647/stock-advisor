"""对话编排 — 意图识别 → 路由到对应处理 → 返回结果"""

import logging

from application.analysis_service import AnalysisService
from application.subscription_service import SubscriptionService
from domain.intent_parser import Intent, parse_intent
from domain.models.user_context import UserContext
from domain.prompt_builder import build_chat_prompt, build_system_prompt
from infrastructure.minimax_client import MiniMaxClient
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self):
        self.analysis = AnalysisService()
        self.subscription = SubscriptionService()
        self.minimax = MiniMaxClient()
        self.user_repo = UserRepository()

    async def handle(self, wechat_id: str, message: str, ctx: UserContext) -> str:
        """处理一条用户消息，返回回复文本"""

        # 记录用户消息
        await self.user_repo.append_chat(wechat_id, "user", message)

        parsed = parse_intent(message)
        logger.info("用户 %s 意图: %s, 代码: %s", wechat_id, parsed.intent, parsed.stock_code)

        response = await self._dispatch(wechat_id, parsed, ctx, message)

        # 记录助手回复
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

            case Intent.FREE_CHAT:
                return await self._handle_free_chat(ctx, message)

    async def _handle_analyze(self, stock_code: str | None, ctx: UserContext, message: str) -> str:
        if not stock_code:
            # 没提取到代码，用MiniMax理解用户说的是哪只股票
            return await self._handle_free_chat(ctx, message)
        return await self.analysis.analyze_stock(stock_code)

    async def _handle_free_chat(self, ctx: UserContext, message: str) -> str:
        """自由对话 — 将完整上下文注入MiniMax"""
        system = build_system_prompt()
        user_prompt = build_chat_prompt(ctx, message)

        # 构造消息列表：历史对话 + 当前问题（含上下文）
        messages = []
        for msg in ctx.recent_chat:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": user_prompt})

        return await self.minimax.chat(system_prompt=system, messages=messages)
