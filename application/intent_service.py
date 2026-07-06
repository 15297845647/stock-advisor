"""LLM 意图理解 — 用大模型把自然语言解析为结构化意图 + 参数

替代纯关键词匹配，能理解同义/口语/上下文（如"再推两个""换一批便宜白马"）。
解析失败时降级到关键词规则 parse_intent，保证鲁棒。
"""

import json
import logging
import re

from domain.intent_parser import Intent, ParsedIntent, _extract_code, parse_intent
from domain.models.user_context import ChatMessage
from infrastructure.minimax_client import MiniMaxClient

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# 供 LLM 选择的意图集合（与 Intent 枚举对应）
_INTENT_SYSTEM_PROMPT = """你是股票助手的意图识别器。把用户消息解析成 JSON，只输出 JSON，不要任何多余文字或解释。

可选 intent（必须从中选一个）：
- RECOMMEND: 让你推荐股票/选股/再来一批/换一批
- ANALYZE_STOCK: 分析某只具体股票（普通分析）
- ANALYZE_STOCK_DEEP: 深度分析/详细分析某只股票
- ANALYZE_FUTURES: 分析期货品种（螺纹、原油、黄金、欧线等）
- SUBSCRIBE: 关注/加自选某只股票
- UNSUBSCRIBE: 取消关注某只股票
- SHOW_WATCHLIST: 查看关注列表/自选股
- MARKET_OVERVIEW: 看大盘/市场行情概览
- SCREEN_STOCKS: 按预设策略条件筛选（金叉选股、超跌反弹、强势突破、均线多头）
- BACKTEST: 回测/看历史建议准不准/胜率
- FREE_CHAT: 其他闲聊、提问、无法归入以上的

输出格式（严格 JSON）：
{"intent":"意图名","stock_code":"6位代码或null","count":数字或null}

规则：
- 股票简称要转成6位A股代码（茅台→600519，宁德时代→300750，比亚迪→002594），不确定就填 null
- count 仅当用户明确提到数量时填（"再推两个"→2，"推荐3只"→3），否则 null
- 结合上下文理解：若上一轮在推荐股票，用户说"再来几个/换一批"应识别为 RECOMMEND
- 只输出 JSON"""


class IntentService:
    def __init__(self):
        self.minimax = MiniMaxClient()

    async def parse(self, message: str, recent_chat: list[ChatMessage] | None = None) -> ParsedIntent:
        """LLM 解析意图，失败降级关键词规则"""
        try:
            data = await self._llm_classify(message, recent_chat or [])
            parsed = self._to_parsed_intent(data, message)
            if parsed:
                return parsed
        except Exception as e:
            logger.warning("LLM 意图解析失败，降级关键词规则: %s", e)

        return parse_intent(message)

    async def _llm_classify(self, message: str, recent_chat: list[ChatMessage]) -> dict:
        """调用 LLM 返回意图 JSON"""
        messages = []
        # 带少量上下文，帮助理解"再推两个"这类承接语义
        for msg in recent_chat[-4:]:
            messages.append({"role": msg.role, "content": msg.content})
        messages.append({"role": "user", "content": message})

        raw = await self.minimax.chat(
            system_prompt=_INTENT_SYSTEM_PROMPT, messages=messages, max_tokens=200,
        )
        match = _JSON_RE.search(raw)
        if not match:
            raise ValueError(f"未找到 JSON: {raw[:120]}")
        return json.loads(match.group(0))

    @staticmethod
    def _to_parsed_intent(data: dict, message: str) -> ParsedIntent | None:
        """把 LLM JSON 映射为 ParsedIntent，非法意图返回 None 触发降级"""
        name = str(data.get("intent", "")).strip().upper()
        try:
            intent = Intent[name]
        except KeyError:
            logger.warning("LLM 返回未知意图: %s", name)
            return None

        # 代码：LLM 优先，缺失则用关键词提取兜底
        code = data.get("stock_code")
        code = str(code) if code and str(code).isdigit() and len(str(code)) == 6 else None
        if not code:
            code = _extract_code(message)

        count = data.get("count")
        count = int(count) if isinstance(count, (int, float)) and 1 <= int(count) <= 20 else None

        return ParsedIntent(intent=intent, stock_code=code, raw_text=message, count=count)
