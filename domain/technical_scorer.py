"""技术评分器 — 给候选打技术分（趋势/MACD/RSI/支撑距离/资金流）

单一职责：纯计算，无 IO。数据由上层拉好传入。
"""

import logging

from domain.models.candidate import Candidate
from domain.models.stock import FundFlow, StockDailyBar
from domain.stock_analyzer import TechnicalSnapshot, analyze_technical

logger = logging.getLogger(__name__)


class TechnicalScorer:
    """5 维技术评分"""

    # 各维度权重（总和 = 1）
    WEIGHTS = {
        "trend": 0.30,
        "macd": 0.20,
        "rsi": 0.20,
        "support": 0.15,
        "fund_flow": 0.15,
    }

    def score(
        self, candidate: Candidate,
        bars: list[StockDailyBar],
        flows: list[FundFlow],
    ) -> Candidate:
        """
        给候选打技术分，就地更新 Candidate 字段。
        任一数据缺失时对应维度得 0 分。
        """
        tech = analyze_technical(bars) if bars else None

        candidate.trend_score = self._trend_score(tech)
        candidate.macd_score = self._macd_score(tech)
        candidate.rsi_score = self._rsi_score(tech)
        candidate.support_score = self._support_score(candidate.price, tech)
        candidate.fund_flow_score = self._fund_flow_score(flows)

        candidate.tech_total = self._compose_total(candidate)
        candidate.kline_summary = self._summarize_tech(tech)
        candidate.fund_flow_summary = self._summarize_flows(flows)
        return candidate

    # ── 各维度打分 ──

    @staticmethod
    def _trend_score(tech: TechnicalSnapshot | None) -> float:
        """MA5>MA10>MA20 均线多头满分，反排空头 0 分"""
        if tech is None:
            return 0.0

        if tech.ma5 > tech.ma10 > tech.ma20:
            return 100.0
        if tech.ma5 > tech.ma20:
            return 70.0
        if tech.ma10 > tech.ma20:
            return 50.0
        if tech.ma5 < tech.ma10 < tech.ma20:
            return 10.0
        return 30.0

    @staticmethod
    def _macd_score(tech: TechnicalSnapshot | None) -> float:
        """DIF>DEA 且柱值放大得高分"""
        if tech is None:
            return 0.0
        if tech.macd > tech.macd_signal and tech.macd_hist > 0:
            return 100.0
        if tech.macd_hist > 0:
            return 70.0
        if tech.macd > tech.macd_signal:
            return 60.0
        return 20.0

    @staticmethod
    def _rsi_score(tech: TechnicalSnapshot | None) -> float:
        """RSI 40-60 得满分，>80 或 <20 惩罚"""
        if tech is None:
            return 0.0

        rsi = tech.rsi_14
        if 40 <= rsi <= 60:
            return 100.0
        if 30 <= rsi < 40 or 60 < rsi <= 70:
            return 80.0
        if 20 <= rsi < 30 or 70 < rsi <= 80:
            return 50.0
        return 20.0

    @staticmethod
    def _support_score(price: float, tech: TechnicalSnapshot | None) -> float:
        """距离支撑越近得分越高（表示回踩支撑，反弹机会大）"""
        if tech is None or not tech.support or tech.support <= 0:
            return 0.0

        distance_pct = (price - tech.support) / price
        if distance_pct < 0.03:
            return 100.0
        if distance_pct < 0.05:
            return 70.0
        if distance_pct < 0.10:
            return 40.0
        return 20.0

    @staticmethod
    def _fund_flow_score(flows: list[FundFlow]) -> float:
        """近3日主力净流入越多分越高"""
        if not flows:
            return 0.0

        recent = flows[-3:]
        total = sum(f.main_net_inflow for f in recent)

        if total > 5e7:      # > 5000万
            return 100.0
        if total > 1e7:
            return 80.0
        if total > 0:
            return 60.0
        if total > -1e7:
            return 40.0
        return 10.0

    def _compose_total(self, c: Candidate) -> float:
        """按权重加权求和"""
        return round(
            c.trend_score * self.WEIGHTS["trend"]
            + c.macd_score * self.WEIGHTS["macd"]
            + c.rsi_score * self.WEIGHTS["rsi"]
            + c.support_score * self.WEIGHTS["support"]
            + c.fund_flow_score * self.WEIGHTS["fund_flow"],
            1,
        )

    # ── 摘要生成 ──

    @staticmethod
    def _summarize_tech(tech: TechnicalSnapshot | None) -> str:
        """技术摘要文本"""
        if tech is None:
            return "技术数据不可用"
        return (
            f"趋势={tech.trend} MA5={tech.ma5}/MA10={tech.ma10}/MA20={tech.ma20} "
            f"MACD柱={tech.macd_hist:+.2f} RSI={tech.rsi_14:.0f} "
            f"支撑{tech.support}/压力{tech.resistance}"
        )

    @staticmethod
    def _summarize_flows(flows: list[FundFlow]) -> str:
        """资金流摘要"""
        if not flows:
            return "资金流数据不可用"

        recent = flows[-3:]
        total = sum(f.main_net_inflow for f in recent) / 1e4
        return f"近3日主力净流入 {total:.0f}万元"
