"""LLM 用量记录 Repository

单一职责：llm_usage 表的 CRUD + 聚合查询。
"""

import logging
from datetime import datetime, timedelta

from infrastructure.database import get_connection

logger = logging.getLogger(__name__)


class LLMUsageRepository:
    """LLM 调用记录 CRUD"""

    # ── 写入 ──

    async def insert(
        self,
        wechat_id: str | None,
        task_type: str,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_cny: float,
        latency_ms: int,
        success: bool,
        error: str | None = None,
    ) -> None:
        """插入一条用量记录（失败不抛异常）"""
        total = prompt_tokens + completion_tokens
        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT INTO llm_usage "
                "(wechat_id, task_type, provider, model, "
                " prompt_tokens, completion_tokens, total_tokens, "
                " cost_cny, latency_ms, success, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    wechat_id, task_type, provider, model,
                    prompt_tokens, completion_tokens, total,
                    cost_cny, latency_ms, 1 if success else 0, error,
                ),
            )
            await conn.commit()
        except Exception as e:
            logger.debug("LLM 用量落库失败(忽略): %s", e)
        finally:
            await conn.close()

    async def cleanup_old(self, keep_days: int = 90) -> int:
        """清理 N 天前的记录"""
        cutoff = (
            datetime.utcnow() - timedelta(days=keep_days)
        ).isoformat(sep=" ")
        conn = await get_connection()
        try:
            cur = await conn.execute(
                "DELETE FROM llm_usage WHERE created_at < ?", (cutoff,),
            )
            await conn.commit()
            return cur.rowcount
        finally:
            await conn.close()

    # ── 聚合查询 ──

    async def total_summary(self, hours: int = 24) -> dict:
        """近 N 小时汇总"""
        cutoff = self._cutoff(hours)
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT COUNT(*) as calls, SUM(success) as success, "
                "SUM(total_tokens) as tokens, SUM(cost_cny) as cost, "
                "AVG(latency_ms) as avg_latency "
                "FROM llm_usage WHERE created_at >= ?",
                (cutoff,),
            )
            r = rows[0]
            calls = r["calls"] or 0
            success = r["success"] or 0
            return {
                "calls": calls,
                "success": success,
                "success_rate": round(success / calls, 4) if calls else 0.0,
                "total_tokens": r["tokens"] or 0,
                "total_cost_cny": round(r["cost"] or 0.0, 4),
                "avg_latency_ms": round(r["avg_latency"] or 0.0, 1),
            }
        finally:
            await conn.close()

    async def by_task(self, hours: int = 24) -> list[dict]:
        """按 task_type 汇总"""
        cutoff = self._cutoff(hours)
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT task_type, COUNT(*) as calls, "
                "SUM(total_tokens) as tokens, SUM(cost_cny) as cost, "
                "AVG(latency_ms) as avg_latency "
                "FROM llm_usage WHERE created_at >= ? "
                "GROUP BY task_type ORDER BY cost DESC",
                (cutoff,),
            )
            return [self._row_to_agg(r) for r in rows]
        finally:
            await conn.close()

    async def by_provider(self, hours: int = 24) -> list[dict]:
        """按 provider 汇总"""
        cutoff = self._cutoff(hours)
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT provider || '/' || model as name, "
                "COUNT(*) as calls, SUM(total_tokens) as tokens, "
                "SUM(cost_cny) as cost, AVG(latency_ms) as avg_latency "
                "FROM llm_usage WHERE created_at >= ? "
                "GROUP BY provider, model ORDER BY cost DESC",
                (cutoff,),
            )
            return [self._row_to_agg(r, name_key="name") for r in rows]
        finally:
            await conn.close()

    async def by_user(self, hours: int = 24, limit: int = 20) -> list[dict]:
        """按用户排行"""
        cutoff = self._cutoff(hours)
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT wechat_id, COUNT(*) as calls, "
                "SUM(total_tokens) as tokens, SUM(cost_cny) as cost "
                "FROM llm_usage "
                "WHERE created_at >= ? AND wechat_id IS NOT NULL "
                "GROUP BY wechat_id ORDER BY cost DESC LIMIT ?",
                (cutoff, limit),
            )
            return [
                {
                    "wechat_id": r["wechat_id"],
                    "calls": r["calls"],
                    "tokens": r["tokens"] or 0,
                    "cost_cny": round(r["cost"] or 0.0, 4),
                }
                for r in rows
            ]
        finally:
            await conn.close()

    async def daily_series(self, days: int = 30) -> list[dict]:
        """每日成本趋势"""
        cutoff = (
            datetime.utcnow() - timedelta(days=days)
        ).isoformat(sep=" ")
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT DATE(created_at) as day, "
                "COUNT(*) as calls, SUM(total_tokens) as tokens, "
                "SUM(cost_cny) as cost "
                "FROM llm_usage WHERE created_at >= ? "
                "GROUP BY day ORDER BY day ASC",
                (cutoff,),
            )
            return [
                {
                    "day": r["day"],
                    "calls": r["calls"],
                    "tokens": r["tokens"] or 0,
                    "cost_cny": round(r["cost"] or 0.0, 4),
                }
                for r in rows
            ]
        finally:
            await conn.close()

    # ── 辅助 ──

    @staticmethod
    def _cutoff(hours: int) -> str:
        return (
            datetime.utcnow() - timedelta(hours=hours)
        ).isoformat(sep=" ")

    @staticmethod
    def _row_to_agg(row, name_key: str = "task_type") -> dict:
        return {
            name_key: row[name_key] if name_key in row.keys() else row["task_type"],
            "calls": row["calls"],
            "tokens": row["tokens"] or 0,
            "cost_cny": round(row["cost"] or 0.0, 4),
            "avg_latency_ms": round(row["avg_latency"] or 0.0, 1),
        }
