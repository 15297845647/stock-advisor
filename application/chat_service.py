"""对话编排 — 统一 LLM 对话（去掉意图分类层）

流程：
1. 快速关键词检测明确动作（大盘/分析/期货）→ 直接执行
2. 推荐类请求走两阶段：LLM选股 → 拉实时数据 → LLM分析验证
3. 其余走单次 LLM 对话（注入市场数据 + 用户画像）
4. 解析 LLM 输出中的动作标记并执行副作用
"""

import logging
import re

from application.analysis_service import AnalysisService
from application.futures_service import FuturesAnalysisService
from application.market_data_service import MarketDataService
from application.recommend_service import RecommendService
from domain.action_parser import extract_actions
from domain.models.user_context import UserContext
from domain.prompt_builder import _load_template
from infrastructure.minimax_client import MiniMaxClient
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)

# 关键词快速路由（不需要 LLM，秒响应）
_MARKET_KW = {"大盘", "市场概览", "三大指数", "大盘怎么样", "今天行情"}
_DEEP_RE = re.compile(r"(?:深度分析|详细分析|深入分析)\s*(\d{6})")
_DEEP_NAME_RE = re.compile(r"(?:深度分析|详细分析|深入分析)\s*([^\d\s]{2,6})")
_QUICK_RE = re.compile(r"(?:快速分析|简版分析|快速看)\s*(\d{6})")
_QUICK_NAME_RE = re.compile(r"(?:快速分析|简版分析|快速看)\s*([^\d\s]{2,6})")
_BACKTEST_KW = {"回测", "胜率", "历史准确率"}

# 期货品种关键词 → 走 FuturesAnalysisService
_FUTURES_KW = {"期货", "合约", "主力合约", "连续合约"}
_FUTURES_NAMES = {
    "欧线", "集运", "欧线集运", "集运指数",
    "螺纹", "螺纹钢", "铁矿", "铁矿石",
    "原油", "黄金", "白银", "铜", "沪铜",
    "豆粕", "棕榈油", "焦煤", "焦炭",
    "甲醇", "PTA", "pta", "纯碱", "玻璃",
    "橡胶", "沥青", "乙二醇", "豆油", "菜油",
    "苹果", "生猪", "锌", "镍", "锡", "铝",
    "沪深300", "上证50", "中证500", "中证1000",
    "国债", "十年国债", "燃油", "低硫燃油",
    "不锈钢", "花生", "尿素", "棉花", "白糖", "菜粕",
}

# 推荐意图关键词（无具体代码 + 含这些词 → 走两阶段推荐）
_RECOMMEND_KW = {
    "推荐", "选股", "推几只", "推一只", "推个", "推一个", "来几只",
    "有什么好股", "买什么", "哪只好", "哪些值得", "帮我选", "换一批",
    "再推", "还有吗", "再来几个", "有没有好的",
}

# 从 LLM 输出中提取 [PICKS:...] 标记（兼容代码间有空格）
_PICKS_RE = re.compile(r"\[PICKS:([\d,\s]+)\]")


class ChatService:
    def __init__(self):
        self.analysis = AnalysisService()
        self.futures = FuturesAnalysisService()
        self.minimax = MiniMaxClient()
        self.user_repo = UserRepository()
        self.market_data = MarketDataService()
        self.recommend_svc = RecommendService()

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
                import asyncio
                try:
                    response = await asyncio.wait_for(
                        self._two_stage_recommend(message, ctx), timeout=90
                    )
                except asyncio.TimeoutError:
                    logger.warning("两阶段推荐超时，降级快速推荐")
                    response = await self._unified_chat_with_snapshot(message, ctx)
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
        """
        推荐流程（P2 改造版）：
        规则漏斗 → LLM 裁决 → 决策校准 → 落库
        委托给 RecommendService 编排。
        """
        try:
            recs, summary = await self.recommend_svc.recommend(message, ctx)
        except Exception as e:
            logger.exception("推荐流程失败，降级快照对话")
            return await self._unified_chat_with_snapshot(message, ctx)

        if not recs:
            # 无推荐时降级快照对话给用户一个响应
            logger.warning("推荐服务无输出，降级快照对话")
            fallback = await self._unified_chat_with_snapshot(message, ctx)
            return summary + "\n\n" + fallback if summary else fallback

        return self.recommend_svc.format_response(recs, summary)

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

        # 大盘概览
        if any(kw in msg for kw in _MARKET_KW):
            return await self.analysis.get_market_overview()

        # 深度分析（代码）
        m = _DEEP_RE.search(msg)
        if m:
            from domain.models.research_depth import ResearchDepth
            return await self.analysis.analyze_stock_deep(
                m.group(1), depth=ResearchDepth.DEEP, wechat_id=wechat_id,
            )

        # 深度分析（名称）
        m = _DEEP_NAME_RE.search(msg)
        if m:
            from domain.models.research_depth import ResearchDepth
            from infrastructure.akshare_client import AKShareClient
            code = await AKShareClient().resolve_stock_name(m.group(1))
            if code:
                return await self.analysis.analyze_stock_deep(
                    code, depth=ResearchDepth.DEEP, wechat_id=wechat_id,
                )
            return f"未找到「{m.group(1)}」对应的股票，请用6位代码重试。"

        # 快速分析（代码）
        m = _QUICK_RE.search(msg)
        if m:
            from domain.models.research_depth import ResearchDepth
            return await self.analysis.analyze_stock_deep(
                m.group(1), depth=ResearchDepth.QUICK, wechat_id=wechat_id,
            )

        # 快速分析（名称）
        m = _QUICK_NAME_RE.search(msg)
        if m:
            from domain.models.research_depth import ResearchDepth
            from infrastructure.akshare_client import AKShareClient
            code = await AKShareClient().resolve_stock_name(m.group(1))
            if code:
                return await self.analysis.analyze_stock_deep(
                    code, depth=ResearchDepth.QUICK, wechat_id=wechat_id,
                )

        # 期货分析
        if self._is_futures_request(msg):
            return await self.futures.analyze(msg)

        # 回测
        if any(kw in msg for kw in _BACKTEST_KW):
            from application.backtest_service import BacktestService
            return await BacktestService().run_backtest(days=30)

        return None

    @staticmethod
    def _is_futures_request(text: str) -> bool:
        """判断是否为期货分析请求"""
        has_kw = any(kw in text for kw in _FUTURES_KW)
        has_name = any(fn in text for fn in _FUTURES_NAMES)
        return has_kw or has_name

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
                    case "DEEP_ANALYZE":
                        pass  # 深度分析需要时间，不在当前回复中执行
            except Exception as e:
                logger.warning("执行动作 %s 失败: %s", act.action, e)
