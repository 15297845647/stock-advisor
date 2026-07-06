"""策略配置服务 — 读库合并默认值，带短 TTL 缓存，保存即刷新

供选股引擎与 admin 后台共用：admin 保存后刷新缓存，下次读取实时生效。
支持多策略（养家短线 yangjia / 中长线 midlong），按 key 分别缓存。
"""

import logging
import time

from domain.strategy.strategy_config import MidLongConfig, YangjiaConfig
from repository.strategy_config_repository import StrategyConfigRepository

logger = logging.getLogger(__name__)

_CACHE_TTL = 30.0  # 秒

_KEY_YANGJIA = "yangjia"
_KEY_MIDLONG = "midlong"


class ConfigService:
    # 类级共享缓存：admin 实例保存后，picker 实例也能立即读到新值
    # key -> (缓存时间, 配置对象)
    _cache: dict[str, tuple[float, object]] = {}

    def __init__(self):
        self.repo = StrategyConfigRepository()

    async def get_yangjia_config(self) -> YangjiaConfig:
        return await self._get(_KEY_YANGJIA, YangjiaConfig)

    async def save_yangjia_config(self, data: dict) -> YangjiaConfig:
        return await self._save(_KEY_YANGJIA, YangjiaConfig, data)

    async def get_midlong_config(self) -> MidLongConfig:
        return await self._get(_KEY_MIDLONG, MidLongConfig)

    async def save_midlong_config(self, data: dict) -> MidLongConfig:
        return await self._save(_KEY_MIDLONG, MidLongConfig, data)

    # ── 内部通用读写 ──

    async def _get(self, key: str, cls):
        """命中缓存直接返回，否则读库合并默认"""
        cached = ConfigService._cache.get(key)
        if cached and (time.monotonic() - cached[0]) < _CACHE_TTL:
            return cached[1]

        data = await self.repo.load(key)
        cfg = cls.from_dict(data)
        ConfigService._cache[key] = (time.monotonic(), cfg)
        return cfg

    async def _save(self, key: str, cls, data: dict):
        """合并默认值后整体写入，并立即刷新缓存"""
        cfg = cls.from_dict(data)
        await self.repo.save(key, cfg.to_dict())
        ConfigService._cache[key] = (time.monotonic(), cfg)
        return cfg
