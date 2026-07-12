"""候选股票 DTO — 推荐流水线各 Layer 之间传递

单一职责：数据模型。
"""

from dataclasses import dataclass, field


@dataclass
class Candidate:
    """候选股票 — 携带各 Layer 的评分和数据"""

    code: str
    name: str
    price: float = 0.0
    change_pct: float = 0.0
    turnover: float = 0.0                # 换手率
    volume_ratio: float = 0.0            # 量比
    amount: float = 0.0                  # 成交额（元）
    market_cap: float = 0.0              # 总市值
    pe_ratio: float = 0.0
    pb_ratio: float = 0.0

    # 板块信息
    sector: str = ""
    sector_score: float = 0.0            # 板块热度打分（0-100）

    # 技术评分（Layer 4 填充）
    trend_score: float = 0.0
    macd_score: float = 0.0
    rsi_score: float = 0.0
    support_score: float = 0.0
    fund_flow_score: float = 0.0
    tech_total: float = 0.0

    # 综合得分
    final_score: float = 0.0

    # 附加信息（供 LLM 裁决）
    kline_summary: str = ""              # 技术指标摘要文本
    fund_flow_summary: str = ""

    def to_summary(self) -> str:
        """生成一行摘要供 LLM 输入"""
        parts = [
            f"{self.name}({self.code})",
            f"价{self.price}",
            f"涨{self.change_pct:+.2f}%",
        ]
        if self.turnover:
            parts.append(f"换手{self.turnover:.1f}%")
        if self.volume_ratio:
            parts.append(f"量比{self.volume_ratio:.1f}")
        if self.sector:
            parts.append(f"板块={self.sector}(热{self.sector_score:.0f})")
        if self.tech_total:
            parts.append(f"技术分{self.tech_total:.0f}")
        return " ".join(parts)
