"""股票新闻 Repository — 预拉取入库 + 分析时读取

单一职责：stock_news 表 CRUD。
"""

import hashlib
import logging
from datetime import datetime, timedelta

from domain.models.stock import StockNews
from infrastructure.database import get_connection

logger = logging.getLogger(__name__)


class NewsRepository:
    """股票新闻 CRUD"""

    async def upsert_batch(
        self, stock_code: str, news_list: list[StockNews],
    ) -> int:
        """批量插入 — hash 冲突自动跳过，返回新插入条数"""
        if not news_list:
            return 0

        inserted = 0
        conn = await get_connection()
        try:
            for n in news_list:
                h = self._make_hash(stock_code, n.title, n.time)
                try:
                    await conn.execute(
                        "INSERT OR IGNORE INTO stock_news "
                        "(stock_code, title, content, publish_time, "
                        " source, url, news_type, hash) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            stock_code, n.title[:500], "",
                            n.time, n.source[:100], n.url[:500],
                            n.news_type, h,
                        ),
                    )
                    inserted += 1
                except Exception as e:
                    logger.debug("新闻插入失败(%s): %s", n.title[:30], e)
            await conn.commit()
            return inserted
        finally:
            await conn.close()

    async def get_recent(
        self, stock_code: str, hours: int = 24, limit: int = 20,
    ) -> list[StockNews]:
        """按 stock_code 查最近 N 小时新闻（按发布时间倒序）"""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat(sep=" ")
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT title, source, publish_time, url, news_type "
                "FROM stock_news WHERE stock_code = ? AND created_at >= ? "
                "ORDER BY publish_time DESC LIMIT ?",
                (stock_code, cutoff, limit),
            )
            return [self._row_to_news(r) for r in rows]
        finally:
            await conn.close()

    async def has_recent(self, stock_code: str, hours: int = 6) -> bool:
        """是否有近 N 小时的新闻缓存"""
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat(sep=" ")
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT 1 FROM stock_news WHERE stock_code = ? AND created_at >= ? LIMIT 1",
                (stock_code, cutoff),
            )
            return len(rows) > 0
        finally:
            await conn.close()

    async def cleanup_old(self, keep_days: int = 30) -> int:
        """清理 N 天前新闻"""
        cutoff = (datetime.utcnow() - timedelta(days=keep_days)).isoformat(sep=" ")
        conn = await get_connection()
        try:
            cur = await conn.execute(
                "DELETE FROM stock_news WHERE created_at < ?", (cutoff,),
            )
            await conn.commit()
            return cur.rowcount
        finally:
            await conn.close()

    # ── 辅助 ──

    @staticmethod
    def _make_hash(code: str, title: str, time_str: str) -> str:
        raw = f"{code}|{title}|{time_str}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_news(row) -> StockNews:
        return StockNews(
            title=row["title"], source=row["source"] or "",
            time=row["publish_time"], url=row["url"] or "",
            news_type=row["news_type"] or "news",
        )
