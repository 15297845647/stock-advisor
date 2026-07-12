"""板块热度打分 — 根据当日板块资金流对候选加权

单一职责：给定候选 + 板块资金流数据 → 打分。
"""

import logging

from domain.keyword_dictionaries import SECTOR_KEYWORDS
from domain.models.candidate import Candidate
from domain.models.recommend_intent import RecommendIntent

logger = logging.getLogger(__name__)


class SectorHeatCalculator:
    """板块热度打分器"""

    def score_candidates(
        self,
        candidates: list[Candidate],
        sector_flows: list[dict],
        intent: RecommendIntent,
    ) -> list[Candidate]:
        """
        主入口：为候选打板块热度分，返回排序后的列表
        - Top 30% 板块得 80-100 分
        - 中间 40% 板块得 40-60 分
        - Bottom 30% 板块得 0-30 分
        """
        if not sector_flows:
            # 无板块数据，全部保持默认分（不影响后续 Layer）
            return candidates

        sector_scores = self._compute_sector_scores(sector_flows)
        sector_names = list(sector_scores.keys())

        for c in candidates:
            matched = self._match_sector(c, sector_names)
            if matched:
                c.sector = matched
                c.sector_score = sector_scores.get(matched, 0.0)

        return self._filter_and_sort(candidates, intent)

    # ── 内部逻辑 ──

    @staticmethod
    def _compute_sector_scores(sector_flows: list[dict]) -> dict[str, float]:
        """按主力净流入 + 涨幅综合打分"""
        if not sector_flows:
            return {}

        # 按 main_net_inflow 排序，越高分越高
        sorted_flows = sorted(
            sector_flows, key=lambda x: x.get("main_net_inflow", 0), reverse=True,
        )
        n = len(sorted_flows)
        scores: dict[str, float] = {}

        for rank, item in enumerate(sorted_flows):
            name = item.get("name", "")
            if not name:
                continue

            # 位次转分数：Top 1 = 100，末位 = 0
            rank_score = 100 * (1 - rank / max(n - 1, 1))
            chg_score = min(max(item.get("change_pct", 0) * 10, 0), 30)  # 涨幅奖励
            scores[name] = min(rank_score * 0.7 + chg_score, 100)

        return scores

    @staticmethod
    def _match_sector(
        candidate: Candidate, sector_names: list[str],
    ) -> str | None:
        """粗匹配：股票所属板块（暂用板块名 keyword 反查）"""
        # candidate.sector 可能为空 —— 这里用 name/code 关键词做粗匹配
        name = candidate.name
        for canonical, aliases in SECTOR_KEYWORDS.items():
            if any(a in name for a in aliases):
                # 再从 sector_names 里找最相似的
                for sn in sector_names:
                    if any(a in sn for a in aliases):
                        return sn
        return None

    def _filter_and_sort(
        self, candidates: list[Candidate], intent: RecommendIntent,
    ) -> list[Candidate]:
        """按意图应用板块过滤 + 综合排序"""
        # 若用户指定 sectors，且有硬性要求，只留匹配的
        if intent.sectors and intent.require_hot_sector:
            candidates = [
                c for c in candidates
                if c.sector and self._sector_matches(c.sector, intent.sectors)
            ]

        # 黑名单
        if intent.blacklist_sectors:
            candidates = [
                c for c in candidates
                if not self._sector_matches(c.sector or "", intent.blacklist_sectors)
            ]

        # 综合排序：涨幅 * (1-w) + 板块热度 * w
        w = intent.sector_weight
        for c in candidates:
            c.final_score = c.change_pct * 5 * (1 - w) + c.sector_score * w
        candidates.sort(key=lambda c: c.final_score, reverse=True)
        return candidates

    @staticmethod
    def _sector_matches(sector_name: str, targets: list[str]) -> bool:
        """板块名是否匹配任一目标（宽松匹配）"""
        for t in targets:
            aliases = SECTOR_KEYWORDS.get(t, [t])
            if any(a in sector_name for a in aliases):
                return True
        return False
