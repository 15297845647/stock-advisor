"""新闻同步定时任务 — 每交易日 07:30 拉自选股新闻

用 APScheduler 已在 admin/server.py 启动。
本模块提供独立入口 + 交易日判断。
"""

import asyncio
import logging
from datetime import date

from application.news_sync_service import NewsSyncService

logger = logging.getLogger(__name__)


def _is_trade_day(d: date | None = None) -> bool:
    """粗判是否交易日 — 周末除外（假日暂不判断）"""
    if d is None:
        d = date.today()
    return d.weekday() < 5


async def run_news_sync(force: bool = False, limit_per_stock: int = 10) -> dict:
    """
    执行一次新闻同步（供 scheduler 调用 + CLI 手动跑）

    force=True：即使非交易日也执行
    """
    if not force and not _is_trade_day():
        logger.info("非交易日，跳过新闻同步")
        return {"skipped": True, "reason": "non-trade day"}

    svc = NewsSyncService()
    return await svc.sync_all_watchlists(limit_per_stock=limit_per_stock)


def sync_main() -> None:
    """CLI 入口 — 手动跑一次"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    result = asyncio.run(run_news_sync(force=True))
    print(result)


if __name__ == "__main__":
    sync_main()
