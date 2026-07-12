"""推荐结果 DTO

单一职责：领域模型。
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Recommendation:
    """单条推荐结果"""

    code: str
    name: str
    price: float                        # 推荐时价格
    target_price: float                 # 目标价
    stop_loss: float                    # 止损价
    risk_score: int                     # 风险 1-10
    reason: str                         # 推荐理由
    tech_summary: str = ""              # 技术摘要
    fund_flow_summary: str = ""         # 资金摘要
    adjusted: bool = False              # 是否被规则校准
    recommended_at: datetime = field(default_factory=datetime.now)
