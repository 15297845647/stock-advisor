"""熔断器 — 连续失败 N 次后 cooldown 秒内跳过

单一职责：熔断状态管理，无网络调用。
每个数据源实例持有一个 CircuitBreaker。
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class _State:
    """熔断器内部状态"""
    failures: int = 0
    last_fail_ts: float = 0.0
    last_error: str = ""
    last_fail_at: datetime | None = None
    total_success: int = 0
    total_failure: int = 0


class CircuitBreaker:
    """
    熔断器状态机：
        Closed  → 失败 threshold 次 → Open
        Open    → cooldown 秒后 → Closed（重置失败计数）
    Open 状态下 is_open() 返回 True，调用方应跳过实际请求。
    """

    def __init__(self, name: str, threshold: int = 3, cooldown: int = 300):
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self._state = _State()

    # ── 状态变更 ──

    def record_success(self) -> None:
        """记录一次成功 — 立即重置失败计数"""
        self._state.failures = 0
        self._state.total_success += 1

    def record_failure(self, error: str = "") -> None:
        """记录一次失败 — 累加计数，超过阈值即触发熔断"""
        self._state.failures += 1
        self._state.last_fail_ts = time.monotonic()
        self._state.last_fail_at = datetime.now()
        self._state.last_error = error[:200]  # 截断避免过长
        self._state.total_failure += 1

    def reset(self) -> None:
        """手动重置熔断器 — 清零失败计数"""
        self._state.failures = 0
        self._state.last_fail_ts = 0.0
        self._state.last_error = ""

    # ── 状态查询 ──

    def is_open(self) -> bool:
        """当前是否处于熔断状态"""
        if self._state.failures < self.threshold:
            return False
        elapsed = time.monotonic() - self._state.last_fail_ts
        if elapsed > self.cooldown:
            # cooldown 结束，自动重置进入半开状态
            self._state.failures = 0
            return False
        return True

    def get_cooldown_remaining(self) -> int:
        """返回熔断剩余秒数（未熔断时返回 0）"""
        if self._state.failures < self.threshold:
            return 0
        elapsed = time.monotonic() - self._state.last_fail_ts
        remain = self.cooldown - elapsed
        return max(0, int(remain))

    def get_state(self) -> dict:
        """返回完整状态快照（供 Admin 展示）"""
        return {
            "name": self.name,
            "threshold": self.threshold,
            "cooldown": self.cooldown,
            "consecutive_failures": self._state.failures,
            "is_open": self.is_open(),
            "cooldown_remaining_sec": self.get_cooldown_remaining(),
            "last_fail_at": (
                self._state.last_fail_at.isoformat()
                if self._state.last_fail_at else None
            ),
            "last_error": self._state.last_error or None,
            "total_success": self._state.total_success,
            "total_failure": self._state.total_failure,
        }
