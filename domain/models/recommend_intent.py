"""推荐意图 DTO — 从用户诉求解析出的结构化筛选条件

单一职责：数据类型定义。
"""

from dataclasses import dataclass, field


@dataclass
class RecommendIntent:
    """推荐意图 — 用于驱动筛选流水线"""

    # 交易风格
    style: str = "swing"                          # day/swing/position/long
    horizon_days: int = 5                         # 持有周期估算（天）

    # 硬性财务门槛
    max_pe: float | None = None                   # 市盈率上限
    max_pb: float | None = None                   # 市净率上限
    min_market_cap: float | None = None           # 最小总市值（元）
    max_market_cap: float | None = None
    min_roe: float | None = None                  # ROE 下限（%）
    min_revenue_growth: float | None = None       # 营收增长下限（%）

    # 硬性行情门槛
    min_turnover: float | None = None             # 换手率下限（%）
    min_volume_ratio: float | None = None         # 量比下限
    min_change_pct: float | None = None           # 当日涨幅下限
    max_change_pct: float | None = None           # 当日涨幅上限（防追高）
    min_amount: float = 5e7                       # 成交额下限（默认 5000万 排除死票）

    # 板块偏好
    sectors: list[str] = field(default_factory=list)    # 目标板块名（"半导体","AI"）
    require_hot_sector: bool = False                    # 是否要求当日热门板块
    sector_weight: float = 0.3                          # 板块热度打分权重

    # 排除
    exclude_st: bool = True                             # 排除 ST/退市/停牌
    blacklist_sectors: list[str] = field(default_factory=list)  # 用户禁忌板块
    blacklist_codes: list[str] = field(default_factory=list)    # 用户禁忌代码
    exclude_codes: list[str] = field(default_factory=list)      # 本次要排除（"再推一批"用）

    # 输出
    target_count: int = 5                          # 最终推荐数量
    candidate_cap: int = 100                       # Layer 2 硬筛输出上限

    # 原始诉求（透传，用于 LLM 裁决）
    raw_message: str = ""

    def to_dict(self) -> dict:
        return {
            "style": self.style,
            "horizon_days": self.horizon_days,
            "max_pe": self.max_pe,
            "max_pb": self.max_pb,
            "min_market_cap": self.min_market_cap,
            "min_roe": self.min_roe,
            "min_revenue_growth": self.min_revenue_growth,
            "min_turnover": self.min_turnover,
            "min_volume_ratio": self.min_volume_ratio,
            "min_amount": self.min_amount,
            "sectors": self.sectors,
            "require_hot_sector": self.require_hot_sector,
            "exclude_st": self.exclude_st,
            "blacklist_sectors": self.blacklist_sectors,
            "exclude_codes": self.exclude_codes,
            "target_count": self.target_count,
            "raw_message": self.raw_message,
        }
