"""自选股新闻同步服务

单一职责：编排"查全体自选股 → 并发拉新闻 → 去重入库 → 清理旧数据"。
被 scheduler/news_sync.py 定时调用。
"""

import asyncio
import logging
from datetime import datetime

from infrastructure.data_source import get_data_manager
from infrastructure.database import get_connection
from repository.news_repository import NewsRepository

logger = logging.getLogger(__name__)


class NewsSyncService:
    """新闻同步编排"""

    _CONCURRENCY = 5   # 并发拉取限制

    def __init__(self):
        self.news_repo = NewsRepository()

    async def sync_all_watchlists(
        self, limit_per_stock: int = 10,
    ) -> dict:
        """
        主入口：拉所有用户自选股的新闻
        返回执行摘要
        """
        start = datetime.now()
        codes = await self._collect_watchlist_codes()
        if not codes:
            return {"start": start.isoformat(), "codes": 0, "inserted": 0}

        logger.info("新闻同步开始: %d 只自选股", len(codes))

        total_inserted = 0
        success = 0
        failed = 0

        sem = asyncio.Semaphore(self._CONCURRENCY)

        async def _fetch_one(code: str) -> int:
            async with sem:
                return await self._fetch_and_store(code, limit_per_stock)

        tasks = [_fetch_one(c) for c in codes]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                failed += 1
            else:
                success += 1
                total_inserted += r

        deleted = await self.news_repo.cleanup_old(keep_days=30)
        elapsed = (datetime.now() - start).total_seconds()

        summary = {
            "start": start.isoformat(),
            "codes": len(codes),
            "success": success,
            "failed": failed,
            "inserted": total_inserted,
            "cleaned_up": deleted,
            "elapsed_sec": round(elapsed, 2),
        }
        logger.info("新闻同步完成: %s", summary)
        return summary

    # ── 内部 ──

    async def _collect_watchlist_codes(self) -> list[str]:
        """查所有用户自选股的并集"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT DISTINCT stock_code FROM user_watchlist"
            )
            return [r["stock_code"] for r in rows]
        finally:
            await conn.close()

    async def _fetch_and_store(self, code: str, limit: int) -> int:
        """拉单只股票的新闻并入库，返回新增条数"""
        result = await get_data_manager().fetch_news(code, limit=limit)
        if not result.success:
            logger.debug("新闻拉取失败 %s: %s", code, result.error)
            return 0

        news_list = result.data or []
        return await self.news_repo.upsert_batch(code, news_list)
