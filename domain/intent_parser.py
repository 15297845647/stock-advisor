"""用户意图解析 — 从自然语言提取意图和参数"""

import re
from dataclasses import dataclass
from enum import Enum, auto


class Intent(Enum):
    ANALYZE_STOCK = auto()    # 分析某只股票
    SUBSCRIBE = auto()        # 关注/订阅
    UNSUBSCRIBE = auto()      # 取消关注
    SHOW_WATCHLIST = auto()   # 查看关注列表
    MARKET_OVERVIEW = auto()  # 大盘概览
    FREE_CHAT = auto()        # 自由对话/闲聊


@dataclass
class ParsedIntent:
    intent: Intent
    stock_code: str | None = None
    raw_text: str = ""


# 6位数字股票代码
_CODE_RE = re.compile(r"\b(\d{6})\b")

# 意图关键词映射
_SUBSCRIBE_KW = {"关注", "订阅", "加入", "添加", "跟踪", "加自选"}
_UNSUBSCRIBE_KW = {"取消关注", "取关", "删除", "移除", "不看了"}
_WATCHLIST_KW = {"关注列表", "自选股", "我的关注", "我关注了什么", "看看列表"}
_MARKET_KW = {"大盘", "上证", "沪深", "市场", "指数", "今天行情"}
_ANALYZE_KW = {"分析", "看看", "怎么样", "走势", "技术面", "帮我看", "诊断", "研判"}


def parse_intent(text: str) -> ParsedIntent:
    """解析用户消息意图"""
    text_lower = text.strip()

    # 取消关注（优先匹配，因为包含"关注"）
    if any(kw in text_lower for kw in _UNSUBSCRIBE_KW):
        code = _extract_code(text_lower)
        return ParsedIntent(Intent.UNSUBSCRIBE, stock_code=code, raw_text=text)

    # 关注
    if any(kw in text_lower for kw in _SUBSCRIBE_KW):
        code = _extract_code(text_lower)
        return ParsedIntent(Intent.SUBSCRIBE, stock_code=code, raw_text=text)

    # 查看关注列表
    if any(kw in text_lower for kw in _WATCHLIST_KW):
        return ParsedIntent(Intent.SHOW_WATCHLIST, raw_text=text)

    # 大盘概览
    if any(kw in text_lower for kw in _MARKET_KW):
        return ParsedIntent(Intent.MARKET_OVERVIEW, raw_text=text)

    # 分析个股（含代码 or 分析关键词+代码）
    code = _extract_code(text_lower)
    if code:
        return ParsedIntent(Intent.ANALYZE_STOCK, stock_code=code, raw_text=text)

    if any(kw in text_lower for kw in _ANALYZE_KW):
        return ParsedIntent(Intent.ANALYZE_STOCK, stock_code=None, raw_text=text)

    # 兜底：自由对话
    return ParsedIntent(Intent.FREE_CHAT, raw_text=text)


def _extract_code(text: str) -> str | None:
    match = _CODE_RE.search(text)
    return match.group(1) if match else None
