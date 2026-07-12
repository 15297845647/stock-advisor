"""数据源类型 + 操作类型枚举 + 返回结构

用于多数据源降级链的类型定义，无任何逻辑。
单一职责：定义领域内的数据源相关类型。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DataSourceType(str, Enum):
    """具体数据源枚举 — 每个值对应一个物理接口"""

    # AKShare 系列
    AKSHARE_BID_ASK = "akshare.bid_ask"          # stock_bid_ask_em 五档报价
    AKSHARE_SPOT_EM = "akshare.spot_em"          # stock_zh_a_spot_em 东财全量
    AKSHARE_SPOT = "akshare.spot"                # stock_zh_a_spot 新浪全量
    AKSHARE_HIST = "akshare.hist"                # stock_zh_a_hist 日K
    AKSHARE_FUND_FLOW = "akshare.fund_flow"      # stock_individual_fund_flow
    AKSHARE_SECTOR_FLOW = "akshare.sector_flow"  # stock_sector_fund_flow_rank
    AKSHARE_NEWS = "akshare.news"                # stock_news_em

    # Tushare 系列
    TUSHARE_QUOTE = "tushare.quote"              # 实时行情
    TUSHARE_DAILY = "tushare.daily"              # 日线数据
    TUSHARE_MONEY_FLOW = "tushare.money_flow"    # 个股资金流


class DataOperation(str, Enum):
    """业务操作类型 — 一个操作可对应多个 DataSourceType（降级链）"""

    QUOTE = "quote"                # 实时行情
    KLINE = "kline"                # K线
    FUND_FLOW = "fund_flow"        # 个股资金流
    SECTOR_FLOW = "sector_flow"    # 板块资金流
    NEWS = "news"                  # 新闻


@dataclass
class FetchResult:
    """统一返回结构 — 记录哪个 source 成功、数据、耗时"""

    success: bool
    data: Any = None                          # 具体数据（quote/kline list/...）
    source: DataSourceType | None = None      # 命中的 source
    latency_ms: int = 0                       # 端到端耗时
    error: str | None = None                  # 全部失败时的最后错误
    fallback_count: int = 0                   # 经历的降级次数（0 = 首选命中）
    attempts: list[dict] = field(default_factory=list)  # 每次尝试详情（供调试）


@dataclass
class SourceHealth:
    """数据源健康状态快照 — 供 Admin 展示"""

    name: str
    source_type: DataSourceType
    enabled: bool
    healthy: bool                             # 综合状态：熔断未开且启用
    breaker_open: bool                        # 熔断器是否打开
    consecutive_failures: int
    cooldown_remaining_sec: int               # 熔断剩余秒数
    last_failure_at: datetime | None = None
    last_error: str | None = None
