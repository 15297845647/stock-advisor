import asyncio
import logging
from datetime import date, datetime, timedelta
from functools import partial

import akshare as ak

from agent.config import AKSHARE_REQUEST_INTERVAL
from domain.models.stock import FundFlow, StockDailyBar, StockQuote

logger = logging.getLogger(__name__)

# 简易限频锁
_rate_lock = asyncio.Lock()
_last_call_time: float = 0.0


async def _throttle():
    """AKShare 调用限频，防反爬"""
    global _last_call_time
    async with _rate_lock:
        now = asyncio.get_event_loop().time()
        elapsed = now - _last_call_time
        if elapsed < AKSHARE_REQUEST_INTERVAL:
            await asyncio.sleep(AKSHARE_REQUEST_INTERVAL - elapsed)
        _last_call_time = asyncio.get_event_loop().time()


async def _run_sync(func, *args, **kwargs):
    """在线程池中执行同步 akshare 调用"""
    await _throttle()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


class AKShareClient:
    """AKShare 数据采集封装"""

    async def get_stock_history(
        self, code: str, days: int = 60
    ) -> list[StockDailyBar]:
        """获取个股日K线"""
        end_date = date.today()
        start_date = end_date - timedelta(days=days + 30)  # 多取一些防节假日不足
        try:
            df = await _run_sync(
                ak.stock_zh_a_hist,
                symbol=code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception as e:
            logger.error("获取 %s 日K失败: %s", code, e)
            return []

        bars = []
        for _, row in df.tail(days).iterrows():
            bars.append(
                StockDailyBar(
                    code=code,
                    trade_date=row["日期"].date() if isinstance(row["日期"], datetime) else row["日期"],
                    open=float(row["开盘"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    close=float(row["收盘"]),
                    volume=float(row["成交量"]),
                    amount=float(row["成交额"]),
                    change_pct=float(row["涨跌幅"]),
                )
            )
        return bars

    async def get_realtime_quote(self, code: str) -> StockQuote | None:
        """获取个股实时行情"""
        try:
            df = await _run_sync(ak.stock_zh_a_spot_em)
            row = df[df["代码"] == code]
            if row.empty:
                return None
            r = row.iloc[0]
            return StockQuote(
                code=code,
                name=str(r["名称"]),
                price=float(r["最新价"]),
                change_pct=float(r["涨跌幅"]),
                volume=float(r["成交量"]),
                amount=float(r["成交额"]),
                high=float(r["最高"]),
                low=float(r["最低"]),
                open_price=float(r["今开"]),
                prev_close=float(r["昨收"]),
            )
        except Exception as e:
            logger.error("获取 %s 实时行情失败: %s", code, e)
            return None

    async def get_fund_flow(self, code: str) -> list[FundFlow]:
        """获取个股资金流向（近日）"""
        try:
            df = await _run_sync(
                ak.stock_individual_fund_flow, stock=code, market="sh" if code.startswith("6") else "sz"
            )
        except Exception as e:
            logger.error("获取 %s 资金流向失败: %s", code, e)
            return []

        flows = []
        for _, row in df.tail(5).iterrows():
            flows.append(
                FundFlow(
                    code=code,
                    trade_date=row["日期"].date() if isinstance(row["日期"], datetime) else row["日期"],
                    main_net_inflow=float(row.get("主力净流入-净额", 0)),
                    super_large_net=float(row.get("超大单净流入-净额", 0)),
                    large_net=float(row.get("大单净流入-净额", 0)),
                    medium_net=float(row.get("中单净流入-净额", 0)),
                    small_net=float(row.get("小单净流入-净额", 0)),
                )
            )
        return flows

    async def get_market_index(self, index_code: str = "000001") -> StockQuote | None:
        """获取指数实时行情（如上证指数000001）"""
        try:
            df = await _run_sync(ak.stock_zh_index_spot_em)
            row = df[df["代码"] == index_code]
            if row.empty:
                return None
            r = row.iloc[0]
            return StockQuote(
                code=index_code,
                name=str(r["名称"]),
                price=float(r["最新价"]),
                change_pct=float(r["涨跌幅"]),
                volume=float(r.get("成交量", 0)),
                amount=float(r.get("成交额", 0)),
                high=float(r.get("最高", 0)),
                low=float(r.get("最低", 0)),
                open_price=float(r.get("今开", 0)),
                prev_close=float(r.get("昨收", 0)),
            )
        except Exception as e:
            logger.error("获取指数 %s 失败: %s", index_code, e)
            return None

    async def is_trade_day(self, d: date | None = None) -> bool:
        """判断是否为交易日"""
        d = d or date.today()
        try:
            df = await _run_sync(ak.tool_trade_date_hist_sina)
            trade_dates = set(df["trade_date"].astype(str))
            return d.strftime("%Y-%m-%d") in trade_dates
        except Exception as e:
            logger.warning("获取交易日历失败，默认为交易日: %s", e)
            return d.weekday() < 5  # 降级：周一到周五
