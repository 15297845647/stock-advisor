"""AKShare 数据源适配器 — 每个适配器只负责一类接口

不同类型接口拆成独立类，避免 God Object：
    AkshareBidAskSource      — 五档报价（QUOTE，优先级最高）
    AkshareSpotEmSource      — 东财全量快照（QUOTE 备胎）
    AkshareHistSource        — 日K线（KLINE + QUOTE 兜底取昨收）
    AkshareFundFlowSource    — 个股资金流
    AkshareSectorFlowSource  — 板块资金流
    AkshareNewsSource        — 新闻/公告

策略：委托给现有 AKShareClient 具体实现，本层只做接口适配 + 熔断埋点。
"""

import asyncio
import logging
from datetime import date, datetime, timedelta
from functools import partial

import akshare as ak

from domain.models.data_source import DataOperation, DataSourceType
from domain.models.stock import FundFlow, StockDailyBar, StockNews, StockQuote
from infrastructure.akshare_client import AKShareClient
from infrastructure.data_source.base import DataSource
from infrastructure.data_source.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


# ─────────────────── QUOTE ───────────────────


class AkshareBidAskSource(DataSource):
    """基于 stock_bid_ask_em 的五档报价源 — QUOTE 首选"""

    name = DataSourceType.AKSHARE_BID_ASK.value
    source_type = DataSourceType.AKSHARE_BID_ASK
    supported_ops = {DataOperation.QUOTE}

    def __init__(
        self, breaker: CircuitBreaker,
        akshare_client: AKShareClient,
        enabled: bool = True,
    ):
        super().__init__(breaker, enabled)
        self._name_cache: dict[str, str] = {}
        self._client = akshare_client  # 用于名称查询兜底（复用 spot 缓存）

    async def fetch_quote(self, code: str) -> StockQuote | None:
        """调 stock_bid_ask_em 拉五档，转成 StockQuote"""
        try:
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(
                None, partial(ak.stock_bid_ask_em, symbol=code)
            )
            if df is None or df.empty:
                return None

            info = self._parse_bid_ask_df(df)
            if info is None:
                return None

            name = await self._resolve_name(code)
            return self._build_quote(code, name, info)
        except Exception as e:
            raise RuntimeError(f"bid_ask 拉取 {code} 失败: {e}") from e

    @staticmethod
    def _parse_bid_ask_df(df) -> dict | None:
        """stock_bid_ask_em 返回列为 item/value 的键值对，转 dict"""
        info: dict = {}
        for _, row in df.iterrows():
            key = str(row.get("item", ""))
            val = row.get("value", 0)
            info[key] = val
        if not info.get("最新"):
            return None
        return info

    async def _resolve_name(self, code: str) -> str:
        """通过 spot_em 全量快照反查名称（带缓存，失败退回 code）"""
        if code in self._name_cache:
            return self._name_cache[code]
        try:
            quote = await self._client.get_realtime_quote(code)
            name = quote.name if quote and quote.name else code
        except Exception:
            name = code
        self._name_cache[code] = name
        return name

    @staticmethod
    def _build_quote(code: str, name: str, info: dict) -> StockQuote:
        """把 bid_ask dict 组装成 StockQuote"""
        return StockQuote(
            code=code, name=name,
            price=float(info.get("最新", 0) or 0),
            change_pct=float(info.get("涨幅", 0) or 0),
            volume=float(info.get("总手", 0) or 0),
            amount=float(info.get("金额", 0) or 0),
            high=float(info.get("最高", 0) or 0),
            low=float(info.get("最低", 0) or 0),
            open_price=float(info.get("今开", 0) or 0),
            prev_close=float(info.get("昨收", 0) or 0),
        )

    async def health_check(self) -> tuple[bool, str]:
        """用平安银行 000001 探活"""
        try:
            q = await self.fetch_quote("000001")
            return (q is not None, "" if q else "empty response")
        except Exception as e:
            return (False, str(e)[:200])


class AkshareSpotEmSource(DataSource):
    """基于全量 spot_em 快照的行情源 — QUOTE 备胎"""

    name = DataSourceType.AKSHARE_SPOT_EM.value
    source_type = DataSourceType.AKSHARE_SPOT_EM
    supported_ops = {DataOperation.QUOTE}

    def __init__(
        self, breaker: CircuitBreaker,
        akshare_client: AKShareClient,
        enabled: bool = True,
    ):
        super().__init__(breaker, enabled)
        self.client = akshare_client

    async def fetch_quote(self, code: str) -> StockQuote | None:
        """复用 AKShareClient 的全量快照缓存逻辑"""
        return await self.client.get_realtime_quote(code)

    async def health_check(self) -> tuple[bool, str]:
        try:
            q = await self.fetch_quote("000001")
            return (q is not None, "" if q else "empty")
        except Exception as e:
            return (False, str(e)[:200])


# ─────────────────── KLINE ───────────────────


class AkshareHistSource(DataSource):
    """基于日K线的数据源 — 主 KLINE，兜底 QUOTE（取昨收）"""

    name = DataSourceType.AKSHARE_HIST.value
    source_type = DataSourceType.AKSHARE_HIST
    supported_ops = {DataOperation.KLINE, DataOperation.QUOTE}

    def __init__(
        self, breaker: CircuitBreaker,
        akshare_client: AKShareClient,
        enabled: bool = True,
    ):
        super().__init__(breaker, enabled)
        self.client = akshare_client

    async def fetch_kline(self, code: str, days: int = 30) -> list[StockDailyBar]:
        """复用 AKShareClient 的多层降级日K逻辑"""
        return await self.client.get_stock_history(code, days=days)

    async def fetch_quote(self, code: str) -> StockQuote | None:
        """兜底：日K末位组装 quote（仅有昨收+今收，非实时）"""
        bars = await self.fetch_kline(code, days=2)
        if not bars:
            return None
        last = bars[-1]
        prev = bars[-2] if len(bars) > 1 else last
        return StockQuote(
            code=code, name=code,
            price=last.close, change_pct=last.change_pct,
            volume=last.volume, amount=last.amount,
            high=last.high, low=last.low,
            open_price=last.open, prev_close=prev.close,
        )

    async def health_check(self) -> tuple[bool, str]:
        try:
            bars = await self.fetch_kline("000001", days=5)
            return (len(bars) > 0, "" if bars else "empty")
        except Exception as e:
            return (False, str(e)[:200])


# ─────────────────── FUND_FLOW ───────────────────


class AkshareFundFlowSource(DataSource):
    """个股资金流数据源"""

    name = DataSourceType.AKSHARE_FUND_FLOW.value
    source_type = DataSourceType.AKSHARE_FUND_FLOW
    supported_ops = {DataOperation.FUND_FLOW}

    def __init__(
        self, breaker: CircuitBreaker,
        akshare_client: AKShareClient,
        enabled: bool = True,
    ):
        super().__init__(breaker, enabled)
        self.client = akshare_client

    async def fetch_fund_flow(self, code: str) -> list[FundFlow]:
        return await self.client.get_fund_flow(code)

    async def health_check(self) -> tuple[bool, str]:
        try:
            flows = await self.fetch_fund_flow("000001")
            return (len(flows) > 0, "" if flows else "empty")
        except Exception as e:
            return (False, str(e)[:200])


# ─────────────────── SECTOR_FLOW ───────────────────


class AkshareSectorFlowSource(DataSource):
    """板块资金流数据源"""

    name = DataSourceType.AKSHARE_SECTOR_FLOW.value
    source_type = DataSourceType.AKSHARE_SECTOR_FLOW
    supported_ops = {DataOperation.SECTOR_FLOW}

    def __init__(
        self, breaker: CircuitBreaker,
        akshare_client: AKShareClient,
        enabled: bool = True,
    ):
        super().__init__(breaker, enabled)
        self.client = akshare_client

    async def fetch_sector_flow(self, count: int = 30) -> list[dict]:
        return await self.client.get_sector_fund_flow(count=count)

    async def health_check(self) -> tuple[bool, str]:
        try:
            data = await self.fetch_sector_flow(count=5)
            return (len(data) > 0, "" if data else "empty")
        except Exception as e:
            return (False, str(e)[:200])


# ─────────────────── NEWS ───────────────────


class AkshareNewsSource(DataSource):
    """新闻 + 公告数据源"""

    name = DataSourceType.AKSHARE_NEWS.value
    source_type = DataSourceType.AKSHARE_NEWS
    supported_ops = {DataOperation.NEWS}

    def __init__(
        self, breaker: CircuitBreaker,
        akshare_client: AKShareClient,
        enabled: bool = True,
    ):
        super().__init__(breaker, enabled)
        self.client = akshare_client

    async def fetch_news(self, code: str, limit: int = 20) -> list[StockNews]:
        return await self.client.get_stock_news(code, limit=limit)

    async def health_check(self) -> tuple[bool, str]:
        try:
            news = await self.fetch_news("000001", limit=3)
            return (len(news) > 0, "" if news else "empty")
        except Exception as e:
            return (False, str(e)[:200])
