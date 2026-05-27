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
        """获取个股实时行情，东方财富接口失败时降级到日K最新一条"""
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
        except Exception:
            logger.warning("东方财富个股接口不可用，降级到日K: %s", code)
            return await self._quote_from_daily(code)

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
        """获取指数行情，东方财富接口失败时降级到新浪日K"""
        # 优先尝试东方财富实时接口
        try:
            df = await _run_sync(ak.stock_zh_index_spot_em)
            row = df[df["代码"] == index_code]
            if not row.empty:
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
        except Exception:
            logger.warning("东方财富指数接口不可用，降级到新浪日K: %s", index_code)

        # 降级：新浪日K最新一条
        return await self._index_from_daily(index_code)

    async def get_stock_rank_list(self, count: int = 20) -> list[dict]:
        """获取A股涨幅榜，降级链：东方财富 → 新浪 → 蓝筹兜底"""
        # 尝试东方财富
        try:
            df = await _run_sync(ak.stock_zh_a_spot_em)
            return self._parse_rank_em(df, count)
        except Exception:
            logger.warning("东方财富涨幅榜不可用，尝试新浪")

        # 尝试新浪
        try:
            df = await _run_sync(ak.stock_zh_a_spot)
            df["涨跌幅"] = df["changepercent"].astype(float)
            df = df.sort_values("涨跌幅", ascending=False).head(count)
            result = []
            for _, r in df.iterrows():
                result.append({
                    "code": str(r.get("code", r.get("symbol", ""))),
                    "name": str(r.get("name", "")),
                    "price": float(r.get("trade", r.get("close", 0))),
                    "change_pct": float(r.get("changepercent", 0)),
                    "volume": float(r.get("volume", 0)),
                    "amount": float(r.get("amount", 0)),
                    "turnover": 0,
                })
            return result
        except Exception:
            logger.warning("新浪涨幅榜也不可用，用蓝筹兜底")

        # 最终兜底：用预设蓝筹股拉日K
        return await self._bluechip_fallback()

    @staticmethod
    def _parse_rank_em(df, count: int) -> list[dict]:
        df = df.sort_values("涨跌幅", ascending=False).head(count)
        result = []
        for _, r in df.iterrows():
            result.append({
                "code": str(r["代码"]),
                "name": str(r["名称"]),
                "price": float(r["最新价"]),
                "change_pct": float(r["涨跌幅"]),
                "volume": float(r.get("成交量", 0)),
                "amount": float(r.get("成交额", 0)),
                "turnover": float(r.get("换手率", 0)),
            })
        return result

    _BLUECHIP_CODES = [
        "600519", "000858", "601318", "600036", "000001",
        "600900", "601012", "000333", "002594", "601888",
        "600276", "000568", "002415", "600309", "601166",
    ]

    async def _bluechip_fallback(self) -> list[dict]:
        """用预设蓝筹股的最新日K构造榜单"""
        result = []
        for code in self._BLUECHIP_CODES[:10]:
            bars = await self.get_stock_history(code, days=2)
            if not bars:
                continue
            latest = bars[-1]
            result.append({
                "code": code,
                "name": code,
                "price": latest.close,
                "change_pct": latest.change_pct,
                "volume": latest.volume,
                "amount": latest.amount,
                "turnover": 0,
            })
        result.sort(key=lambda x: x["change_pct"], reverse=True)
        return result

    async def get_sector_fund_flow(self, count: int = 10) -> list[dict]:
        """获取板块资金流向排行"""
        try:
            df = await _run_sync(ak.stock_sector_fund_flow_rank, indicator="今日")
            result = []
            for _, r in df.head(count).iterrows():
                result.append({
                    "name": str(r.get("名称", "")),
                    "change_pct": float(r.get("涨跌幅", 0)),
                    "main_net_inflow": float(r.get("主力净流入-净额", 0)),
                })
            return result
        except Exception:
            logger.warning("板块资金流向接口不可用，跳过")
            return []

    # ── 降级方法：东方财富接口不可用时，用新浪日K填充 ──

    _INDEX_SYMBOL_MAP = {
        "000001": "sh000001",
        "399001": "sz399001",
        "399006": "sz399006",
    }

    _INDEX_NAME_MAP = {
        "000001": "上证指数",
        "399001": "深证成指",
        "399006": "创业板指",
    }

    async def _index_from_daily(self, index_code: str) -> StockQuote | None:
        """用新浪日K最新一条构造指数行情"""
        symbol = self._INDEX_SYMBOL_MAP.get(index_code, f"sh{index_code}")
        try:
            df = await _run_sync(ak.stock_zh_index_daily, symbol=symbol)
            if df.empty:
                return None
            r = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else r
            prev_close = float(prev["close"])
            close = float(r["close"])
            change_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0
            return StockQuote(
                code=index_code,
                name=self._INDEX_NAME_MAP.get(index_code, index_code),
                price=close,
                change_pct=round(change_pct, 2),
                volume=float(r.get("volume", 0)),
                amount=0,
                high=float(r["high"]),
                low=float(r["low"]),
                open_price=float(r["open"]),
                prev_close=prev_close,
            )
        except Exception as e:
            logger.error("新浪指数日K也失败 %s: %s", index_code, e)
            return None

    async def _quote_from_daily(self, code: str) -> StockQuote | None:
        """用个股日K最新一条构造行情"""
        bars = await self.get_stock_history(code, days=2)
        if not bars:
            return None
        latest = bars[-1]
        prev_close = bars[-2].close if len(bars) >= 2 else latest.close
        return StockQuote(
            code=code,
            name=code,
            price=latest.close,
            change_pct=latest.change_pct,
            volume=latest.volume,
            amount=latest.amount,
            high=latest.high,
            low=latest.low,
            open_price=latest.open,
            prev_close=prev_close,
        )

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
