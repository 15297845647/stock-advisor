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
        self.client = httpx.AsyncClient(timeout=90)

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 4096,
    ) -> str:
        """发送对话请求，返回文本响应"""
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": messages,
        }
        try:
            resp = await self.client.post(
                self.base_url, headers=self.headers, json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]
        except httpx.HTTPStatusError as e:
            logger.error("MiniMax API HTTP error: %s — %s", e.response.status_code, e.response.text)
            raise
        except (KeyError, IndexError) as e:
            logger.error("MiniMax API unexpected response shape: %s", e)
            raise

    async def close(self):
        await self.client.aclose()
