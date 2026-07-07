"""对话编排 — 统一 LLM 对话（去掉意图分类层）

流程：
1. 快速关键词检测明确动作（关注/取消/自选/大盘）→ 直接执行
2. 其余全部走单次 LLM 对话（注入市场数据 + 用户画像）
3. 解析 LLM 输出中的动作标记并执行副作用
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
_BACKTEST_KW = {"回测", "胜率", "历史准确率"}


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

        # 统一 LLM 对话
        try:
            response = await self._unified_chat(wechat_id, message, ctx)
        except Exception as e:
            logger.exception("unified_chat error")
            response = f"服务暂时不可用，请稍后再试。（{type(e).__name__}）"

        await self.user_repo.append_chat(wechat_id, "assistant", response)
        return response

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

        # 深度分析
        m = _DEEP_RE.search(msg)
        if m:
            return await self.analysis.analyze_stock_deep(m.group(1))

        # 回测
        if any(kw in msg for kw in _BACKTEST_KW):
            from application.backtest_service import BacktestService
            return await BacktestService().run_backtest(days=30)

        return None

    async def _unified_chat(self, wechat_id: str, message: str, ctx: UserContext) -> str:
        """单次 LLM 调用：注入市场上下文 + 用户画像，由 LLM 理解语意并回复"""
        system_prompt = _load_template("unified.txt")

        # 构建 user 消息：市场上下文 + 对话历史 + 当前问题
        market_context = await self.market_data.build_market_context(ctx)

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
