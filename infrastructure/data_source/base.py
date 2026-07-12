"""DataSource 抽象基类 — 定义所有数据源必须实现的接口

单一职责：接口定义。所有具体 source 只关心自己支持的操作，
不支持的方法返回 None 或抛 NotImplementedError。
"""

from abc import ABC, abstractmethod

from domain.models.data_source import DataOperation, DataSourceType
from domain.models.stock import FundFlow, StockDailyBar, StockNews, StockQuote
from infrastructure.data_source.circuit_breaker import CircuitBreaker


class DataSource(ABC):
    """数据源抽象基类

    子类必须设置类属性：
        name              — 唯一标识（与 DataSourceType 枚举值一致）
        source_type       — DataSourceType 枚举
        supported_ops     — 支持的操作集合
    并实现 supported_ops 里声明的方法。
    """

    name: str = ""
    source_type: DataSourceType = None  # type: ignore
    supported_ops: set[DataOperation] = set()

    def __init__(self, breaker: CircuitBreaker, enabled: bool = True):
        self.breaker = breaker
        self.enabled = enabled

    # ── 支持性检查 ──

    def supports(self, operation: DataOperation) -> bool:
        """检查是否支持某操作"""
        return operation in self.supported_ops

    def is_available(self) -> bool:
        """综合可用性：启用 + 熔断未开"""
        return self.enabled and not self.breaker.is_open()

    # ── 数据获取接口（子类按需覆盖）──

    async def fetch_quote(self, code: str) -> StockQuote | None:
        """获取实时行情"""
        raise NotImplementedError

    async def fetch_kline(self, code: str, days: int = 30) -> list[StockDailyBar]:
        """获取日K线"""
        raise NotImplementedError

    async def fetch_fund_flow(self, code: str) -> list[FundFlow]:
        """获取个股资金流"""
        raise NotImplementedError

    async def fetch_sector_flow(self, count: int = 30) -> list[dict]:
        """获取板块资金流排名"""
        raise NotImplementedError

    async def fetch_news(self, code: str, limit: int = 20) -> list[StockNews]:
        """获取个股新闻"""
        raise NotImplementedError

    # ── 健康检查 ──

    @abstractmethod
    async def health_check(self) -> tuple[bool, str]:
        """
        主动探活 — 返回 (是否健康, 错误信息)
        子类实现，通常调一个轻量接口验证连通性。
        """
        ...
