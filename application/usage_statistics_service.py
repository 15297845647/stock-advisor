"""LLM 用量统计 Admin 编排服务

单一职责：编排 LLMUsageRepository 数据供 Admin API。
"""

from repository.llm_usage_repository import LLMUsageRepository


class UsageStatisticsService:
    """LLM 用量统计"""

    def __init__(self):
        self.repo = LLMUsageRepository()

    async def get_dashboard(self, hours: int = 24) -> dict:
        """一次性返回仪表盘所有数据"""
        summary = await self.repo.total_summary(hours=hours)
        by_task = await self.repo.by_task(hours=hours)
        by_provider = await self.repo.by_provider(hours=hours)
        by_user = await self.repo.by_user(hours=hours, limit=20)
        return {
            "period_hours": hours,
            "summary": summary,
            "by_task": by_task,
            "by_provider": by_provider,
            "by_user": by_user,
        }

    async def get_daily_series(self, days: int = 30) -> dict:
        """每日趋势"""
        series = await self.repo.daily_series(days=days)
        return {
            "days": days,
            "series": series,
            "total_cost_cny": round(sum(r["cost_cny"] for r in series), 4),
            "total_calls": sum(r["calls"] for r in series),
        }
