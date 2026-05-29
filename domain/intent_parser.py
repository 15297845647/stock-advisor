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
    CLOSE_POSITION = auto()   # 卖出/平仓
    SHOW_POSITIONS = auto()   # 查看持仓
    CONFIRM = auto()          # 确认操作
    CANCEL = auto()           # 取消操作
    FREE_CHAT = auto()        # 自由对话/闲聊


@dataclass
class ParsedIntent:
    intent: Intent
    stock_code: str | None = None
    raw_text: str = ""
    price: float | None = None


# 6位数字股票代码
_CODE_RE = re.compile(r"\b(\d{6})\b")

# 3-5位短代码（需配合名称映射使用）
_SHORT_CODE_RE = re.compile(r"\b(\d{3,5})\b")

# 常见股票简称 → 6位代码映射（持续补充）
_STOCK_ALIAS: dict[str, str] = {
    "360": "601360", "三六零": "601360",
    "茅台": "600519", "贵州茅台": "600519",
    "平安": "601318", "中国平安": "601318",
    "宁德": "300750", "宁德时代": "300750",
    "比亚迪": "002594",
    "腾讯": "00700",
    "招商银行": "600036", "招行": "600036",
    "万科": "000002", "万科A": "000002",
    "中芯": "688981", "中芯国际": "688981",
    "隆基": "601012", "隆基绿能": "601012",
}

# 意图关键词映射
_SUBSCRIBE_KW = {"关注", "订阅", "加入", "添加", "跟踪", "加自选"}
_UNSUBSCRIBE_KW = {"取消关注", "取关", "删除", "移除", "不看了"}
_WATCHLIST_KW = {"关注列表", "自选股", "我的关注", "我关注了什么", "看看列表"}
_MARKET_KW = {"大盘", "上证", "沪深", "市场", "指数", "今天行情"}
_RECOMMEND_KW = {"推荐", "选股", "买什么", "热点股", "龙头", "强势股", "牛股", "推荐几只", "有什么好股"}
_ANALYZE_KW = {"分析", "看看", "怎么样", "走势", "技术面", "帮我看", "诊断", "研判"}
_CLOSE_POSITION_KW = {"卖出", "平仓", "清仓", "出了", "卖了", "减仓", "止盈", "止损"}
_SHOW_POSITION_KW = {"持仓", "我的仓位", "仓位", "持有什么", "我买了什么", "看看持仓", "当前持仓"}
_CONFIRM_KW = {"确认", "确定", "是的", "对的", "没错", "录入", "保存", "ok", "OK", "好的"}
_CANCEL_KW = {"取消", "算了", "不要了", "不录了", "放弃"}

# 匹配价格（仅用于 CLOSE_POSITION 提取卖出价）
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)")


def _extract_price(text: str) -> float | None:
    cleaned = _CODE_RE.sub("", text)
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

    # 确认 / 取消（短消息才匹配，避免长句误触）
    if len(text_lower) <= 10:
        if any(kw in text_lower for kw in _CANCEL_KW):
            return ParsedIntent(Intent.CANCEL, raw_text=text)
        if any(kw in text_lower for kw in _CONFIRM_KW):
            return ParsedIntent(Intent.CONFIRM, raw_text=text)

    # 查看持仓
    if any(kw in text_lower for kw in _SHOW_POSITION_KW):
        return ParsedIntent(Intent.SHOW_POSITIONS, raw_text=text)

    # 平仓/卖出（优先于建仓，因为"减仓"含"仓"）
    if any(kw in text_lower for kw in _CLOSE_POSITION_KW):
        code = _extract_code(text_lower)
        price = _extract_price(text_lower)
        return ParsedIntent(Intent.CLOSE_POSITION, stock_code=code, raw_text=text, price=price)

    # 分析个股 — 必须含分析关键词，纯代码不算（可能是在描述持仓等其他语境）
    has_analyze_kw = any(kw in text_lower for kw in _ANALYZE_KW)
    code = _extract_code(text_lower)
    if has_analyze_kw:
        return ParsedIntent(Intent.ANALYZE_STOCK, stock_code=code, raw_text=text)
    if code and len(text_lower) <= 10:
        # 短消息只含代码 → 视为分析请求
        return ParsedIntent(Intent.ANALYZE_STOCK, stock_code=code, raw_text=text)

    # 兜底：自由对话
    return ParsedIntent(Intent.FREE_CHAT, raw_text=text)


def _extract_code(text: str) -> str | None:
    # 优先精确 6 位代码
    match = _CODE_RE.search(text)
    if match:
        return match.group(1)

    # 尝试股票简称映射
    for alias, code in _STOCK_ALIAS.items():
        if alias in text:
            return code

    # 尝试 3-5 位短代码 → 查映射表
    short_match = _SHORT_CODE_RE.search(text)
    if short_match:
        short = short_match.group(1)
        if short in _STOCK_ALIAS:
            return _STOCK_ALIAS[short]

    return None
