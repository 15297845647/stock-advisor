import logging

import httpx

from agent.config import MINIMAX_API_KEY, MINIMAX_BASE_URL, MINIMAX_MODEL

logger = logging.getLogger(__name__)


class MiniMaxClient:
    """MiniMax API 客户端，使用 Anthropic Messages 兼容格式"""

    def __init__(
        self,
        api_key: str = MINIMAX_API_KEY,
        base_url: str = MINIMAX_BASE_URL,
        model: str = MINIMAX_MODEL,
    ):
        self.base_url = base_url
        self.model = model
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=180)

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 4096,
        retries: int = 1,
    ) -> str:
        """发送对话请求，返回文本响应。超时自动重试 retries 次。"""
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": messages,
            "tool_choice": {"type": "none"},
        }
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = await self.client.post(
                    self.base_url, headers=self.headers, json=payload
                )
                resp.raise_for_status()
                data = resp.json()
                return self._extract_response_text(data)
            except httpx.ReadTimeout as e:
                last_err = e
                if attempt < retries:
                    logger.warning("MiniMax 超时(第%d次)，重试中...", attempt + 1)
                    continue
            except httpx.HTTPStatusError as e:
                logger.error("MiniMax API HTTP error: %s — %s", e.response.status_code, e.response.text)
                raise
        raise last_err  # type: ignore[misc]

    @staticmethod
    def _extract_response_text(data: dict) -> str:
        """兼容多种 API 返回格式提取文本（含 thinking / tool_use 块跳过）"""
        content = data.get("content", [])
        thinking_text = ""

        # Anthropic / MiniMax 格式: {"content": [{...}, ...]}
        if isinstance(content, list) and content:
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "thinking":
                        thinking_text = block.get("thinking", "")
                        continue
                    if block.get("type") == "tool_use":
                        continue
                    if block.get("type") == "text" and "text" in block:
                        return block["text"]
                    if "text" in block:
                        return block["text"]
                    if "value" in block:
                        return block["value"]
                if isinstance(block, str):
                    return block

        # OpenAI 格式: {"choices": [{"message": {"content": "..."}}]}
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            if msg.get("content"):
                return msg["content"]

        # content 本身是字符串
        if isinstance(content, str):
            return content

        # 最终降级：模型只产生了 thinking 没产生 text，用 thinking 内容
        if thinking_text:
            logger.warning("MiniMax 仅返回 thinking 块，用作回复")
            return thinking_text

        logger.error("MiniMax API unknown response shape: %s", str(data)[:500])
        raise ValueError("无法解析 MiniMax 返回格式")

    async def close(self):
        await self.client.aclose()
