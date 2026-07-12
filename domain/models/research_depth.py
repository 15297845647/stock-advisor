"""研究深度分级 + 进度事件 DTO"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ResearchDepth(str, Enum):
    """分析深度级别"""
    QUICK = "quick"          # ~8s 单次综合
    STANDARD = "standard"    # ~20s 4分析师并行 + Judge
    DEEP = "deep"            # ~50s 4分析师 + Bull/Bear + 三方风控


DEPTH_LABELS = {
    ResearchDepth.QUICK: "快速",
    ResearchDepth.STANDARD: "标准",
    ResearchDepth.DEEP: "深度",
}


@dataclass
class ProgressEvent:
    """分析进度事件 — 供中间态推送"""
    wechat_id: str
    phase: str                          # 阶段标识
    percent: int                        # 0-100
    message: str
    depth: ResearchDepth
    stock_code: str = ""
    ts: datetime = field(default_factory=datetime.now)
