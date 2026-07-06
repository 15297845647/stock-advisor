"""通达信（TDX）行情源 — 基于 mootdx TCP 直连

作为东财/腾讯之外的独立协议兜底：TCP 直连通达信行情服务器，
不走 HTTP，不受东财/新浪反爬与 302 跳转影响。
仅提供个股日K与实时行情；量比等仍由上层用K线估算。
"""

import asyncio
import logging
from datetime import date, datetime

from domain.models.stock import StockDailyBar, StockQuote

logger = logging.getLogger(__name__)

# 日K周期码（mootdx: 9 = 日线）
_FREQ_DAY = 9

# 全局复用客户端：factory 首次会探测最优服务器，避免每次重建
_client = None
_client_lock = asyncio.Lock()


def _get_client():
    global _client
    if _client is None:
        from mootdx.quotes import Quotes
        _client = Quotes.factory(market="std")
    return _client


def _parse_trade_date(value: object) -> date:
    """兼容 Timestamp / 'YYYY-MM-DD HH:MM' 字符串 → date"""
    if isinstance(value, datetime):
        return value.date()
    if hasattr(value, "date"):
        return value.date()
    text = str(value)[:10]
    return datetime.strptime(text, "%Y-%m-%d").date()


class TdxClient:
    """通达信行情客户端，同步调用放线程池并串行化（TCP 连接非并发安全）"""

    async def get_daily_hist(self, code: str, days: int = 60) -> list[StockDailyBar]:
        """拉取个股日K，涨跌幅由相邻收盘价推算"""
        try:
            async with _client_lock:
                df = await asyncio.to_thread(self._bars_sync, code, days)
        except Exception as e:
            logger.warning("通达信日K %s 失败: %s", code, e)
            return []

        if df is None or len(df) == 0:
            return []

        return self._df_to_bars(df, code)

    async def get_realtime_quote(self, code: str) -> StockQuote | None:
        """拉取个股实时行情"""
        try:
            async with _client_lock:
                df = await asyncio.to_thread(self._quotes_sync, code)
        except Exception as e:
            logger.warning("通达信实时行情 %s 失败: %s", code, e)
            return None

        if df is None or len(df) == 0:
            return None

        return self._row_to_quote(df.iloc[0], code)

    # ── 同步调用（在线程池执行）──

    def _bars_sync(self, code: str, days: int):
        return _get_client().bars(symbol=code, frequency=_FREQ_DAY, offset=days)

    def _quotes_sync(self, code: str):
        return _get_client().quotes(symbol=code)

    # ── 数据映射 ──

    @staticmethod
    def _df_to_bars(df, code: str) -> list[StockDailyBar]:
        bars = []
        prev_close = None
        for _, row in df.iterrows():
            close = float(row["close"])
            change_pct = 0.0
            if prev_close and prev_close != 0:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            prev_close = close
            bars.append(StockDailyBar(
                code=code,
                trade_date=_parse_trade_date(row.get("datetime")),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=close,
                volume=float(row.get("vol", 0)),
                amount=float(row.get("amount", 0)),
                change_pct=change_pct,
            ))
        return bars

    @staticmethod
    def _row_to_quote(r, code: str) -> StockQuote:
        price = float(r["price"])
        prev_close = float(r.get("last_close", 0))
        change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        return StockQuote(
            code=code,
            name="",
            price=price,
            change_pct=change_pct,
            volume=float(r.get("vol", 0)),
            amount=float(r.get("amount", 0)),
            high=float(r.get("high", 0)),
            low=float(r.get("low", 0)),
            open_price=float(r.get("open", 0)),
            prev_close=prev_close,
        )
