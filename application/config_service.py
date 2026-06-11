"""策略配置服务 — 读库合并默认值，带短 TTL 缓存，保存即刷新

供选股引擎与 admin 后台共用：admin 保存后清缓存，下次读取实时生效。
"""

import logging
import time

from domain.strategy.strategy_config import YangjiaConfig
from repository.strategy_config_repository import StrategyConfigRepository

logger = logging.getLogger(__name__)

_CACHE_TTL = 30.0  # 秒


class ConfigService:
    # 类级共享缓存：admin 实例保存后，picker 实例也能立即读到新值
    _cache: YangjiaConfig | None = None
    _cache_time: float = 0.0

    def __init__(self):
        self.repo = StrategyConfigRepository()

    async def get_yangjia_config(self) -> YangjiaConfig:
        """获取养家策略配置（命中缓存直接返回，否则读库合并默认）"""
        now = time.monotonic()
        if ConfigService._cache is not None and (now - ConfigService._cache_time) < _CACHE_TTL:
            return ConfigService._cache

        data = await self.repo.load()
        cfg = YangjiaConfig.from_dict(data)
        ConfigService._cache = cfg
        ConfigService._cache_time = now
        return cfg

    async def save_yangjia_config(self, data: dict) -> YangjiaConfig:
        """保存配置（合并默认值后整体写入），并立即刷新缓存"""
        cfg = YangjiaConfig.from_dict(data)
        await self.repo.save(cfg.to_dict())
        ConfigService._cache = cfg
        ConfigService._cache_time = time.monotonic()
        return cfg
