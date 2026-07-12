"""OpenAI 兼容协议 Provider — 覆盖 DeepSeek / Qwen / MiniMax / OpenAI 等

单一职责：调用 OpenAI Chat Completions 兼容协议，解析响应。
所有走 `/chat/completions` 的服务都用这一个类，通过 base_url 区分。
"""

import logging

import httpx

from domain.models.llm_task import LLMRequest
from infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容协议实现"""

    def __init__(
        self, name: str, base_url: str, api_key: str, enabled: bool = True,
    ):
        super().__init__(base_url, api_key, enabled)
        self.name = name
        self._client = httpx.AsyncClient(timeout=180)

    async def chat(
        self, req: LLMRequest, model: str,
    ) -> tuple[str, int, int]:
        """POST /chat/completions，返回 (content, prompt_tokens, completion_tokens)"""
        payload = self._build_payload(req, model)
        headers = self._build_headers()
        url = f"{self.base_url}/chat/completions"

        resp = await self._client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"{self.name} HTTP {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        content = self._extract_content(data)
        prompt_tokens, completion_tokens = self._extract_usage(data, req, content)
        return content, prompt_tokens, completion_tokens

    # ── 内部辅助 ──

    def _build_payload(self, req: LLMRequest, model: str) -> dict:
        """构造 payload — 兼容 OpenAI Chat Completions 规范"""
        messages = [{"role": "system", "content": req.system_prompt}]
        messages.extend(req.messages)
        return {
            "model": model,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "stream": False,
        }

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_content(data: dict) -> str:
        """从响应中提取正文"""
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM 响应无 choices 字段")
        msg = choices[0].get("message") or {}
        content = msg.get("content", "")
        if not content:
            raise RuntimeError("LLM 响应 content 为空")
        return content

    def _extract_usage(
        self, data: dict, req: LLMRequest, content: str,
    ) -> tuple[int, int]:
        """
        提取 token 用量 — 优先用 API 返回的 usage，兜底用估算。
        """
        usage = data.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)

        if not prompt:
            prompt = self._estimate_prompt_tokens(req)
        if not completion:
            completion = self.rough_token_count(content)
        return prompt, completion

    def _estimate_prompt_tokens(self, req: LLMRequest) -> int:
        """从请求内容估算 prompt tokens"""
        total = self.rough_token_count(req.system_prompt)
        for m in req.messages:
            total += self.rough_token_count(m.get("content", ""))
        return total
