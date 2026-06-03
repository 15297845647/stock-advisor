"""AKShare 数据采集

数据源策略（基于 AKShare 文档）：
- 实时行情: stock_zh_a_spot_em（东方财富，稳定不封IP）
- 日K历史: stock_zh_a_hist（东方财富，含涨跌幅/换手率/成交额）
- 指数行情: index_zh_a_hist（东方财富，含涨跌幅）
- 资金流向: stock_individual_fund_flow（东方财富）
- 板块资金: stock_sector_fund_flow_rank（东方财富）
- 交易日历: tool_trade_date_hist_sina（新浪）

全量行情缓存 60s，避免重复请求。
所有接口自动重试 2 次。
"""

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path

import akshare as ak

from agent.config import AKSHARE_REQUEST_INTERVAL
from domain.models.stock import FundFlow, StockDailyBar, StockNews, StockQuote

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.environ.get("DB_PATH", "data/stock_advisor.db")).parent / "cache"

# ── 限频（按接口分组，互不阻塞）──

_rate_locks: dict[str, asyncio.Lock] = {}
_last_call_times: dict[str, float] = {}


def _get_lock(group: str) -> asyncio.Lock:
    if group not in _rate_locks:
        _rate_locks[group] = asyncio.Lock()
    return _rate_locks[group]


async def _throttle(group: str = "default"):
    lock = _get_lock(group)
    async with lock:
        now = time.monotonic()
        elapsed = now - _last_call_times.get(group, 0)
        if elapsed < AKSHARE_REQUEST_INTERVAL:
            await asyncio.sleep(AKSHARE_REQUEST_INTERVAL - elapsed)
        _last_call_times[group] = time.monotonic()


# ── 东财熔断器（云服务器反爬时自动跳过）──

class _CircuitBreaker:
    """简易熔断器：连续失败 N 次后，cooldown 秒内自动跳过"""
    def __init__(self, threshold: int = 3, cooldown: int = 300):
        self.threshold = threshold
        self.cooldown = cooldown
        self._failures = 0
        self._last_fail_time: float = 0

    def record_failure(self):
        self._failures += 1
        self._last_fail_time = time.monotonic()

    def record_success(self):
        self._failures = 0

    def is_open(self) -> bool:
        if self._failures < self.threshold:
            return False
        elapsed = time.monotonic() - self._last_fail_time
        if elapsed > self.cooldown:
            self._failures = 0
            return False
        return True


_eastmoney_breaker = _CircuitBreaker(threshold=3, cooldown=300)

_EASTMONEY_FUNCS = {
    "stock_zh_a_spot_em", "stock_zh_a_hist", "stock_individual_fund_flow",
    "stock_sector_fund_flow_rank", "index_zh_a_hist", "stock_news_em",
    "stock_individual_notice_report", "stock_individual_info_em",
}


def _is_eastmoney_func(func) -> bool:
    return getattr(func, "__name__", "") in _EASTMONEY_FUNCS


async def _run_sync(func, *args, group: str = "default", **kwargs):
    await _throttle(group)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


async def _run_with_retry(func, *args, group: str = "default", retries: int = 2, **kwargs):
    # 东财熔断：连续失败 3 次后 5 分钟内自动跳过
    if _eastmoney_breaker.is_open() and _is_eastmoney_func(func):
        raise ConnectionError(f"东财熔断中，跳过 {func.__name__}")

    for attempt in range(retries + 1):
        try:
            result = await _run_sync(func, *args, group=group, **kwargs)
            if _is_eastmoney_func(func):
                _eastmoney_breaker.record_success()
            return result
        except Exception as e:
            if _is_eastmoney_func(func):
                _eastmoney_breaker.record_failure()
            if attempt < retries:
                wait = (attempt + 1) * 2
                logger.warning("%s 失败(第%d次)，%ds后重试: %s", func.__name__, attempt + 1, wait, e)
                await asyncio.sleep(wait)
            else:
                raise


# ── 全量行情内存缓存（60s TTL）──

_spot_cache_data = None
_spot_cache_time: float = 0
_spot_cache_lock = asyncio.Lock()
_SPOT_CACHE_TTL = 60


async def _get_spot_df():
    """获取 A 股全量行情，60s 缓存，东财 → 新浪 fallback"""
    global _spot_cache_data, _spot_cache_time

    async with _spot_cache_lock:
        now = time.monotonic()
        if _spot_cache_data is not None and (now - _spot_cache_time) < _SPOT_CACHE_TTL:
            return _spot_cache_data

        # 优先东方财富（稳定不封IP）
        try:
            df = await _run_with_retry(ak.stock_zh_a_spot_em, group="spot")
            if df is not None and not df.empty:
                _spot_cache_data = df
                _spot_cache_time = time.monotonic()
                logger.info("全量行情已刷新(东财源)，共 %d 条", len(df))
                return df
        except Exception as e:
            logger.warning("东财实时行情失败: %s，尝试新浪源", e)

        # 降级新浪（注意：频繁调用会被封IP）
        try:
            df = await _run_with_retry(ak.stock_zh_a_spot, group="spot_sina")
            if df is not None and not df.empty:
                _spot_cache_data = df
                _spot_cache_time = time.monotonic()
                logger.info("全量行情已刷新(新浪源)，共 %d 条", len(df))
                return df
        except Exception as e:
            logger.error("新浪实时行情也失败: %s", e)

        # 两源都挂：返回上一次缓存（即使过期）
        if _spot_cache_data is not None:
            logger.warning("行情源均不可用，使用过期缓存")
            return _spot_cache_data

        import pandas as pd
        logger.error("所有实时行情源均不可用，返回空数据")
        return pd.DataFrame()


# ── 文件缓存 ──

def _save_cache(name: str, data: list[dict]):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"date": date.today().isoformat(), "data": data}
    (_CACHE_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _load_cache(name: str, max_age_days: int = 1) -> list[dict]:
    path = _CACHE_DIR / f"{name}.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cached_date = date.fromisoformat(payload["date"])
        if (date.today() - cached_date).days <= max_age_days:
            return payload["data"]
    except Exception:
        pass
    return []


def _col(df, *candidates) -> str | None:
    """从 DataFrame 列名中匹配第一个存在的候选名（防 AKShare 上游改列名）"""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _col_val(row, *candidates, default=0):
    """从行数据中取第一个匹配的列值"""
    for c in candidates:
        if c in row.index:
            v = row[c]
            try:
                return float(v)
            except (ValueError, TypeError):
                return v
    return default


class AKShareClient:

    # ── 个股日K（东财 → 腾讯 fallback）──

    async def get_stock_history(self, code: str, days: int = 60) -> list[StockDailyBar]:
        end_date = date.today()
        start_date = end_date - timedelta(days=days + 30)

        # 优先东财（字段完整：含涨跌幅/成交额/换手率）
        bars = await self._hist_from_eastmoney(code, start_date, end_date, days)
        if bars:
            return bars

        # 降级腾讯（缺涨跌幅，需手动算）
        logger.info("%s 东财日K失败，尝试腾讯源", code)
        return await self._hist_from_tencent(code, start_date, end_date, days)

    async def _hist_from_eastmoney(self, code, start_date, end_date, days) -> list[StockDailyBar]:
        try:
            df = await _run_with_retry(
                ak.stock_zh_a_hist,
                symbol=code, period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq", group="hist",
            )
        except Exception as e:
            logger.warning("东财日K %s 失败: %s", code, e)
            return []

        if df is None or df.empty:
            return []

        bars = []
        for _, row in df.tail(days).iterrows():
            trade_date = row["日期"]
            if isinstance(trade_date, str):
                trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
            elif isinstance(trade_date, datetime):
                trade_date = trade_date.date()
            bars.append(StockDailyBar(
                code=code, trade_date=trade_date,
                open=float(row["开盘"]), high=float(row["最高"]),
                low=float(row["最低"]), close=float(row["收盘"]),
                volume=float(row.get("成交量", 0)),
                amount=float(row.get("成交额", 0)),
                change_pct=float(row.get("涨跌幅", 0)),
            ))
        return bars

    async def _hist_from_tencent(self, code, start_date, end_date, days) -> list[StockDailyBar]:
        symbol = f"sh{code}" if code.startswith(("6", "9")) else f"sz{code}"
        try:
            df = await _run_with_retry(
                ak.stock_zh_a_hist_tx,
                symbol=symbol,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq", group="hist_tx",
            )
        except Exception as e:
            logger.error("腾讯日K %s 也失败: %s", code, e)
            return []

        if df is None or df.empty:
            return []

        bars = []
        prev_close = None
        for _, row in df.tail(days).iterrows():
            close = float(row["close"])
            change_pct = 0.0
            if prev_close and prev_close != 0:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            prev_close = close

            trade_date = row["date"]
            if isinstance(trade_date, str):
                trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
            elif isinstance(trade_date, datetime):
                trade_date = trade_date.date()
            bars.append(StockDailyBar(
                code=code, trade_date=trade_date,
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=close,
                volume=float(row.get("amount", 0)), amount=0,
                change_pct=change_pct,
            ))
        return bars

    # ── 个股实时行情（东财源 + 日K降级）──

    async def get_realtime_quote(self, code: str) -> StockQuote | None:
        try:
            df = await _get_spot_df()
            code_col = _col(df, "代码", "code", "symbol", "股票代码")
            if not code_col:
                logger.error("行情 DataFrame 无可识别的代码列: %s", list(df.columns)[:10])
                return await self._quote_from_daily(code)

            row = df[df[code_col] == code]
            if row.empty:
                return await self._quote_from_daily(code)
            r = row.iloc[0]
            return StockQuote(
                code=code,
                name=str(_col_val(r, "名称", "name", "股票名称", default=code)),
                price=_col_val(r, "最新价", "现价", "price", "trade", "close"),
                change_pct=_col_val(r, "涨跌幅", "pct_chg", "changepercent"),
                volume=_col_val(r, "成交量", "volume"),
                amount=_col_val(r, "成交额", "amount"),
                high=_col_val(r, "最高", "high"),
                low=_col_val(r, "最低", "low"),
                open_price=_col_val(r, "今开", "open"),
                prev_close=_col_val(r, "昨收", "pre_close", "settlement"),
            )
        except Exception as e:
            logger.warning("实时行情获取失败(%s): %s", code, e)
        return await self._quote_from_daily(code)

    # ── 个股资金流向 ──

    async def get_fund_flow(self, code: str) -> list[FundFlow]:
        try:
            df = await _run_with_retry(
                ak.stock_individual_fund_flow,
                stock=code,
                market="sh" if code.startswith("6") else "sz",
                group="fund",
            )
        except Exception as e:
            logger.error("获取 %s 资金流向失败: %s", code, e)
            return []

        if df is None or df.empty:
            return []

        flows = []
        for _, row in df.tail(5).iterrows():
            trade_date = row["日期"]
            if isinstance(trade_date, datetime):
                trade_date = trade_date.date()
            flows.append(
                FundFlow(
                    code=code,
                    trade_date=trade_date,
                    main_net_inflow=float(row.get("主力净流入-净额", 0)),
                    super_large_net=float(row.get("超大单净流入-净额", 0)),
                    large_net=float(row.get("大单净流入-净额", 0)),
                    medium_net=float(row.get("中单净流入-净额", 0)),
                    small_net=float(row.get("小单净流入-净额", 0)),
                )
            )
        return flows

    # ── 个股新闻（东方财富）──

    async def get_stock_news(self, code: str, limit: int = 20) -> list[StockNews]:
        """获取个股新闻 + 公告，合并返回"""
        news_list: list[StockNews] = []

        # 新闻
        try:
            df = await _run_with_retry(ak.stock_news_em, symbol=code, group="news")
            if df is not None and not df.empty:
                for _, row in df.head(limit).iterrows():
                    news_list.append(StockNews(
                        title=str(row.get("新闻标题", row.get("title", ""))),
                        source=str(row.get("新闻来源", row.get("source", ""))),
                        time=str(row.get("发布时间", row.get("time", ""))),
                        url=str(row.get("新闻链接", row.get("url", ""))),
                        news_type="news",
                    ))
        except Exception as e:
            logger.warning("获取 %s 新闻失败: %s", code, e)

        # 个股公告（stock_individual_notice_report，需 security 参数）
        try:
            notice_func = getattr(ak, "stock_individual_notice_report", None)
            if notice_func:
                df = await _run_with_retry(
                    notice_func, security=code, symbol="全部", group="news",
                )
                if df is not None and not df.empty:
                    for _, row in df.head(10).iterrows():
                        news_list.append(StockNews(
                            title=str(row.get("公告标题", row.get("title", ""))),
                            source="公司公告",
                            time=str(row.get("公告日期", row.get("date", ""))),
                            url=str(row.get("公告链接", row.get("url", ""))),
                            news_type="announcement",
                        ))
        except Exception as e:
            logger.warning("获取 %s 公告失败（非关键）: %s", code, e)

        return news_list

    # ── 指数行情（东方财富，含涨跌幅）──

    _INDEX_NAME_MAP = {
        "000001": "上证指数",
        "399001": "深证成指",
        "399006": "创业板指",
    }

    _INDEX_SYMBOL_MAP = {
        "000001": "sh000001", "399001": "sz399001", "399006": "sz399006",
    }

    async def get_market_index(self, index_code: str = "000001") -> StockQuote | None:
        """指数行情：东财 → 新浪 fallback"""
        end_date = date.today().strftime("%Y%m%d")
        start_date = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
        name = self._INDEX_NAME_MAP.get(index_code, index_code)

        # 优先东财（含涨跌幅）
        try:
            df = await _run_with_retry(
                ak.index_zh_a_hist, symbol=index_code, period="daily",
                start_date=start_date, end_date=end_date, group="index",
            )
            if df is not None and not df.empty:
                r = df.iloc[-1]
                return StockQuote(
                    code=index_code, name=name,
                    price=float(r["收盘"]), change_pct=float(r.get("涨跌幅", 0)),
                    volume=float(r.get("成交量", 0)), amount=float(r.get("成交额", 0)),
                    high=float(r["最高"]), low=float(r["最低"]),
                    open_price=float(r["开盘"]),
                    prev_close=float(r["收盘"]) - float(r.get("涨跌额", 0)),
                )
        except Exception as e:
            logger.warning("东财指数 %s 失败: %s，尝试新浪", index_code, e)

        # 降级新浪
        symbol = self._INDEX_SYMBOL_MAP.get(index_code, f"sh{index_code}")
        try:
            df = await _run_with_retry(
                ak.stock_zh_index_daily, symbol=symbol, group="index_sina",
            )
            if df is not None and not df.empty:
                r = df.iloc[-1]
                prev = df.iloc[-2] if len(df) >= 2 else r
                prev_close = float(prev["close"])
                close = float(r["close"])
                pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else 0
                return StockQuote(
                    code=index_code, name=name, price=close, change_pct=pct,
                    volume=float(r.get("volume", 0)), amount=0,
                    high=float(r["high"]), low=float(r["low"]),
                    open_price=float(r["open"]), prev_close=prev_close,
                )
        except Exception as e:
            logger.error("新浪指数 %s 也失败: %s", index_code, e)

        return None

    # ── 涨幅榜（复用全量缓存 + 文件降级）──

    async def get_stock_rank_list(self, count: int = 20) -> list[dict]:
        try:
            df = await _get_spot_df()
            df = df.copy()
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
                    "turnover": float(r.get("换手率", 0)),
                })
            _save_cache("stock_rank", result)
            return result
        except Exception as e:
            logger.warning("涨幅榜不可用(%s)，尝试缓存", e)

        cached = _load_cache("stock_rank", max_age_days=1)
        if cached:
            return cached[:count]
        return []

    # ── 市场广度统计（涨跌家数 + 涨跌停）──

    async def get_market_breadth(self) -> dict:
        """从全量行情统计涨跌家数和涨跌停"""
        try:
            df = await _get_spot_df()
            chg_col = _col(df, "涨跌幅", "pct_chg", "changepercent")
            if not chg_col:
                return {"rise": 0, "fall": 0, "limit_up": 0, "limit_down": 0}

            df[chg_col] = df[chg_col].astype(float)
            rise = int((df[chg_col] > 0).sum())
            fall = int((df[chg_col] < 0).sum())
            limit_up = int((df[chg_col] >= 9.9).sum())
            limit_down = int((df[chg_col] <= -9.9).sum())

            return {
                "rise": rise, "fall": fall,
                "limit_up": limit_up, "limit_down": limit_down,
            }
        except Exception as e:
            logger.warning("市场广度统计失败: %s", e)
            return {"rise": 0, "fall": 0, "limit_up": 0, "limit_down": 0}

    # ── 板块资金流向（修正列名）──

    async def get_sector_fund_flow(self, count: int = 10) -> list[dict]:
        try:
            df = await _run_with_retry(
                ak.stock_sector_fund_flow_rank,
                indicator="今日",
                sector_type="行业资金流",
                group="sector",
            )
            if df is None or df.empty:
                return []
            result = []
            for _, r in df.head(count).iterrows():
                result.append({
                    "name": str(r.get("名称", "")),
                    "change_pct": float(r.get("今日涨跌幅", 0)),
                    "main_net_inflow": float(r.get("主力净流入-净额", 0)),
                })
            return result
        except Exception as e:
            logger.warning("板块资金流向接口不可用: %s", e)
            return []

    # ── 个股基本面（东财 + 新浪财务摘要）──

    async def get_fundamentals(self, code: str) -> 'StockFundamental | None':
        """获取个股基本面数据：stock_individual_info_em + stock_financial_abstract"""
        from domain.models.stock import StockFundamental

        info = {}

        # 1. 东财个股信息（总市值/流通市值/行业）
        try:
            df = await _run_with_retry(ak.stock_individual_info_em, symbol=code, group="info")
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    item = str(row.get("item", ""))
                    value = row.get("value", "")
                    if item == "总市值":
                        info["total_market_cap"] = float(value) if value else 0
                    elif item == "流通市值":
                        info["circulating_cap"] = float(value) if value else 0
                    elif item == "行业":
                        info["industry"] = str(value)
                    elif item == "股票简称":
                        info["name"] = str(value)
        except Exception as e:
            logger.warning("东财个股信息 %s 失败: %s", code, e)

        # 2. 新浪财务摘要（PE/PB/ROE/营收/净利润）
        try:
            df = await _run_with_retry(ak.stock_financial_abstract, symbol=code, group="finance")
            if df is not None and not df.empty:
                latest = df.iloc[0]
                info["report_period"] = str(latest.get("报告期", ""))
                info["revenue"] = float(latest.get("营业收入", 0) or 0)
                info["revenue_growth"] = float(latest.get("营业收入同比增长", 0) or 0)
                info["net_profit"] = float(latest.get("净利润", 0) or 0)
                info["profit_growth"] = float(latest.get("净利润同比增长", 0) or 0)
                info["eps"] = float(latest.get("每股收益", 0) or 0)
                info["roe"] = float(latest.get("净资产收益率", 0) or 0)
                info["bvps"] = float(latest.get("每股净资产", 0) or 0)
        except Exception as e:
            logger.warning("财务摘要 %s 失败（非关键）: %s", code, e)

        # 3. 从实时行情补 PE/PB
        try:
            quote = await self.get_realtime_quote(code)
            if quote and info.get("eps") and info["eps"] != 0:
                info["pe_ratio"] = round(quote.price / info["eps"], 2)
            if quote and info.get("bvps") and info["bvps"] != 0:
                info["pb_ratio"] = round(quote.price / info["bvps"], 2)
        except Exception:
            pass

        if not info:
            return None

        return StockFundamental(
            code=code,
            name=info.get("name", code),
            total_market_cap=info.get("total_market_cap", 0),
            circulating_cap=info.get("circulating_cap", 0),
            industry=info.get("industry", ""),
            pe_ratio=info.get("pe_ratio", 0),
            pb_ratio=info.get("pb_ratio", 0),
            roe=info.get("roe", 0),
            revenue=info.get("revenue", 0),
            revenue_growth=info.get("revenue_growth", 0),
            net_profit=info.get("net_profit", 0),
            profit_growth=info.get("profit_growth", 0),
            eps=info.get("eps", 0),
            bvps=info.get("bvps", 0),
            debt_ratio=info.get("debt_ratio", 0),
            report_period=info.get("report_period", ""),
        )

    # ── 数据一致性验证 ──

    async def verify_quote_consistency(self, code: str) -> dict:
        """多源交叉验证行情数据"""
        results = {}

        # 东财源
        try:
            df = await _run_with_retry(ak.stock_zh_a_spot_em, group="spot")
            row = df[df["代码"] == code]
            if not row.empty:
                results["eastmoney"] = float(row.iloc[0]["最新价"])
        except Exception:
            pass

        # 腾讯日K最新
        try:
            bars = await self._hist_from_tencent(code, date.today() - timedelta(days=5), date.today(), 1)
            if bars:
                results["tencent"] = bars[-1].close
        except Exception:
            pass

        # 比较
        prices = list(results.values())
        consistent = True
        max_diff_pct = 0
        if len(prices) >= 2:
            max_diff_pct = abs(prices[0] - prices[1]) / prices[0] * 100
            consistent = max_diff_pct < 2  # 2% tolerance

        return {
            "sources": results,
            "consistent": consistent,
            "max_diff_pct": round(max_diff_pct, 2),
        }

    # ── 期货行情（新浪源）──

    # 期货品种别名 → 合约代码前缀
    _FUTURES_ALIAS: dict[str, tuple[str, str]] = {
        "欧线": ("EC0", "集运指数(欧线)"), "集运": ("EC0", "集运指数(欧线)"),
        "欧线集运": ("EC0", "集运指数(欧线)"), "集运指数": ("EC0", "集运指数(欧线)"),
        "螺纹": ("RB0", "螺纹钢"), "螺纹钢": ("RB0", "螺纹钢"),
        "铁矿": ("I0", "铁矿石"), "铁矿石": ("I0", "铁矿石"),
        "原油": ("SC0", "原油"), "黄金": ("AU0", "黄金"),
        "白银": ("AG0", "白银"), "铜": ("CU0", "铜"),
        "豆粕": ("M0", "豆粕"), "棕榈油": ("P0", "棕榈油"),
        "焦煤": ("JM0", "焦煤"), "焦炭": ("J0", "焦炭"),
        "甲醇": ("MA0", "甲醇"), "PTA": ("TA0", "PTA"),
        "纯碱": ("SA0", "纯碱"), "玻璃": ("FG0", "玻璃"),
        "沪深300": ("IF0", "沪深300股指"), "上证50": ("IH0", "上证50股指"),
    }

    def resolve_futures_code(self, text: str) -> tuple[str, str] | None:
        """从文本识别期货品种，返回 (合约代码, 品种名) 或 None"""
        for alias, (code, name) in self._FUTURES_ALIAS.items():
            if alias in text:
                return code, name
        # 尝试直接匹配合约代码格式（如 EC2408, RB2410, EC0）
        import re
        m = re.search(r"\b([A-Za-z]{1,3}\d{0,4})\b", text)
        if m:
            raw = m.group(1).upper()
            # 检查是否是已知品种前缀
            for _, (code, name) in self._FUTURES_ALIAS.items():
                prefix = re.match(r"[A-Z]+", code).group()
                if raw.startswith(prefix):
                    return raw if len(raw) > len(prefix) else code, name
        return None

    async def get_futures_quote(self, symbol: str, name: str = "") -> StockQuote | None:
        """获取期货实时行情"""
        try:
            df = await _run_with_retry(
                ak.futures_zh_spot, symbol=symbol, market="CF", adjust="0",
                group="futures",
            )
            if df is None or df.empty:
                return None
            r = df.iloc[0]
            current = float(r.get("current_price", 0))
            last_close = float(r.get("last_close", current))
            change_pct = round((current - last_close) / last_close * 100, 2) if last_close else 0
            return StockQuote(
                code=symbol, name=name or str(r.get("symbol", symbol)),
                price=current, change_pct=change_pct,
                volume=float(r.get("buy_vol", 0)) + float(r.get("sell_vol", 0)),
                amount=0, high=float(r.get("high", 0)), low=float(r.get("low", 0)),
                open_price=float(r.get("open", 0)), prev_close=last_close,
            )
        except Exception as e:
            logger.warning("期货实时行情 %s 失败: %s", symbol, e)
            return await self._futures_quote_from_daily(symbol, name)

    async def get_futures_history(self, symbol: str, days: int = 60) -> list[StockDailyBar]:
        """获取期货日K（新浪源，连续合约用品种代码+0如EC0）"""
        try:
            df = await _run_with_retry(
                ak.futures_zh_daily_sina, symbol=symbol, group="futures_hist",
            )
        except Exception as e:
            logger.error("期货日K %s 失败: %s", symbol, e)
            return []

        if df is None or df.empty:
            return []

        bars = []
        prev_close = None
        for _, row in df.tail(days).iterrows():
            trade_date = row["date"]
            if isinstance(trade_date, str):
                trade_date = datetime.strptime(trade_date, "%Y-%m-%d").date()
            elif isinstance(trade_date, datetime):
                trade_date = trade_date.date()

            close = float(row["close"])
            change_pct = 0.0
            if prev_close and prev_close != 0:
                change_pct = round((close - prev_close) / prev_close * 100, 2)
            prev_close = close

            bars.append(StockDailyBar(
                code=symbol, trade_date=trade_date,
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=close,
                volume=float(row.get("volume", 0)), amount=0,
                change_pct=change_pct,
            ))
        return bars

    async def _futures_quote_from_daily(self, symbol: str, name: str = "") -> StockQuote | None:
        bars = await self.get_futures_history(symbol, days=2)
        if not bars:
            return None
        latest = bars[-1]
        prev_close = bars[-2].close if len(bars) >= 2 else latest.close
        return StockQuote(
            code=symbol, name=name or symbol,
            price=latest.close, change_pct=latest.change_pct,
            volume=latest.volume, amount=0,
            high=latest.high, low=latest.low,
            open_price=latest.open, prev_close=prev_close,
        )

    # ── 内部降级 ──

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
            df = await _run_with_retry(ak.tool_trade_date_hist_sina, group="calendar")
            trade_dates = set(df["trade_date"].astype(str))
            return d.strftime("%Y-%m-%d") in trade_dates
        except Exception as e:
            logger.warning("交易日历获取失败，默认按工作日: %s", e)
            return d.weekday() < 5
