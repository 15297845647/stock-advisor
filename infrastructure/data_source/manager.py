"""数据源降级管理器 — 按 policy 链式尝试各 source

核心职责：
1. 接收业务层的数据请求（quote/kline/...）
2. 按 policy 获取降级链
3. 遍历 chain 依次尝试，跳过熔断/未启用/不支持的
4. 首个成功即返回，全部失败返回 FetchResult(success=False)
5. 每次调用埋点到 call_log_repo（异步）

无 God Object：本类只做编排，不含数据源具体实现。
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from domain.data_source_policy import DataSourcePolicy
from domain.models.data_source import (
    DataOperation, DataSourceType, FetchResult, SourceHealth,
)
from infrastructure.data_source.base import DataSource

logger = logging.getLogger(__name__)


class DataSourceManager:
    """数据源降级管理器（单例风格，通过 config 层构造）"""

    def __init__(
        self,
        policy: DataSourcePolicy,
        sources: dict[DataSourceType, DataSource],
        call_log_repo,  # DataSourceCallLogRepository（避免循环 import）
    ):
        self.policy = policy
        self._sources: dict[DataSourceType, DataSource] = sources
        self._log_repo = call_log_repo

    # ────────────── 业务接口（供 Application 层调用）──────────────

    async def fetch_quote(self, code: str) -> FetchResult:
        return await self._fetch(
            DataOperation.QUOTE, code,
            lambda src: src.fetch_quote(code),
        )

    async def fetch_kline(self, code: str, days: int = 30) -> FetchResult:
        return await self._fetch(
            DataOperation.KLINE, code,
            lambda src: src.fetch_kline(code, days=days),
        )

    async def fetch_fund_flow(self, code: str) -> FetchResult:
        return await self._fetch(
            DataOperation.FUND_FLOW, code,
            lambda src: src.fetch_fund_flow(code),
        )

    async def fetch_sector_flow(self, count: int = 30) -> FetchResult:
        return await self._fetch(
            DataOperation.SECTOR_FLOW, None,
            lambda src: src.fetch_sector_flow(count=count),
        )

    async def fetch_news(self, code: str, limit: int = 20) -> FetchResult:
        return await self._fetch(
            DataOperation.NEWS, code,
            lambda src: src.fetch_news(code, limit=limit),
        )

    # ────────────── 核心降级循环 ──────────────

    async def _fetch(
        self,
        operation: DataOperation,
        code: str | None,
        invoker: Callable[[DataSource], Awaitable[Any]],
    ) -> FetchResult:
        """
        通用降级执行 — 沿 policy 定义的链依次尝试。
        逻辑约 50 行，符合"方法不超 80 行"规则。
        """
        chain = self.policy.get_chain(operation)
        if not chain:
            return self._empty_result(operation, "policy 中无降级链")

        start_ts = time.monotonic()
        attempts: list[dict] = []
        fallback = 0
        last_error = ""

        for st in chain:
            src = self._sources.get(st)
            if src is None or not src.is_available():
                self._skip_attempt(attempts, st, src)
                fallback += 1
                continue

            attempt_ts = time.monotonic()
            try:
                data = await invoker(src)
            except Exception as e:
                await self._on_failure(attempts, st, src, code, operation, e, attempt_ts)
                last_error = str(e)[:200]
                fallback += 1
                continue

            if self._is_empty(data):
                await self._on_empty(attempts, st, src, code, operation, attempt_ts)
                fallback += 1
                continue

            latency = int((time.monotonic() - attempt_ts) * 1000)
            await self._on_success(attempts, st, src, code, operation, latency)
            return FetchResult(
                success=True, data=data, source=st,
                latency_ms=int((time.monotonic() - start_ts) * 1000),
                fallback_count=max(0, fallback - 0),
                attempts=attempts,
            )

        return FetchResult(
            success=False, data=None, source=None,
            latency_ms=int((time.monotonic() - start_ts) * 1000),
            error=last_error or "所有 source 均不可用",
            fallback_count=fallback,
            attempts=attempts,
        )

    # ────────────── 埋点与状态更新 ──────────────

    def _skip_attempt(
        self, attempts: list[dict], st: DataSourceType, src: DataSource | None,
    ) -> None:
        """记录一次跳过（未启用/熔断/未注册）"""
        reason = "not_registered"
        if src is not None:
            reason = "breaker_open" if src.breaker.is_open() else "disabled"
        attempts.append({"source": st.value, "status": "skip", "reason": reason})

    async def _on_failure(
        self, attempts: list[dict], st: DataSourceType, src: DataSource,
        code: str | None, operation: DataOperation, error: Exception, ts: float,
    ) -> None:
        """记录失败 + 触发熔断 + 落库"""
        latency = int((time.monotonic() - ts) * 1000)
        err = str(error)[:200]
        src.breaker.record_failure(err)
        attempts.append({
            "source": st.value, "status": "fail",
            "latency_ms": latency, "error": err,
        })
        logger.warning("数据源失败 %s %s: %s", st.value, code or "-", err)
        await self._log(operation, st, code, False, latency, err)

    async def _on_empty(
        self, attempts: list[dict], st: DataSourceType, src: DataSource,
        code: str | None, operation: DataOperation, ts: float,
    ) -> None:
        """记录空响应（不算失败，但触发降级）"""
        latency = int((time.monotonic() - ts) * 1000)
        attempts.append({
            "source": st.value, "status": "empty", "latency_ms": latency,
        })
        await self._log(operation, st, code, False, latency, "empty response")

    async def _on_success(
        self, attempts: list[dict], st: DataSourceType, src: DataSource,
        code: str | None, operation: DataOperation, latency: int,
    ) -> None:
        """记录成功 + 重置熔断计数"""
        src.breaker.record_success()
        attempts.append({
            "source": st.value, "status": "success", "latency_ms": latency,
        })
        await self._log(operation, st, code, True, latency, None)

    async def _log(
        self, operation: DataOperation, st: DataSourceType,
        code: str | None, success: bool, latency: int, error: str | None,
    ) -> None:
        """异步落库日志（失败不阻塞主流程）"""
        if self._log_repo is None:
            return
        try:
            await self._log_repo.insert(
                operation=operation.value, source=st.value,
                stock_code=code, success=success,
                latency_ms=latency, error=error,
            )
        except Exception as e:
            logger.debug("落库调用日志失败(忽略): %s", e)

    @staticmethod
    def _is_empty(data: Any) -> bool:
        """判空 — None / 空 list / 空 dict"""
        if data is None:
            return True
        if isinstance(data, (list, tuple, dict)) and len(data) == 0:
            return True
        return False

    def _empty_result(self, operation: DataOperation, msg: str) -> FetchResult:
        """构造空结果"""
        logger.warning("operation %s: %s", operation.value, msg)
        return FetchResult(success=False, error=msg)

    # ────────────── Admin 支持接口 ──────────────

    def get_all_sources(self) -> dict[str, DataSource]:
        """返回所有 source 按 name 索引的字典"""
        return {src.name: src for src in self._sources.values()}

    def get_source_by_name(self, name: str) -> DataSource | None:
        """按 name 查 source"""
        for src in self._sources.values():
            if src.name == name:
                return src
        return None

    def reset_breaker(self, name: str) -> bool:
        """手动重置指定 source 的熔断器"""
        src = self.get_source_by_name(name)
        if src is None:
            return False
        src.breaker.reset()
        logger.info("熔断器已手动重置: %s", name)
        return True

    def snapshot_health(self) -> list[SourceHealth]:
        """当前所有 source 的健康状态快照（无 IO）"""
        out: list[SourceHealth] = []
        for st, src in self._sources.items():
            state = src.breaker.get_state()
            out.append(SourceHealth(
                name=src.name,
                source_type=st,
                enabled=src.enabled,
                healthy=src.is_available(),
                breaker_open=state["is_open"],
                consecutive_failures=state["consecutive_failures"],
                cooldown_remaining_sec=state["cooldown_remaining_sec"],
                last_failure_at=self._parse_dt(state.get("last_fail_at")),
                last_error=state.get("last_error"),
            ))
        return out

    async def probe_all(self) -> dict[str, tuple[bool, str]]:
        """并发探活所有 source（供 Admin"立即探活"按钮）"""
        results: dict[str, tuple[bool, str]] = {}
        tasks = {
            src.name: asyncio.create_task(src.health_check())
            for src in self._sources.values() if src.enabled
        }
        for name, task in tasks.items():
            try:
                results[name] = await task
            except Exception as e:
                results[name] = (False, str(e)[:200])
        return results

    @staticmethod
    def _parse_dt(s: str | None):
        """解析 ISO 时间字符串为 datetime（失败返回 None）"""
        if not s:
            return None
        from datetime import datetime as _dt
        try:
            return _dt.fromisoformat(s)
        except Exception:
            return None
