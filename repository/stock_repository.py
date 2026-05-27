from datetime import date

from infrastructure.database import get_connection
from domain.models.stock import StockDailyBar


class StockRepository:
    """行情数据缓存"""

    async def save_daily_bars(self, bars: list[StockDailyBar]):
        if not bars:
            return
        conn = await get_connection()
        try:
            await conn.executemany(
                """INSERT OR REPLACE INTO stock_daily_cache
                   (stock_code, trade_date, open, high, low, close, volume, amount, change_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (b.code, b.trade_date.isoformat(), b.open, b.high, b.low,
                     b.close, b.volume, b.amount, b.change_pct)
                    for b in bars
                ],
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_cached_bars(self, code: str, days: int = 60) -> list[StockDailyBar]:
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                """SELECT * FROM stock_daily_cache
                   WHERE stock_code = ?
                   ORDER BY trade_date DESC LIMIT ?""",
                (code, days),
            )
            bars = [
                StockDailyBar(
                    code=r["stock_code"],
                    trade_date=date.fromisoformat(r["trade_date"]),
                    open=r["open"], high=r["high"], low=r["low"], close=r["close"],
                    volume=r["volume"], amount=r["amount"], change_pct=r["change_pct"],
                )
                for r in reversed(rows)
            ]
            return bars
        finally:
            await conn.close()
