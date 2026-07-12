"""推荐意图解析器 — 关键词优先 + LLM 兜底

单一职责：从用户消息 + 用户画像 → RecommendIntent。
本层无网络调用（LLM 兜底委托给 Application）。
"""

import logging
import re

from domain.keyword_dictionaries import (
    BLACKLIST_PATTERNS, RISK_KEYWORDS, SECTOR_KEYWORDS, STYLE_KEYWORDS,
    REFRESH_KEYWORDS,
)
from domain.models.recommend_intent import RecommendIntent
from domain.models.user_context import UserContext

logger = logging.getLogger(__name__)


class RecommendIntentParser:
    """基于关键词字典的意图解析器"""

    def parse(
        self, message: str, ctx: UserContext,
        exclude_codes: list[str] | None = None,
    ) -> RecommendIntent:
        """
        主入口：合成 RecommendIntent
        优先级：用户画像默认 → 关键词字典 → 用户诉求覆盖 → 排除项
        """
        intent = RecommendIntent(raw_message=message)

        self._apply_user_profile(intent, ctx)
        self._apply_style_keywords(intent, message)
        self._apply_sector_keywords(intent, message)
        self._apply_blacklist(intent, message, ctx)

        if exclude_codes:
            intent.exclude_codes = list(exclude_codes)

        # 从消息中解析显式数字（"推 3 只"）
        self._parse_target_count(intent, message)

        return intent

    def is_refresh_request(self, message: str) -> bool:
        """判断是否为"再推一批"类刷新请求"""
        return any(kw in message for kw in REFRESH_KEYWORDS)

    # ── 内部规则 ──

    def _apply_user_profile(
        self, intent: RecommendIntent, ctx: UserContext,
    ) -> None:
        """应用用户画像默认值"""
        style = getattr(ctx.profile, "trade_style", "swing")
        intent.style = style
        # 风险偏好映射
        risk = getattr(ctx.profile, "risk_level", "moderate")
        cfg = RISK_KEYWORDS.get(risk, {})
        self._merge(intent, cfg)

        # style 默认周期
        if style == "day":
            intent.horizon_days = 3
        elif style in ("position", "long"):
            intent.horizon_days = 30

    def _apply_style_keywords(
        self, intent: RecommendIntent, message: str,
    ) -> None:
        """匹配风格关键词"""
        for kw, overrides in STYLE_KEYWORDS.items():
            if kw in message:
                self._merge(intent, overrides)

    def _apply_sector_keywords(
        self, intent: RecommendIntent, message: str,
    ) -> None:
        """匹配板块偏好"""
        for canonical, aliases in SECTOR_KEYWORDS.items():
            if any(a in message for a in aliases):
                if canonical not in intent.sectors:
                    intent.sectors.append(canonical)

    def _apply_blacklist(
        self, intent: RecommendIntent, message: str, ctx: UserContext,
    ) -> None:
        """应用用户诉求 + 记忆里的黑名单"""
        # 消息中的显式黑名单
        for pat, cfg in BLACKLIST_PATTERNS:
            if pat in message:
                self._merge_blacklist(intent, cfg)

        # 记忆里的黑名单（简单启发式扫描）
        for memory in getattr(ctx, "memories", []):
            self._scan_memory_for_blacklist(intent, memory)

    def _scan_memory_for_blacklist(
        self, intent: RecommendIntent, memory: str,
    ) -> None:
        """从单条记忆里挖黑名单板块"""
        if "不要" in memory or "不喜欢" in memory or "别推" in memory:
            for canonical, aliases in SECTOR_KEYWORDS.items():
                if any(a in memory for a in aliases):
                    if canonical not in intent.blacklist_sectors:
                        intent.blacklist_sectors.append(canonical)

    @staticmethod
    def _parse_target_count(intent: RecommendIntent, message: str) -> None:
        """解析"推 3 只/来 5 个"类显式数量"""
        m = re.search(r"(\d+)\s*[只个支]", message)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 20:
                intent.target_count = n

    @staticmethod
    def _merge(intent: RecommendIntent, cfg: dict) -> None:
        """按字段合并（不覆盖已有的严格约束）"""
        for k, v in cfg.items():
            # min_* 取更严格的（更大值），max_* 取更严格的（更小值）
            if k.startswith("min_") and getattr(intent, k, None) is not None:
                setattr(intent, k, max(getattr(intent, k), v))
            elif k.startswith("max_") and getattr(intent, k, None) is not None:
                setattr(intent, k, min(getattr(intent, k), v))
            else:
                setattr(intent, k, v)

    @staticmethod
    def _merge_blacklist(intent: RecommendIntent, cfg: dict) -> None:
        """合并黑名单类字段（追加不覆盖）"""
        for k, v in cfg.items():
            if k == "blacklist_sectors":
                intent.blacklist_sectors.extend(
                    [s for s in v if s not in intent.blacklist_sectors]
                )
            elif k == "blacklist_codes":
                intent.blacklist_codes.extend(
                    [c for c in v if c not in intent.blacklist_codes]
                )
            elif k == "exclude_st":
                intent.exclude_st = v
