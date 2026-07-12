"""LLM 路由器 — 按 task_type 分发到 provider，失败自动 fallback

单一职责：路由决策 + 埋点。
不含具体 HTTP 逻辑，委托给 LLMProvider 实现。
"""

import asyncio
import logging
import os
import time

from domain.llm_routing_policy import LLMRoutingPolicy
from domain.models.llm_task import (
    LLMRequest, LLMResponse, LLMTaskType, TaskRouting,
)
from infrastructure.llm.base import LLMProvider
from infrastructure.llm.cost_calculator import CostCalculator

logger = logging.getLogger(__name__)


class LLMRouter:
    """LLM 请求路由器 —按 task_type 选择 provider 并计费"""

    def __init__(
        self,
        policy: LLMRoutingPolicy,
        providers: dict[str, LLMProvider],
        cost_calc: CostCalculator,
        usage_repo=None,   # LLMUsageRepository（延迟传入，允许无落库）
    ):
        self.policy = policy
        self._providers = providers
        self._cost_calc = cost_calc
        self._usage_repo = usage_repo

    # ── 主入口 ──

    async def chat(self, req: LLMRequest) -> LLMResponse:
        """
        路由并发送请求，返回 LLMResponse。
        流程：
            1. 查 policy 获取 routing
            2. 尝试主 provider (含重试)
            3. 失败则尝试 fallback provider
            4. 全部失败抛异常
            5. 每次尝试落库到 llm_usage
        """
        routing = self._resolve_routing(req.task_type)
        start = time.monotonic()

        for attempt, (provider_name, model) in enumerate(self._build_chain(routing)):
            provider = self._providers.get(provider_name)
            if provider is None or not provider.is_available():
                logger.warning("LLM provider %s 不可用，跳过", provider_name)
                continue

            try:
                content, ptoks, ctoks = await self._invoke_with_retry(
                    provider, req, model,
                )
            except Exception as e:
                await self._log_failure(req, provider_name, model, str(e))
                logger.warning("LLM %s/%s 失败: %s", provider_name, model, e)
                continue

            latency = int((time.monotonic() - start) * 1000)
            cost = self._cost_calc.calc(provider_name, model, ptoks, ctoks)
            await self._log_success(req, provider_name, model, ptoks, ctoks, cost, latency)

            return LLMResponse(
                content=content, provider=provider_name, model=model,
                prompt_tokens=ptoks, completion_tokens=ctoks,
                total_tokens=ptoks + ctoks, latency_ms=latency,
                cost_cny=cost, fallback_count=attempt,
            )

        raise RuntimeError(f"LLM 全部 provider 失败: task={req.task_type.value}")

    # ── 兼容旧接口（供渐进迁移）──

    async def simple_chat(
        self,
        task_type: LLMTaskType,
        system_prompt: str,
        user_content: str,
        max_tokens: int | None = None,
        wechat_id: str | None = None,
    ) -> str:
        """简单包装：单轮对话，返回纯文本"""
        req = LLMRequest(
            task_type=task_type,
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=max_tokens or 4096,
            wechat_id=wechat_id,
        )
        resp = await self.chat(req)
        return resp.content

    # ── 内部辅助 ──

    def _resolve_routing(self, task_type: LLMTaskType) -> TaskRouting:
        """查路由；未配置则回退到第一个 provider"""
        routing = self.policy.get_routing(task_type)
        if routing is not None:
            return routing

        providers = list(self.policy.all_providers().keys())
        if not providers:
            raise RuntimeError("无任何 LLM provider 配置")
        return TaskRouting(
            primary_provider=providers[0], primary_model="default",
        )

    def _build_chain(self, routing: TaskRouting) -> list[tuple[str, str]]:
        """构造主+备胎链"""
        chain: list[tuple[str, str]] = [
            (routing.primary_provider, routing.primary_model),
        ]
        if routing.fallback_provider and routing.fallback_model:
            chain.append((routing.fallback_provider, routing.fallback_model))
        return chain

    async def _invoke_with_retry(
        self, provider: LLMProvider, req: LLMRequest, model: str,
    ) -> tuple[str, int, int]:
        """在单个 provider 内做重试"""
        last_err: Exception | None = None
        for i in range(req.retries + 1):
            try:
                return await provider.chat(req, model)
            except Exception as e:
                last_err = e
                if i < req.retries:
                    await asyncio.sleep((i + 1) * 1.5)
        raise last_err or RuntimeError("unknown provider error")

    async def _log_success(
        self, req: LLMRequest, provider: str, model: str,
        ptoks: int, ctoks: int, cost: float, latency_ms: int,
    ) -> None:
        if self._usage_repo is None:
            return
        try:
            await self._usage_repo.insert(
                wechat_id=req.wechat_id, task_type=req.task_type.value,
                provider=provider, model=model,
                prompt_tokens=ptoks, completion_tokens=ctoks,
                cost_cny=cost, latency_ms=latency_ms, success=True,
            )
        except Exception as e:
            logger.debug("LLM 用量落库失败(忽略): %s", e)

    async def _log_failure(
        self, req: LLMRequest, provider: str, model: str, err: str,
    ) -> None:
        if self._usage_repo is None:
            return
        try:
            await self._usage_repo.insert(
                wechat_id=req.wechat_id, task_type=req.task_type.value,
                provider=provider, model=model,
                prompt_tokens=0, completion_tokens=0,
                cost_cny=0.0, latency_ms=0, success=False,
                error=err[:200],
            )
        except Exception:
            pass


# ── 全局单例 ──

_router: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    """全局 LLMRouter 单例"""
    global _router
    if _router is None:
        _router = _build_default_router()
    return _router


def reload_llm_router() -> LLMRouter:
    """重建单例（供 Admin 后台热重载）"""
    global _router
    _router = _build_default_router()
    return _router


def _build_default_router() -> LLMRouter:
    """
    从默认配置构造 router — provider 的 key/url 通过 getter 动态读取，
    Admin 后台改配置后立即生效。
    """
    from agent.config import PROJECT_ROOT
    from infrastructure.llm.openai_compat_provider import OpenAICompatProvider
    from repository.llm_usage_repository import LLMUsageRepository

    cfg_path = PROJECT_ROOT / "config" / "llm_routing.yaml"
    policy = LLMRoutingPolicy(cfg_path)

    providers: dict[str, LLMProvider] = {}
    for name, pcfg in policy.all_providers().items():
        key_getter, url_getter = _make_getters(name, pcfg)
        providers[name] = OpenAICompatProvider(
            name=name,
            base_url_getter=url_getter,
            api_key_getter=key_getter,
            enabled=pcfg.enabled,
        )

    cost_calc = CostCalculator(policy)
    usage_repo = LLMUsageRepository()

    router = LLMRouter(policy, providers, cost_calc, usage_repo)
    logger.info(
        "LLMRouter 已构造: %d providers (启用: %s)",
        len(providers),
        [n for n, p in providers.items() if p.is_available()],
    )
    return router


def _make_getters(provider_name: str, policy_cfg):
    """
    构造 (key_getter, url_getter) — 优先读运行时配置，兜底用 policy + env
    - key: agent.config.get_provider_config → policy.api_key_env → LLM_API_KEY
    - url: agent.config.get_provider_config → policy.base_url
    """
    from agent.config import get_llm_config, get_provider_config

    def _get_key() -> str:
        cfg = get_provider_config(provider_name)
        if cfg.get("api_key"):
            return cfg["api_key"]

        # 尝试从环境变量读（可能启动后被更新）
        env_key = os.getenv(policy_cfg.api_key_env, "").strip()
        if env_key:
            return env_key

        # 兜底：default provider 用 LLM_API_KEY
        if provider_name == "default":
            return get_llm_config().get("api_key", "")

        return ""

    def _get_url() -> str:
        cfg = get_provider_config(provider_name)
        if cfg.get("base_url"):
            return cfg["base_url"]

        if policy_cfg.base_url:
            return policy_cfg.base_url

        # default provider 用 LLM_BASE_URL 兜底
        if provider_name == "default":
            return get_llm_config().get("base_url", "")

        return ""

    return _get_key, _get_url
