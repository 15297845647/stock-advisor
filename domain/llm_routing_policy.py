"""LLM 路由策略 — 从 yaml 加载 task_type → provider/model 映射

单一职责：策略加载 + 查询，无网络调用。
"""

import logging
from pathlib import Path
from typing import Any

import yaml

from domain.models.llm_task import (
    LLMTaskType, ModelPricing, ProviderConfig, TaskRouting,
)

logger = logging.getLogger(__name__)


class LLMRoutingPolicy:
    """LLM 路由策略容器"""

    def __init__(self, config_path: Path):
        self._config_path = config_path
        self._routings: dict[LLMTaskType, TaskRouting] = {}
        self._providers: dict[str, ProviderConfig] = {}
        self._pricing: dict[tuple[str, str], ModelPricing] = {}
        self.reload()

    # ── 公开接口 ──

    def get_routing(self, task_type: LLMTaskType) -> TaskRouting | None:
        """返回指定任务的路由规则"""
        return self._routings.get(task_type)

    def get_provider(self, name: str) -> ProviderConfig | None:
        """获取 provider 配置"""
        return self._providers.get(name)

    def all_providers(self) -> dict[str, ProviderConfig]:
        """所有已配置 provider"""
        return dict(self._providers)

    def get_pricing(self, provider: str, model: str) -> ModelPricing | None:
        """查询模型计价"""
        return self._pricing.get((provider, model))

    def dump_config(self) -> dict[str, Any]:
        """当前配置字典（供 Admin 展示）"""
        return {
            "tasks": {
                t.value: {
                    "provider": r.primary_provider, "model": r.primary_model,
                    "max_tokens": r.max_tokens, "temperature": r.temperature,
                    "fallback_provider": r.fallback_provider,
                    "fallback_model": r.fallback_model,
                }
                for t, r in self._routings.items()
            },
            "providers": {
                n: {"base_url": p.base_url, "api_key_env": p.api_key_env,
                    "enabled": p.enabled}
                for n, p in self._providers.items()
            },
            "pricing": [
                {"provider": p.provider, "model": p.model,
                 "input": p.input_price_per_1m, "output": p.output_price_per_1m}
                for p in self._pricing.values()
            ],
        }

    def reload(self) -> None:
        """热重载配置文件"""
        if not self._config_path.exists():
            logger.warning("LLM 路由配置不存在 %s，使用默认策略", self._config_path)
            self._load_defaults()
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        self._parse_providers(raw.get("providers", {}))
        self._parse_tasks(raw.get("tasks", {}))
        self._parse_pricing(raw.get("pricing", []))
        logger.info(
            "LLM 路由策略已加载: %d 个任务, %d 个 provider",
            len(self._routings), len(self._providers),
        )

    # ── 私有解析 ──

    def _parse_providers(self, providers_raw: dict[str, dict]) -> None:
        self._providers.clear()
        for name, cfg in providers_raw.items():
            self._providers[name] = ProviderConfig(
                name=name,
                base_url=str(cfg.get("base_url", "")).rstrip("/"),
                api_key_env=str(cfg.get("api_key_env", "")),
                enabled=bool(cfg.get("enabled", True)),
            )

    def _parse_tasks(self, tasks_raw: dict[str, dict]) -> None:
        self._routings.clear()
        for task_name, cfg in tasks_raw.items():
            task = self._parse_task_type(task_name)
            if task is None:
                continue
            self._routings[task] = TaskRouting(
                primary_provider=str(cfg.get("provider", "")),
                primary_model=str(cfg.get("model", "")),
                max_tokens=int(cfg.get("max_tokens", 4096)),
                temperature=float(cfg.get("temperature", 0.7)),
                fallback_provider=cfg.get("fallback_provider"),
                fallback_model=cfg.get("fallback_model"),
            )

    def _parse_pricing(self, pricing_raw: list[dict]) -> None:
        self._pricing.clear()
        for row in pricing_raw:
            key = (str(row.get("provider", "")), str(row.get("model", "")))
            self._pricing[key] = ModelPricing(
                provider=key[0], model=key[1],
                input_price_per_1m=float(row.get("input", 0)),
                output_price_per_1m=float(row.get("output", 0)),
            )

    @staticmethod
    def _parse_task_type(name: str) -> LLMTaskType | None:
        try:
            return LLMTaskType(name.lower())
        except ValueError:
            logger.warning("未知 LLM 任务类型: %s，跳过", name)
            return None

    def _load_defaults(self) -> None:
        """内置默认策略 — 使用 agent.config 里的 LLM_MODEL"""
        from agent.config import get_llm_config
        cfg = get_llm_config()
        default_url = cfg.get("base_url", "https://api.deepseek.com")
        default_model = cfg.get("model", "deepseek-chat")

        self._providers = {
            "default": ProviderConfig(
                name="default", base_url=default_url,
                api_key_env="LLM_API_KEY", enabled=True,
            ),
        }
        routing = TaskRouting(
            primary_provider="default", primary_model=default_model,
        )
        self._routings = {t: routing for t in LLMTaskType}
