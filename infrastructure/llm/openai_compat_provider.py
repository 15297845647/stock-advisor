"""OpenAI 兼容协议 Provider — 覆盖 DeepSeek / Qwen / MiniMax / OpenAI 等

单一职责：调用 OpenAI Chat Completions 兼容协议，解析响应。

设计要点：
    api_key / base_url 通过 callable 动态读取 —
    Admin 后台修改配置后立即生效，无需重启进程。
"""

import logging
from typing import Callable

import httpx

from domain.models.llm_task import LLMRequest
from infrastructure.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容协议实现（动态配置版）"""

    def __init__(
        self, name: str,
        base_url_getter: Callable[[], str],
        api_key_getter: Callable[[], str],
        enabled: bool = True,
    ):
        # 父类需要 base_url/api_key 字段，这里传空占位（真正调用时用 getter）
        super().__init__(base_url="", api_key="", enabled=enabled)
        self.name = name
        self._get_url = base_url_getter
        self._get_key = api_key_getter
        self._client = httpx.AsyncClient(timeout=180)

    # ── 覆盖基类的可用性判断（动态读 key）──

    def is_available(self) -> bool:
        try:
            return self.enabled and bool(self._get_key())
        except Exception:
            return False

    # ── 主接口 ──

    async def chat(
        self, req: LLMRequest, model: str,
    ) -> tuple[str, int, int]:
        """POST /chat/completions，返回 (content, prompt_tokens, completion_tokens)"""
        base_url = (self._get_url() or "").rstrip("/")
        api_key = self._get_key() or ""

        if not base_url:
            raise RuntimeError(f"{self.name}: base_url 未配置")
        if not api_key:
            raise RuntimeError(f"{self.name}: api_key 未配置")

        payload = self._build_payload(req, model)
        headers = self._build_headers(api_key)
        url = f"{base_url}/chat/completions"

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

    @staticmethod
    def _build_headers(api_key: str) -> dict:
        return {
            "Authorization": f"Bearer {api_key}",
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
