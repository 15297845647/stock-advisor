"""数据源调用日志 Repository

单一职责：data_source_call_log 表的 CRUD + Admin 用的聚合查询。
"""

import logging
from datetime import datetime, timedelta

from infrastructure.database import get_connection

logger = logging.getLogger(__name__)


class DataSourceCallLogRepository:
    """数据源调用记录存取"""

    # ────────────── 写入 ──────────────

    async def insert(
        self,
        operation: str,
        source: str,
        stock_code: str | None,
        success: bool,
        latency_ms: int,
        error: str | None = None,
    ) -> None:
        """插入一条调用日志（供 Manager 埋点调用，失败不抛异常）"""
        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT INTO data_source_call_log "
                "(operation, source, stock_code, success, latency_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (operation, source, stock_code, 1 if success else 0, latency_ms, error),
            )
            await conn.commit()
        except Exception as e:
            logger.debug("插入调用日志失败(忽略): %s", e)
        finally:
            await conn.close()

    async def cleanup_old(self, keep_days: int = 30) -> int:
        """清理 N 天前的日志，返回删除条数"""
        conn = await get_connection()
        try:
            cutoff = (
                datetime.utcnow() - timedelta(days=keep_days)
            ).isoformat(sep=" ")
            cursor = await conn.execute(
                "DELETE FROM data_source_call_log WHERE created_at < ?", (cutoff,),
            )
            await conn.commit()
            return cursor.rowcount
        finally:
            await conn.close()

    # ────────────── 聚合查询（Admin 用）──────────────

    async def aggregate_by_source(self, hours: int = 24) -> list[dict]:
        """按 source 汇总近 N 小时的调用统计"""
        cutoff = self._cutoff(hours)
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT source, "
                "  COUNT(*) as calls, "
                "  SUM(success) as success, "
                "  AVG(latency_ms) as avg_latency, "
                "  MAX(latency_ms) as max_latency "
                "FROM data_source_call_log "
                "WHERE created_at >= ? "
                "GROUP BY source "
                "ORDER BY calls DESC",
                (cutoff,),
            )
            return [self._row_to_stats(r) for r in rows]
        finally:
            await conn.close()

    async def aggregate_by_operation(self, hours: int = 24) -> list[dict]:
        """按 operation × source 展示降级链命中分布"""
        cutoff = self._cutoff(hours)
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT operation, source, COUNT(*) as hits "
                "FROM data_source_call_log "
                "WHERE success = 1 AND created_at >= ? "
                "GROUP BY operation, source "
                "ORDER BY operation, hits DESC",
                (cutoff,),
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def get_recent_failures(self, limit: int = 50) -> list[dict]:
        """查最近 N 条失败记录"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT operation, source, stock_code, latency_ms, error, created_at "
                "FROM data_source_call_log "
                "WHERE success = 0 "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def get_timeline(
        self, hours: int = 24, bucket_minutes: int = 30,
    ) -> list[dict]:
        """
        按时间桶聚合各 source 调用量（供折线图）
        SQLite 用 strftime 分桶
        """
        cutoff = self._cutoff(hours)
        # 拼分钟粒度的时间桶（30分钟 → 每半小时一个点）
        bucket_expr = self._time_bucket_expr(bucket_minutes)
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                f"SELECT {bucket_expr} as bucket, source, "
                "  COUNT(*) as calls, SUM(success) as success "
                "FROM data_source_call_log "
                "WHERE created_at >= ? "
                f"GROUP BY bucket, source "
                "ORDER BY bucket ASC",
                (cutoff,),
            )
            return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def total_calls(self, hours: int = 24) -> dict:
        """汇总总调用数、成功率"""
        cutoff = self._cutoff(hours)
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT COUNT(*) as calls, SUM(success) as success, "
                "  AVG(latency_ms) as avg_latency "
                "FROM data_source_call_log WHERE created_at >= ?",
                (cutoff,),
            )
            r = rows[0]
            calls = r["calls"] or 0
            success = r["success"] or 0
            return {
                "calls": calls,
                "success": success,
                "success_rate": round(success / calls, 4) if calls else 0.0,
                "avg_latency_ms": round(r["avg_latency"] or 0, 1),
            }
        finally:
            await conn.close()

    # ────────────── 辅助 ──────────────

    @staticmethod
    def _cutoff(hours: int) -> str:
        """返回 N 小时前的 UTC ISO 时间戳（对齐 SQLite CURRENT_TIMESTAMP）"""
        return (datetime.utcnow() - timedelta(hours=hours)).isoformat(sep=" ")

    @staticmethod
    def _time_bucket_expr(bucket_minutes: int) -> str:
        """构造 SQLite 时间桶表达式（按 bucket_minutes 分钟对齐）"""
        # strftime('%Y-%m-%d %H:%M', created_at) 得到分钟精度
        # 再用整数除法对齐到桶
        return (
            "strftime('%Y-%m-%d %H:', created_at) || "
            f"printf('%02d', (CAST(strftime('%M', created_at) AS INTEGER) / "
            f"{bucket_minutes}) * {bucket_minutes})"
        )

    @staticmethod
    def _row_to_stats(row) -> dict:
        """DB 行转统计 dict（计算成功率）"""
        calls = row["calls"] or 0
        success = row["success"] or 0
        return {
            "source": row["source"],
            "calls": calls,
            "success": success,
            "success_rate": round(success / calls, 4) if calls else 0.0,
            "avg_latency_ms": round(row["avg_latency"] or 0, 1),
            "max_latency_ms": row["max_latency"] or 0,
        }
