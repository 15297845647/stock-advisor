"""Tushare 数据源适配器 — quote / daily / money_flow 三类

Tushare Pro 接口通过 pro_api，需要 token 且积分 >=2000。
数据源实例延迟初始化 pro 对象，token 每次重新读（支持后台热更新）。

拆成 3 个类保持单一职责：
    TushareQuoteSource       — 实时行情（stock）
    TushareDailySource       — 日K线
    TushareMoneyFlowSource   — 个股资金流
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from functools import partial

from agent.config import get_tushare_config
from domain.models.data_source import DataOperation, DataSourceType
from domain.models.stock import FundFlow, StockDailyBar, StockQuote
from infrastructure.data_source.base import DataSource
from infrastructure.data_source.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


def _to_ts_code(code: str) -> str:
    """A股6位代码转 Tushare 格式：600xxx.SH / 000xxx.SZ / 30xxx.SZ"""
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("0", "3", "2")):
        return f"{code}.SZ"
    return f"{code}.SZ"


class _TushareProProxy:
    """Tushare Pro 对象懒加载 — 每次读最新 token + http_url"""

    _pro = None
    _last_token: str = ""
    _last_http_url: str = ""

    @classmethod
    def get(cls):
        """返回可用的 pro_api 实例（token/http_url 变化时自动重建）"""
        cfg = get_tushare_config()
        token = cfg.get("token", "").strip()
        http_url = cfg.get("http_url", "").strip()

        if not token:
            raise ValueError("未配置 TUSHARE_TOKEN")

        # token 或 URL 变化时重建
        needs_rebuild = (
            cls._pro is None
            or cls._last_token != token
            or cls._last_http_url != http_url
        )

        if needs_rebuild:
            import tushare as ts
            cls._pro = ts.pro_api(token)
            # 若配置了自定义 http_url，覆盖 SDK 默认地址
            if http_url:
                cls._pro._DataApi__http_url = http_url
                logger.info("Tushare 使用自定义 API 地址: %s", http_url)
            cls._last_token = token
            cls._last_http_url = http_url

        return cls._pro


async def _run_pro(func_name: str, **params):
    """异步执行 pro_api.<func_name>(**params)，返回 DataFrame"""
    def _call():
        pro = _TushareProProxy.get()
        method = getattr(pro, func_name)
        return method(**params)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call)


# ─────────────────── QUOTE ───────────────────


class TushareQuoteSource(DataSource):
    """Tushare 实时行情源 — 备用于 AKShare 熔断时"""

    name = DataSourceType.TUSHARE_QUOTE.value
    source_type = DataSourceType.TUSHARE_QUOTE
    supported_ops = {DataOperation.QUOTE}

    def __init__(self, breaker: CircuitBreaker, enabled: bool = True):
        super().__init__(breaker, enabled)

    async def fetch_quote(self, code: str) -> StockQuote | None:
        """调 realtime_quote 拉最新报价（需要 Level-2 权限）"""
        ts_code = _to_ts_code(code)
        try:
            df = await _run_pro("realtime_quote", ts_code=ts_code)
        except Exception:
            # 无 realtime 权限时降级用日线取昨收 + 今开做伪 quote
            return await self._fallback_via_daily(code, ts_code)

        if df is None or df.empty:
            return await self._fallback_via_daily(code, ts_code)

        return self._df_to_quote(code, df)

    @staticmethod
    def _df_to_quote(code: str, df) -> StockQuote:
        """把 realtime_quote 的 DataFrame 转成 StockQuote"""
        r = df.iloc[0]
        return StockQuote(
            code=code,
            name=str(r.get("name", code)),
            price=float(r.get("price", 0) or 0),
            change_pct=float(r.get("pct_change", 0) or 0),
            volume=float(r.get("volume", 0) or 0),
            amount=float(r.get("amount", 0) or 0),
            high=float(r.get("high", 0) or 0),
            low=float(r.get("low", 0) or 0),
            open_price=float(r.get("open", 0) or 0),
            prev_close=float(r.get("pre_close", 0) or 0),
        )

    async def _fallback_via_daily(self, code: str, ts_code: str) -> StockQuote | None:
        """无实时权限时，用最近一日 daily 作为快照"""
        end = date.today()
        start = end - timedelta(days=10)
        try:
            df = await _run_pro(
                "daily",
                ts_code=ts_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as e:
            raise RuntimeError(f"tushare quote 兜底失败: {e}") from e

        if df is None or df.empty:
            return None

        df = df.sort_values("trade_date")
        r = df.iloc[-1]
        prev_close = float(r.get("pre_close", 0) or 0)
        close = float(r.get("close", 0) or 0)
        return StockQuote(
            code=code, name=code,
            price=close,
            change_pct=float(r.get("pct_chg", 0) or 0),
            volume=float(r.get("vol", 0) or 0),
            amount=float(r.get("amount", 0) or 0),
            high=float(r.get("high", 0) or 0),
            low=float(r.get("low", 0) or 0),
            open_price=float(r.get("open", 0) or 0),
            prev_close=prev_close,
        )

    async def health_check(self) -> tuple[bool, str]:
        try:
            q = await self.fetch_quote("000001")
            return (q is not None, "" if q else "empty")
        except Exception as e:
            return (False, str(e)[:200])


# ─────────────────── KLINE ───────────────────


class TushareDailySource(DataSource):
    """Tushare 日K线源"""

    name = DataSourceType.TUSHARE_DAILY.value
    source_type = DataSourceType.TUSHARE_DAILY
    supported_ops = {DataOperation.KLINE}

    def __init__(self, breaker: CircuitBreaker, enabled: bool = True):
        super().__init__(breaker, enabled)

    async def fetch_kline(self, code: str, days: int = 30) -> list[StockDailyBar]:
        """调 pro.daily 拉复权前后 N 天数据"""
        ts_code = _to_ts_code(code)
        end = date.today()
        start = end - timedelta(days=days + 30)

        try:
            df = await _run_pro(
                "daily",
                ts_code=ts_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as e:
            raise RuntimeError(f"tushare daily 失败 {code}: {e}") from e

        if df is None or df.empty:
            return []

        df = df.sort_values("trade_date").tail(days)
        return [self._row_to_bar(code, r) for _, r in df.iterrows()]

    @staticmethod
    def _row_to_bar(code: str, r) -> StockDailyBar:
        """DataFrame 行转 StockDailyBar"""
        date_str = str(r["trade_date"])
        trade_date = datetime.strptime(date_str, "%Y%m%d").date()
        return StockDailyBar(
            code=code, trade_date=trade_date,
            open=float(r["open"]), high=float(r["high"]),
            low=float(r["low"]), close=float(r["close"]),
            volume=float(r.get("vol", 0) or 0),
            amount=float(r.get("amount", 0) or 0) * 1000,  # tushare 单位是千元
            change_pct=float(r.get("pct_chg", 0) or 0),
        )

    async def health_check(self) -> tuple[bool, str]:
        try:
            bars = await self.fetch_kline("000001", days=3)
            return (len(bars) > 0, "" if bars else "empty")
        except Exception as e:
            return (False, str(e)[:200])


# ─────────────────── FUND_FLOW ───────────────────


class TushareMoneyFlowSource(DataSource):
    """Tushare 个股资金流"""

    name = DataSourceType.TUSHARE_MONEY_FLOW.value
    source_type = DataSourceType.TUSHARE_MONEY_FLOW
    supported_ops = {DataOperation.FUND_FLOW}

    def __init__(self, breaker: CircuitBreaker, enabled: bool = True):
        super().__init__(breaker, enabled)

    async def fetch_fund_flow(self, code: str) -> list[FundFlow]:
        """调 pro.moneyflow 拉近 N 天资金流"""
        ts_code = _to_ts_code(code)
        end = date.today()
        start = end - timedelta(days=15)

        try:
            df = await _run_pro(
                "moneyflow",
                ts_code=ts_code,
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
        except Exception as e:
            raise RuntimeError(f"tushare moneyflow 失败 {code}: {e}") from e

        if df is None or df.empty:
            return []

        df = df.sort_values("trade_date").tail(5)
        return [self._row_to_flow(code, r) for _, r in df.iterrows()]

    @staticmethod
    def _row_to_flow(code: str, r) -> FundFlow:
        """DataFrame 行转 FundFlow（tushare 金额单位：万元 → 元）"""
        trade_date = datetime.strptime(str(r["trade_date"]), "%Y%m%d").date()

        # tushare moneyflow 提供买卖净额（大小单）
        elg_net = float(r.get("net_mf_amount", 0) or 0) * 10000  # 净流入总额

        # 分档：xl(超大)/lg(大)/md(中)/sm(小)
        xl_net = (float(r.get("buy_elg_amount", 0) or 0)
                  - float(r.get("sell_elg_amount", 0) or 0)) * 10000
        lg_net = (float(r.get("buy_lg_amount", 0) or 0)
                  - float(r.get("sell_lg_amount", 0) or 0)) * 10000
        md_net = (float(r.get("buy_md_amount", 0) or 0)
                  - float(r.get("sell_md_amount", 0) or 0)) * 10000
        sm_net = (float(r.get("buy_sm_amount", 0) or 0)
                  - float(r.get("sell_sm_amount", 0) or 0)) * 10000

        # 主力 = 超大 + 大（若 net_mf_amount 为 0，则用组合计算）
        main = xl_net + lg_net if elg_net == 0 else elg_net

        return FundFlow(
            code=code, trade_date=trade_date,
            main_net_inflow=main,
            super_large_net=xl_net,
            large_net=lg_net,
            medium_net=md_net,
            small_net=sm_net,
        )

    async def health_check(self) -> tuple[bool, str]:
        try:
            flows = await self.fetch_fund_flow("000001")
            return (len(flows) > 0, "" if flows else "empty")
        except Exception as e:
            return (False, str(e)[:200])
