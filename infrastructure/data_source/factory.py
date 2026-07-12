"""DataSourceManager 全局单例工厂

单一职责：组装所有 source + policy + repo，构造 DataSourceManager 实例。
不含业务逻辑，只做依赖装配。
"""

import logging
from pathlib import Path

from agent.config import PROJECT_ROOT
from domain.data_source_policy import DataSourcePolicy
from domain.models.data_source import DataSourceType
from infrastructure.akshare_client import AKShareClient
from infrastructure.data_source.akshare_source import (
    AkshareBidAskSource, AkshareFundFlowSource, AkshareHistSource,
    AkshareNewsSource, AkshareSectorFlowSource, AkshareSpotEmSource,
)
from infrastructure.data_source.base import DataSource
from infrastructure.data_source.circuit_breaker import CircuitBreaker
from infrastructure.data_source.manager import DataSourceManager
from infrastructure.data_source.tushare_source import (
    TushareDailySource, TushareMoneyFlowSource, TushareQuoteSource,
)

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "data_sources.yaml"

# 全局单例
_manager: DataSourceManager | None = None


def build_data_manager(
    config_path: Path | None = None,
) -> DataSourceManager:
    """构造并返回一个新的 DataSourceManager（不缓存）

    参数：
        config_path: 降级链配置文件路径，None 时用默认路径
    """
    cfg_path = config_path or _DEFAULT_CONFIG_PATH
    policy = DataSourcePolicy(cfg_path)

    akshare_client = AKShareClient()
    sources = _build_all_sources(policy, akshare_client)

    # 延迟 import 避免循环依赖
    from repository.data_source_call_log_repository import DataSourceCallLogRepository
    call_log_repo = DataSourceCallLogRepository()

    manager = DataSourceManager(policy, sources, call_log_repo)
    logger.info(
        "DataSourceManager 已构造: %d 个 source (%d 启用)",
        len(sources), sum(1 for s in sources.values() if s.enabled),
    )
    return manager


def get_data_manager() -> DataSourceManager:
    """全局单例访问（首次调用时懒加载）"""
    global _manager
    if _manager is None:
        _manager = build_data_manager()
    return _manager


def reload_data_manager() -> DataSourceManager:
    """重建单例 — 供 Admin 后台热重载配置调用"""
    global _manager
    _manager = build_data_manager()
    return _manager


# ─────────────── 具体 source 组装 ───────────────


def _build_all_sources(
    policy: DataSourcePolicy,
    akshare_client: AKShareClient,
) -> dict[DataSourceType, DataSource]:
    """按配置构建所有 source 实例（含各自的熔断器）"""
    sources: dict[DataSourceType, DataSource] = {}

    _register_akshare(sources, policy, akshare_client)
    _register_tushare(sources, policy)

    return sources


def _register_akshare(
    sources: dict[DataSourceType, DataSource],
    policy: DataSourcePolicy,
    akshare_client: AKShareClient,
) -> None:
    """注册 6 个 AKShare source"""
    ak_specs = [
        (DataSourceType.AKSHARE_BID_ASK,
         lambda b: AkshareBidAskSource(b, akshare_client)),
        (DataSourceType.AKSHARE_SPOT_EM,
         lambda b: AkshareSpotEmSource(b, akshare_client)),
        (DataSourceType.AKSHARE_HIST,
         lambda b: AkshareHistSource(b, akshare_client)),
        (DataSourceType.AKSHARE_FUND_FLOW,
         lambda b: AkshareFundFlowSource(b, akshare_client)),
        (DataSourceType.AKSHARE_SECTOR_FLOW,
         lambda b: AkshareSectorFlowSource(b, akshare_client)),
        (DataSourceType.AKSHARE_NEWS,
         lambda b: AkshareNewsSource(b, akshare_client)),
    ]
    for st, factory in ak_specs:
        breaker = _make_breaker(st, policy)
        sources[st] = factory(breaker)


def _register_tushare(
    sources: dict[DataSourceType, DataSource],
    policy: DataSourcePolicy,
) -> None:
    """注册 3 个 Tushare source（按 policy.tushare_enabled 决定 enabled）"""
    tushare_enabled = policy.is_tushare_enabled()

    ts_specs = [
        (DataSourceType.TUSHARE_QUOTE, TushareQuoteSource),
        (DataSourceType.TUSHARE_DAILY, TushareDailySource),
        (DataSourceType.TUSHARE_MONEY_FLOW, TushareMoneyFlowSource),
    ]
    for st, cls in ts_specs:
        breaker = _make_breaker(st, policy)
        sources[st] = cls(breaker, enabled=tushare_enabled)


def _make_breaker(
    source_type: DataSourceType,
    policy: DataSourcePolicy,
) -> CircuitBreaker:
    """按 policy 的熔断配置构造 CircuitBreaker"""
    cfg = policy.get_breaker_config(source_type)
    return CircuitBreaker(
        name=source_type.value,
        threshold=cfg["threshold"],
        cooldown=cfg["cooldown"],
    )
