"""LLM 客户端（OpenAI Chat Completions 兼容）

**已弃用**：新代码请用 `infrastructure.llm.LLMRouter`，
本类保留为向后兼容 shim — 内部委托给 LLMRouter，
task_type 默认走 CHAT，无法细分成本。
"""

import logging

import httpx

from agent.config import get_llm_config
from domain.models.llm_task import LLMRequest, LLMTaskType

logger = logging.getLogger(__name__)


class MiniMaxClient:
    """LLM 客户端（兼容 shim）

    历史遗留接口，内部委托 LLMRouter。
    新代码请直接调用 `get_llm_router().chat(LLMRequest(...))`。
    """

    def __init__(self):
        # 保留 httpx.AsyncClient 只是为了避免破坏可能的直接引用
        self.client = httpx.AsyncClient(timeout=180)
        self._router = None

    def _get_router(self):
        """延迟获取 LLMRouter 单例"""
        if self._router is None:
            from infrastructure.llm import get_llm_router
            self._router = get_llm_router()
        return self._router

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 4096,
        retries: int = 1,
        task_type: LLMTaskType | None = None,
        wechat_id: str | None = None,
    ) -> str:
        """委托给 LLMRouter；未指定 task_type 时按 CHAT 处理"""
        req = LLMRequest(
            task_type=task_type or LLMTaskType.CHAT,
            system_prompt=system_prompt,
            messages=list(messages),
            max_tokens=max_tokens,
            retries=retries,
            wechat_id=wechat_id,
        )
        resp = await self._get_router().chat(req)
        return resp.content

    async def close(self):
        await self.client.aclose()
