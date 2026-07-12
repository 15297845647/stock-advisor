"""数据源管理 Admin 编排服务

单一职责：编排 DataSourceManager + Repo 数据供 Admin API 使用。
本层不含业务逻辑，只做数据组装 + 格式转换。
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from infrastructure.data_source import (
    get_data_manager, reload_data_manager,
)
from repository.data_source_call_log_repository import (
    DataSourceCallLogRepository,
)

logger = logging.getLogger(__name__)


class DataSourceAdminService:
    """数据源观测 + 管理编排"""

    def __init__(self):
        self.call_log_repo = DataSourceCallLogRepository()

    # ────────────── 概览 ──────────────

    async def get_summary(self, hours: int = 24) -> dict:
        """概览卡片：总调用数、成功率、平均延迟、健康源数"""
        totals = await self.call_log_repo.total_calls(hours=hours)
        health = self._collect_health()
        return {
            "period_hours": hours,
            "total_calls": totals["calls"],
            "success_rate": totals["success_rate"],
            "avg_latency_ms": totals["avg_latency_ms"],
            "sources_total": len(health),
            "sources_healthy": sum(1 for h in health if h["healthy"]),
            "sources_broken": sum(1 for h in health if h["breaker_open"]),
            "sources_disabled": sum(1 for h in health if not h["enabled"]),
        }

    # ────────────── 健康状态 ──────────────

    async def get_health_status(self) -> dict:
        """所有 source 的实时健康状态"""
        return {"sources": self._collect_health()}

    def _collect_health(self) -> list[dict]:
        """内部：把 SourceHealth 转成字典列表"""
        manager = get_data_manager()
        snapshots = manager.snapshot_health()
        return [
            {
                "name": s.name,
                "source_type": s.source_type.value,
                "enabled": s.enabled,
                "healthy": s.healthy,
                "breaker_open": s.breaker_open,
                "consecutive_failures": s.consecutive_failures,
                "cooldown_remaining_sec": s.cooldown_remaining_sec,
                "last_failure_at": (
                    s.last_failure_at.isoformat() if s.last_failure_at else None
                ),
                "last_error": s.last_error,
            }
            for s in snapshots
        ]

    # ────────────── 统计 ──────────────

    async def get_stats(self, hours: int = 24) -> dict:
        """按 source 汇总近 N 小时统计"""
        stats = await self.call_log_repo.aggregate_by_source(hours=hours)
        return {"period_hours": hours, "sources": stats}

    async def get_stats_by_operation(self, hours: int = 24) -> dict:
        """按 operation × source 展示降级链命中分布"""
        rows = await self.call_log_repo.aggregate_by_operation(hours=hours)
        return {"period_hours": hours, "operations": self._pivot_operation(rows)}

    @staticmethod
    def _pivot_operation(rows: list[dict]) -> list[dict]:
        """把扁平的 (op, source, hits) 三元组透视成按 op 分组的结构"""
        grouped: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            grouped[r["operation"]].append({
                "source": r["source"], "hits": r["hits"],
            })

        result = []
        for op, sources in grouped.items():
            total = sum(s["hits"] for s in sources)
            for s in sources:
                s["rate"] = round(s["hits"] / total, 4) if total else 0.0
            # 首选源以外的命中率之和 = 降级率
            fallback = sum(s["hits"] for s in sources[1:])
            result.append({
                "operation": op,
                "total_calls": total,
                "sources": sources,
                "fallback_rate": round(fallback / total, 4) if total else 0.0,
            })
        return result

    async def get_timeline(self, hours: int = 24) -> dict:
        """近 N 小时的分桶调用趋势（供折线图）"""
        bucket = self._pick_bucket(hours)
        rows = await self.call_log_repo.get_timeline(
            hours=hours, bucket_minutes=bucket,
        )
        return self._build_timeline_series(rows, bucket)

    @staticmethod
    def _pick_bucket(hours: int) -> int:
        """按时间窗口自动选择合适的桶大小"""
        if hours <= 6:
            return 10
        if hours <= 24:
            return 30
        if hours <= 72:
            return 60
        return 120

    @staticmethod
    def _build_timeline_series(rows: list[dict], bucket: int) -> dict:
        """把扁平的 (bucket, source, calls) 转为 series 结构"""
        buckets: list[str] = []
        series: dict[str, dict[str, int]] = defaultdict(dict)

        for r in rows:
            b = r["bucket"]
            if b not in buckets:
                buckets.append(b)
            series[r["source"]][b] = r["calls"]

        # 对齐每个 source 的时间轴
        out_series = {
            src: [values.get(b, 0) for b in buckets]
            for src, values in series.items()
        }
        return {
            "bucket_minutes": bucket,
            "timestamps": buckets,
            "series": out_series,
        }

    async def get_recent_failures(self, limit: int = 50) -> list[dict]:
        """最近失败日志"""
        return await self.call_log_repo.get_recent_failures(limit=limit)

    # ────────────── 操作 ──────────────

    async def reset_breaker(self, source_name: str) -> dict:
        """手动重置某 source 的熔断"""
        manager = get_data_manager()
        ok = manager.reset_breaker(source_name)
        if not ok:
            return {"success": False, "message": f"source 不存在: {source_name}"}
        src = manager.get_source_by_name(source_name)
        return {
            "success": True,
            "source": source_name,
            "state": src.breaker.get_state() if src else None,
        }

    async def probe_all(self) -> dict[str, dict[str, Any]]:
        """并发探活所有源"""
        manager = get_data_manager()
        results = await manager.probe_all()
        return {
            "probed_at": datetime.now().isoformat(),
            "results": {
                name: {"healthy": healthy, "error": err}
                for name, (healthy, err) in results.items()
            },
        }

    async def reload_config(self) -> dict:
        """热重载数据源配置"""
        try:
            manager = reload_data_manager()
            logger.info("数据源配置已热重载")
            return {
                "success": True,
                "config": manager.policy.dump_config(),
            }
        except Exception as e:
            logger.exception("重载数据源配置失败")
            return {"success": False, "message": str(e)}

    async def get_current_config(self) -> dict:
        """返回当前生效的配置"""
        return get_data_manager().policy.dump_config()

    # ────────────── 维护 ──────────────

    async def cleanup_old_logs(self, keep_days: int = 30) -> dict:
        """清理旧调用日志"""
        n = await self.call_log_repo.cleanup_old(keep_days=keep_days)
        return {"deleted": n, "keep_days": keep_days}
