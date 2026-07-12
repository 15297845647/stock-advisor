"""LLM 输出的 [REC:...] 结构化解析

单一职责：字符串解析。
"""

import json
import logging
import re
from datetime import datetime

from domain.models.candidate import Candidate
from domain.models.recommendation import Recommendation

logger = logging.getLogger(__name__)

_REC_RE = re.compile(r"\[REC:(\{.*?\})\]", re.DOTALL)


class RecommendationParser:
    """从 LLM 文本响应提取推荐列表"""

    def parse(
        self, text: str, candidates_index: dict[str, Candidate],
    ) -> list[Recommendation]:
        """
        提取 [REC:...] 块，转成 Recommendation 列表
        candidates_index: {code: Candidate}，用于补齐 price / 技术摘要
        """
        recs: list[Recommendation] = []
        for match in _REC_RE.finditer(text):
            raw = match.group(1)
            rec = self._parse_one(raw, candidates_index)
            if rec is not None:
                recs.append(rec)
        return recs

    def _parse_one(
        self, raw: str, candidates_index: dict[str, Candidate],
    ) -> Recommendation | None:
        """解析单个 REC JSON"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("REC JSON 解析失败: %s | raw=%s", e, raw[:200])
            return None

        code = str(data.get("code", "")).strip()
        if not code:
            return None

        candidate = candidates_index.get(code)
        price = candidate.price if candidate else 0.0

        return Recommendation(
            code=code,
            name=str(data.get("name") or (candidate.name if candidate else code)),
            price=price,
            target_price=float(data.get("target", 0) or 0),
            stop_loss=float(data.get("stop", 0) or 0),
            risk_score=int(data.get("risk", 5) or 5),
            reason=str(data.get("reason", "")).strip(),
            tech_summary=candidate.kline_summary if candidate else "",
            fund_flow_summary=candidate.fund_flow_summary if candidate else "",
            recommended_at=datetime.now(),
        )

    def extract_summary_text(self, text: str) -> str:
        """从 LLM 输出中提取 [REC:] 之前的总结文本（去掉标记）"""
        stripped = _REC_RE.sub("", text).strip()
        return stripped
