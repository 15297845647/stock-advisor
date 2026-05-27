import asyncio
import json
import logging
import os
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path

import akshare as ak

from agent.config import AKSHARE_REQUEST_INTERVAL
from domain.models.stock import FundFlow, StockDailyBar, StockQuote

logger = logging.getLogger(__name__)

_rate_lock = asyncio.Lock()
_last_call_time: float = 0.0

_CACHE_DIR = Path(os.environ.get("DB_PATH", "data/stock_advisor.db")).parent / "cache"


async def _throttle():
    global _last_call_time
    async with _rate_lock:
        now = asyncio.get_event_loop().time()
        elapsed = now - _last_call_time
        if elapsed < AKSHARE_REQUEST_INTERVAL:
            await asyncio.sleep(AKSHARE_REQUEST_INTERVAL - elapsed)
        _last_call_time = asyncio.get_event_loop().time()


async def _run_sync(func, *args, **kwargs):
    await _throttle()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


def _tx_symbol(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _save_cache(name: str, data: list[dict]):
    """缓存数据到 JSON 文件，标记日期"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"date": date.today().isoformat(), "data": data}
    (_CACHE_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _load_cache(name: str, max_age_days: int = 1) -> list[dict]:
    """读取缓存，超过 max_age_days 天视为过期"""
    path = _CACHE_DIR / f"{name}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cached_date = date.fromisoformat(payload["date"])
        if (date.today() - cached_date).days <= max_age_days:
            logger.info("使用缓存数据: %s (%s)", name, cached_date)
            return payload["data"]
    except Exception:
        pass
    return []


class AKShareClient:
    """AKShare 数据采集 — 新浪(实时) + 腾讯(日K) + 新浪(指数)
    东方财富源在云服务器上被反爬封禁，全部移除。
    """

    # ── 个股日K（腾讯源）──

    async def get_stock_history(
        self, code: str, days: int = 60
    ) -> list[StockDailyBar]:
        end_date = date.today()
        start_date = end_date - timedelta(days=days + 30)
        try:
            df = await _run_sync(
                ak.stock_zh_a_hist_tx,
                symbol=_tx_symbol(code),
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception as e:
            logger.error("获取 %s 日K失败(腾讯源): %s", code, e)
            return []

        if df.empty:
            return []

        bars = []
        prev_close = None
        for _, row in df.tail(days).iterrows():
            close = float(row["close"])
            # 腾讯源无涨跌幅列，手动计算
            change_pct = 0.0
            if prev_close and prev_close != 0:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            prev_close = close

            trade_date = row["date"]
            if isinstance(trade_date, str):
                trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
            elif isinstance(trade_date, datetime):
                trade_date = trade_date.date()

            bars.append(
                StockDailyBar(
                    code=code,
                    trade_date=trade_date,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=close,
                    volume=float(row.get("amount", 0)),
                    amount=0,
                    change_pct=change_pct,
                )
            )
        return bars

    # ── 个股实时行情（新浪源 + 腾讯降级）──

    async def get_realtime_quote(self, code: str) -> StockQuote | None:
        try:
            df = await _run_sync(ak.stock_zh_a_spot)
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
            logger.warning("新浪实时行情不可用，降级到腾讯日K: %s", code)
        return await self._quote_from_daily(code)

    # ── 个股资金流向 ──

    async def get_fund_flow(self, code: str) -> list[FundFlow]:
        try:
            df = await _run_sync(
                ak.stock_individual_fund_flow,
                stock=code,
                market="sh" if code.startswith("6") else "sz",
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

    # ── 指数行情（新浪日K）──

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

    async def get_market_index(self, index_code: str = "000001") -> StockQuote | None:
        symbol = self._INDEX_SYMBOL_MAP.get(index_code, f"sh{index_code}")
        try:
            df = await _run_sync(ak.stock_zh_index_daily, symbol=symbol)
            if df.empty:
                return None
            r = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else r
            prev_close = float(prev["close"])
            close = float(r["close"])
            change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0
            return StockQuote(
                code=index_code,
                name=self._INDEX_NAME_MAP.get(index_code, index_code),
                price=close,
                change_pct=change_pct,
                volume=float(r.get("volume", 0)),
                amount=0,
                high=float(r["high"]),
                low=float(r["low"]),
                open_price=float(r["open"]),
                prev_close=prev_close,
            )
        except Exception as e:
            logger.error("获取指数 %s 失败: %s", index_code, e)
            return None

    # ── 涨幅榜（新浪源 + 文件缓存）──

    async def get_stock_rank_list(self, count: int = 20) -> list[dict]:
        try:
            df = await _run_sync(ak.stock_zh_a_spot)
            df["涨跌幅"] = df["涨跌幅"].astype(float)
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
                    "turnover": 0,
                })
            _save_cache("stock_rank", result)
            return result
        except Exception as e:
            logger.warning("新浪涨幅榜不可用(%s)，尝试读取缓存", e)

        cached = _load_cache("stock_rank", max_age_days=1)
        if cached:
            return cached[:count]

        logger.error("涨幅榜无数据：接口和缓存均不可用")
        return []

    # ── 板块资金流向 ──

    async def get_sector_fund_flow(self, count: int = 10) -> list[dict]:
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

    # ── 内部降级方法 ──

    async def _quote_from_daily(self, code: str) -> StockQuote | None:
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
        d = d or date.today()
        try:
            df = await _run_sync(ak.tool_trade_date_hist_sina)
            trade_dates = set(df["trade_date"].astype(str))
            return d.strftime("%Y-%m-%d") in trade_dates
        except Exception as e:
            logger.warning("获取交易日历失败，默认为交易日: %s", e)
            return d.weekday() < 5
