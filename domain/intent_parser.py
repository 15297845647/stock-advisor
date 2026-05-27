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
    RECOMMEND = auto()        # 推荐/选股
    ADD_POSITION = auto()     # 买入/建仓
    CLOSE_POSITION = auto()   # 卖出/平仓
    SHOW_POSITIONS = auto()   # 查看持仓
    FREE_CHAT = auto()        # 自由对话/闲聊


@dataclass
class ParsedIntent:
    intent: Intent
    stock_code: str | None = None
    raw_text: str = ""
    shares: int | None = None
    price: float | None = None


# 6位数字股票代码
_CODE_RE = re.compile(r"\b(\d{6})\b")

# 意图关键词映射
_SUBSCRIBE_KW = {"关注", "订阅", "加入", "添加", "跟踪", "加自选"}
_UNSUBSCRIBE_KW = {"取消关注", "取关", "删除", "移除", "不看了"}
_WATCHLIST_KW = {"关注列表", "自选股", "我的关注", "我关注了什么", "看看列表"}
_MARKET_KW = {"大盘", "上证", "沪深", "市场", "指数", "今天行情"}
_RECOMMEND_KW = {"推荐", "选股", "买什么", "热点股", "龙头", "强势股", "牛股", "推荐几只", "有什么好股"}
_ANALYZE_KW = {"分析", "看看", "怎么样", "走势", "技术面", "帮我看", "诊断", "研判"}
_ADD_POSITION_KW = {"买入", "建仓", "加仓", "持有", "买了", "入了"}
_CLOSE_POSITION_KW = {"卖出", "平仓", "清仓", "出了", "卖了", "减仓", "止盈", "止损"}
_SHOW_POSITION_KW = {"持仓", "我的仓位", "仓位", "持有什么", "我买了什么", "看看持仓", "当前持仓"}

# 匹配数量：100股 / 1000手 / 100份
_SHARES_RE = re.compile(r"(\d+)\s*(?:股|手|份)")
# 匹配价格：成本18.5 / 价格18.5 / 18.5元 / 均价18.5
_PRICE_RE = re.compile(r"(?:成本|价格|均价|买入价)?(\d+(?:\.\d+)?)\s*(?:元|块)?")


def _extract_shares(text: str) -> int | None:
    m = _SHARES_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_price(text: str) -> float | None:
    """从文本中提取价格（排除股票代码和数量干扰）"""
    cleaned = _CODE_RE.sub("", text)
    cleaned = _SHARES_RE.sub("", cleaned)
    m = _PRICE_RE.search(cleaned)
    if m:
        val = float(m.group(1))
        if 0.01 < val < 100000:
            return val
    return None


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

    # 推荐选股
    if any(kw in text_lower for kw in _RECOMMEND_KW):
        return ParsedIntent(Intent.RECOMMEND, raw_text=text)

    # 查看持仓
    if any(kw in text_lower for kw in _SHOW_POSITION_KW):
        return ParsedIntent(Intent.SHOW_POSITIONS, raw_text=text)

    # 平仓/卖出（优先于建仓，因为"减仓"含"仓"）
    if any(kw in text_lower for kw in _CLOSE_POSITION_KW):
        code = _extract_code(text_lower)
        price = _extract_price(text_lower)
        return ParsedIntent(Intent.CLOSE_POSITION, stock_code=code, raw_text=text, price=price)

    # 建仓/买入
    if any(kw in text_lower for kw in _ADD_POSITION_KW):
        code = _extract_code(text_lower)
        shares = _extract_shares(text_lower)
        price = _extract_price(text_lower)
        return ParsedIntent(Intent.ADD_POSITION, stock_code=code, raw_text=text,
                            shares=shares, price=price)

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
