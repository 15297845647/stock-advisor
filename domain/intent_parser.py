"""用户意图解析 — 从自然语言提取意图和参数"""

import re
from dataclasses import dataclass
from enum import Enum, auto


class Intent(Enum):
    ANALYZE_STOCK = auto()    # 分析某只股票
    ANALYZE_STOCK_DEEP = auto() # 深度分析（多空辩论）
    ANALYZE_FUTURES = auto()  # 分析期货品种
    SUBSCRIBE = auto()        # 关注/订阅
    UNSUBSCRIBE = auto()      # 取消关注
    SHOW_WATCHLIST = auto()   # 查看关注列表
    MARKET_OVERVIEW = auto()  # 大盘概览
    RECOMMEND = auto()        # 推荐/选股
    SCREEN_STOCKS = auto()    # 条件筛选
    BACKTEST = auto()         # 回测历史建议
    FREE_CHAT = auto()        # 自由对话/闲聊


@dataclass
class ParsedIntent:
    intent: Intent
    stock_code: str | None = None
    raw_text: str = ""
    price: float | None = None
    count: int | None = None   # 期望条数（如"再推两个"）


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
_SCREEN_KW = {"筛选", "选出", "筛股", "金叉选股", "超跌反弹", "强势突破", "均线多头", "条件选股"}
_BACKTEST_KW = {"回测", "准确率", "历史验证", "建议准不准", "胜率", "模型效果"}
_ANALYZE_KW = {"分析", "看看", "怎么样", "走势", "技术面", "帮我看", "诊断", "研判"}
_DEEP_ANALYZE_KW = {"深度分析", "详细分析", "深入分析", "全面分析", "多空分析", "辩论分析"}
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

    # 回测
    if any(kw in text_lower for kw in _BACKTEST_KW):
        return ParsedIntent(Intent.BACKTEST, raw_text=text)

    # 条件筛选（优先于普通推荐）
    if any(kw in text_lower for kw in _SCREEN_KW):
        return ParsedIntent(Intent.SCREEN_STOCKS, raw_text=text)

    # 推荐选股
    if any(kw in text_lower for kw in _RECOMMEND_KW):
        return ParsedIntent(Intent.RECOMMEND, raw_text=text)

    # 期货分析
    is_futures = any(kw in text_lower for kw in _FUTURES_KW)
    has_futures_name = any(fn in text_lower for fn in _FUTURES_NAMES)
    if is_futures or has_futures_name:
        return ParsedIntent(Intent.ANALYZE_FUTURES, raw_text=text)

    # 深度分析（多空辩论）
    has_deep_kw = any(kw in text_lower for kw in _DEEP_ANALYZE_KW)
    code = _extract_code(text_lower)
    if has_deep_kw:
        return ParsedIntent(Intent.ANALYZE_STOCK_DEEP, stock_code=code, raw_text=text)

    # 普通分析
    has_analyze_kw = any(kw in text_lower for kw in _ANALYZE_KW)
    if has_analyze_kw:
        return ParsedIntent(Intent.ANALYZE_STOCK, stock_code=code, raw_text=text)
    if code and len(text_lower) <= 10:
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
