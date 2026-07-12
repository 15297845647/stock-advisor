"""硬规则筛选引擎 — 从全市场行情筛出候选池

单一职责：给定 intent + 全量行情 DataFrame，返回 Candidate 列表。
本层是纯逻辑，无网络调用。
"""

import logging

from domain.models.candidate import Candidate
from domain.models.recommend_intent import RecommendIntent

logger = logging.getLogger(__name__)


class RecommendScreener:
    """全市场硬规则筛选器"""

    def screen(
        self, spot_df, intent: RecommendIntent,
    ) -> list[Candidate]:
        """
        主入口：DataFrame → List[Candidate]
        DataFrame 期望列名（东财 push2delay 格式）：
            代码/名称/最新价/涨跌幅/换手率/量比/成交额
        """
        if spot_df is None or spot_df.empty:
            return []

        rows = self._prefilter_st(spot_df, intent)
        candidates: list[Candidate] = []

        for _, r in rows.iterrows():
            c = self._row_to_candidate(r)
            if c is None:
                continue

            if not self._passes_hard_rules(c, intent):
                continue

            candidates.append(c)

        # 按涨幅倒序取 cap（涨的猛的优先，后面板块+技术会再筛）
        candidates.sort(key=lambda x: x.change_pct, reverse=True)
        return candidates[: intent.candidate_cap]

    # ── 内部规则 ──

    @staticmethod
    def _prefilter_st(spot_df, intent: RecommendIntent):
        """初步过滤：排除 ST/退市/一字板/无量"""
        df = spot_df

        if intent.exclude_st and "名称" in df.columns:
            df = df[~df["名称"].str.contains("ST|退|N", na=False, regex=True)]

        # 排除一字板（|涨跌幅| > 9.7%）
        if "涨跌幅" in df.columns:
            df = df[df["涨跌幅"].between(-9.7, 9.7, inclusive="both")]

        # 排除成交额过低
        if "成交额" in df.columns and intent.min_amount:
            df = df[df["成交额"].fillna(0) >= intent.min_amount]

        return df

    def _row_to_candidate(self, row) -> Candidate | None:
        """DataFrame 行转 Candidate（缺失关键字段直接丢弃）"""
        try:
            code = str(row.get("代码", "")).strip()
            name = str(row.get("名称", "")).strip()
            if not code or len(code) != 6:
                return None
            if code[0] not in "0136":
                return None

            price = float(row.get("最新价", 0) or 0)
            if price <= 0:
                return None

            return Candidate(
                code=code, name=name, price=price,
                change_pct=float(row.get("涨跌幅", 0) or 0),
                turnover=float(row.get("换手率", 0) or 0),
                volume_ratio=float(row.get("量比", 0) or 0),
                amount=float(row.get("成交额", 0) or 0),
            )
        except Exception:
            return None

    def _passes_hard_rules(
        self, c: Candidate, intent: RecommendIntent,
    ) -> bool:
        """判断候选是否通过硬指标"""
        if not self._passes_liquidity(c, intent):
            return False
        if not self._passes_range(c, intent):
            return False
        if not self._passes_exclusion(c, intent):
            return False
        return True

    @staticmethod
    def _passes_liquidity(c: Candidate, intent: RecommendIntent) -> bool:
        """流动性门槛：换手率 + 量比"""
        if intent.min_turnover and c.turnover < intent.min_turnover:
            return False
        if intent.min_volume_ratio and c.volume_ratio < intent.min_volume_ratio:
            return False
        return True

    @staticmethod
    def _passes_range(c: Candidate, intent: RecommendIntent) -> bool:
        """涨跌幅门槛"""
        if intent.min_change_pct is not None and c.change_pct < intent.min_change_pct:
            return False
        if intent.max_change_pct is not None and c.change_pct > intent.max_change_pct:
            return False
        return True

    @staticmethod
    def _passes_exclusion(c: Candidate, intent: RecommendIntent) -> bool:
        """黑名单 + exclude_codes"""
        if c.code in intent.blacklist_codes:
            return False
        if c.code in intent.exclude_codes:
            return False
        return True
