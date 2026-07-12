"""LLM 用量记录 DTO

单一职责：领域内的用量数据类型定义。
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class LLMUsageRecord:
    """一条 LLM 调用记录"""

    wechat_id: str | None
    task_type: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cny: float
    latency_ms: int
    success: bool
    error: str | None = None
    created_at: datetime | None = None
