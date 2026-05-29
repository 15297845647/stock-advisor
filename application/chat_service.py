"""对话编排 — 意图识别 → 路由 → 返回结果

持仓录入完全依赖 LLM 语境理解：MiniMax 在对话响应中自动标记
[POSITION] 数据 → 解析 → 询问用户确认 → 写入。
"""

import json
import logging
import re

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

    async def handle(self, wechat_id: str, message: str, ctx: UserContext) -> str:
        await self.user_repo.append_chat(wechat_id, "user", message)

        parsed = parse_intent(message)
        logger.info("用户 %s 意图: %s, 代码: %s", wechat_id, parsed.intent, parsed.stock_code)

        response = await self._dispatch(wechat_id, parsed, ctx, message)

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

            case Intent.RECOMMEND:
                return await self._handle_recommend(ctx)

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
        if not position_data:
            return response

        # 剥离标记，保留正常对话内容
        clean_response = _POSITION_TAG_RE.sub("", response).strip()

        # 验证股票代码并存入 pending
        confirm_text = await self.position.store_pending_from_llm(wechat_id, position_data)
        if not confirm_text:
            return clean_response

        # 拼接：正常回复 + 确认提示
        return f"{clean_response}\n\n{confirm_text}"

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

    async def _handle_recommend(self, ctx: UserContext) -> str:
        rank_data = await self.akshare.get_stock_rank_list(count=20)
        sector_data = await self.akshare.get_sector_fund_flow(count=10)

        if not rank_data and not sector_data:
            return "暂时无法获取行情数据，请稍后再试。"

        system = build_system_prompt()
        user_prompt = build_recommend_prompt(ctx, rank_data, sector_data)
        messages = [{"role": "user", "content": user_prompt}]
        return await self.minimax.chat(system_prompt=system, messages=messages)
