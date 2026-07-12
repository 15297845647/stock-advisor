"""降级策略 — 从 yaml 加载各 operation 的 source 优先级链

单一职责：策略解析 + 查询，无网络/数据源调用。
"""

import logging
from pathlib import Path
from typing import Any

import yaml

from domain.models.data_source import DataOperation, DataSourceType

logger = logging.getLogger(__name__)


class DataSourcePolicy:
    """降级链策略容器 — 加载 yaml、按 operation 返回 source 顺序"""

    def __init__(self, config_path: Path):
        self._config_path = config_path
        self._chains: dict[DataOperation, list[DataSourceType]] = {}
        self._breaker_configs: dict[DataSourceType, dict[str, int]] = {}
        self._tushare_enabled: bool = False
        self._tushare_token_env: str = "TUSHARE_TOKEN"
        self.reload()

    # ── 公开接口 ──

    def get_chain(self, operation: DataOperation) -> list[DataSourceType]:
        """返回该操作的降级链（按优先级从高到低）"""
        return list(self._chains.get(operation, []))

    def get_breaker_config(self, source_type: DataSourceType) -> dict[str, int]:
        """返回指定 source 的熔断器配置（默认 threshold=3 cooldown=300）"""
        return self._breaker_configs.get(
            source_type, {"threshold": 3, "cooldown": 300}
        )

    def is_tushare_enabled(self) -> bool:
        """Tushare 是否启用"""
        return self._tushare_enabled

    def get_tushare_token_env(self) -> str:
        """Tushare token 环境变量名"""
        return self._tushare_token_env

    def all_source_types(self) -> list[DataSourceType]:
        """返回配置中出现的所有 source 类型"""
        seen: set[DataSourceType] = set()
        for chain in self._chains.values():
            seen.update(chain)
        return list(seen)

    def dump_config(self) -> dict[str, Any]:
        """返回当前生效配置的字典形式（供 Admin 展示）"""
        return {
            "chains": {
                op.value: [st.value for st in chain]
                for op, chain in self._chains.items()
            },
            "circuit_breaker": {
                st.value: cfg for st, cfg in self._breaker_configs.items()
            },
            "tushare": {
                "enabled": self._tushare_enabled,
                "token_env": self._tushare_token_env,
            },
        }

    def reload(self) -> None:
        """热重载配置文件"""
        if not self._config_path.exists():
            logger.warning("数据源配置不存在 %s，使用内置默认策略", self._config_path)
            self._load_defaults()
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self._parse_chains(raw.get("chains", {}))
        self._parse_breaker(raw.get("circuit_breaker", {}))
        self._parse_tushare(raw.get("tushare", {}))
        logger.info(
            "数据源策略已加载: %d 个 operation, tushare_enabled=%s",
            len(self._chains), self._tushare_enabled,
        )

    # ── 私有解析方法 ──

    def _parse_chains(self, chains_raw: dict[str, list[str]]) -> None:
        """解析降级链配置 — 未知 source_type 会跳过并 warn"""
        self._chains.clear()
        for op_name, source_names in chains_raw.items():
            try:
                op = DataOperation(op_name.lower())
            except ValueError:
                logger.warning("未知 operation: %s，跳过", op_name)
                continue

            chain: list[DataSourceType] = []
            for name in source_names:
                st = self._resolve_source_type(name)
                if st is not None:
                    chain.append(st)
                else:
                    logger.warning("operation %s 中未知 source: %s，跳过", op_name, name)
            self._chains[op] = chain

    def _parse_breaker(self, breaker_raw: dict[str, dict[str, int]]) -> None:
        """解析熔断器配置"""
        self._breaker_configs.clear()
        for name, cfg in breaker_raw.items():
            st = self._resolve_source_type(name)
            if st is not None:
                self._breaker_configs[st] = {
                    "threshold": int(cfg.get("threshold", 3)),
                    "cooldown": int(cfg.get("cooldown", 300)),
                }

    def _parse_tushare(self, tushare_raw: dict[str, Any]) -> None:
        """解析 Tushare 全局开关"""
        self._tushare_enabled = bool(tushare_raw.get("enabled", False))
        self._tushare_token_env = str(tushare_raw.get("token_env", "TUSHARE_TOKEN"))

    @staticmethod
    def _resolve_source_type(name: str) -> DataSourceType | None:
        """从字符串解析成 DataSourceType 枚举，找不到返回 None"""
        try:
            return DataSourceType(name)
        except ValueError:
            return None

    def _load_defaults(self) -> None:
        """内置默认策略 — 配置文件缺失时兜底"""
        self._chains = {
            DataOperation.QUOTE: [
                DataSourceType.AKSHARE_BID_ASK,
                DataSourceType.AKSHARE_SPOT_EM,
                DataSourceType.AKSHARE_HIST,
            ],
            DataOperation.KLINE: [DataSourceType.AKSHARE_HIST],
            DataOperation.FUND_FLOW: [DataSourceType.AKSHARE_FUND_FLOW],
            DataOperation.SECTOR_FLOW: [DataSourceType.AKSHARE_SECTOR_FLOW],
            DataOperation.NEWS: [DataSourceType.AKSHARE_NEWS],
        }
        self._breaker_configs = {}
        self._tushare_enabled = False
