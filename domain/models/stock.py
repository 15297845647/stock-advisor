from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class StockQuote:
    """个股实时行情快照"""
    code: str
    name: str
    price: float
    change_pct: float
    volume: float
    amount: float
    high: float
    low: float
    open_price: float
    prev_close: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class StockDailyBar:
    """日K线数据"""
    code: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    change_pct: float


@dataclass
class FundFlow:
    """个股资金流向"""
    code: str
    trade_date: date
    main_net_inflow: float
    super_large_net: float
    large_net: float
    medium_net: float
    small_net: float


@dataclass
class StockNews:
    """个股新闻/公告"""
    title: str
    source: str
    time: str
    url: str
    news_type: str = "news"  # news / announcement


@dataclass
class StockDecision:
    """结构化投资决策"""
    action: str            # 买入 / 卖出 / 持有
    target_price: float    # 目标价
    stop_loss: float       # 止损价
    confidence: float      # 置信度 0-100
    risk_score: int        # 风险评分 1-10
    reasoning: str         # 核心理由
    key_points: list[str] = field(default_factory=list)
