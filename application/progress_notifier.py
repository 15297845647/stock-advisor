"""分析进度推送器

单一职责：管理进度事件的节流 + 推送。
Noop 版供 CLI/无用户上下文场景。
微信版通过 cc-connect 主动推送 API 发送中间态。

设计取舍：
    cc-connect 主动推送需要专属 HTTP 接口（未来对接）。
    当前实现只做本地日志 + 事件收集，
    留出 send_hook 供后续接入 cc-connect / SSE。
"""

import logging
import time

from domain.models.research_depth import DEPTH_LABELS, ResearchDepth

logger = logging.getLogger(__name__)


class ProgressNotifier:
    """基类：定义 emit 接口"""

    async def emit(self, phase: str, percent: int, message: str) -> None:
        raise NotImplementedError


class NoopProgressNotifier(ProgressNotifier):
    """空实现 — 用于 CLI / 无 wechat_id 场景"""

    async def emit(self, phase: str, percent: int, message: str) -> None:
        return


class WeChatProgressNotifier(ProgressNotifier):
    """微信版进度推送器

    通过 cc-connect API 推送中间态。
    未接入 cc-connect 主动推送前，本类只做日志 + 内部事件收集。
    """

    _MIN_INTERVAL_SEC = 3.0

    def __init__(
        self, wechat_id: str, stock_code: str, depth: ResearchDepth,
    ):
        self.wechat_id = wechat_id
        self.stock_code = stock_code
        self.depth = depth
        self._sent_phases: set[str] = set()
        self._last_emit_ts: float = 0.0

    async def emit(self, phase: str, percent: int, message: str) -> None:
        """
        推送一次进度 — 节流 + 去重
        """
        if not self._should_emit(phase):
            return

        self._sent_phases.add(phase)
        self._last_emit_ts = time.monotonic()

        text = self._format_message(percent, message)
        logger.info(
            "[进度] %s %s → %s", self.wechat_id, self.stock_code, text,
        )
        await self._send_to_wechat(text)

    def _should_emit(self, phase: str) -> bool:
        """节流：同 phase 只推一次；相邻推送 ≥3s；quick 深度不推"""
        if self.depth == ResearchDepth.QUICK:
            return False
        if phase in self._sent_phases:
            return False
        if time.monotonic() - self._last_emit_ts < self._MIN_INTERVAL_SEC:
            return False
        return True

    def _format_message(self, percent: int, message: str) -> str:
        """格式化推送文本"""
        depth_label = DEPTH_LABELS.get(self.depth, "分析")
        return f"[{depth_label} {percent}%] {message}"

    async def _send_to_wechat(self, text: str) -> None:
        """
        推送到微信 — TODO: 接入 cc-connect 主动推送 API
        当前占位：仅日志，无实际推送。
        """
        # 未来接入示意：
        # try:
        #     from infrastructure.cc_connect_pusher import push_to_user
        #     await push_to_user(self.wechat_id, text)
        # except Exception as e:
        #     logger.debug("cc-connect 推送失败(忽略): %s", e)
        return
