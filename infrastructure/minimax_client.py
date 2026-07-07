import logging

import httpx

from agent.config import get_llm_config

logger = logging.getLogger(__name__)


class MiniMaxClient:
    """LLM 客户端（OpenAI Chat Completions 兼容格式）

    支持 DeepSeek / OpenAI / 任何 OpenAI 兼容 API。
    每次请求从 config 读取最新配置，后台热更新立即生效。
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=180)

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 4096,
        retries: int = 1,
    ) -> str:
        """发送对话请求（OpenAI Chat Completions 格式），返回文本响应"""
        cfg = get_llm_config()
        base_url = cfg["base_url"].rstrip("/")
        url = f"{base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }

        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)

        payload = {
            "model": cfg["model"],
            "max_tokens": max_tokens,
            "messages": full_messages,
            "stream": False,
        }

        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = await self.client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return self._extract_response_text(data)
            except httpx.ReadTimeout as e:
                last_err = e
                if attempt < retries:
                    logger.warning("LLM 超时(第%d次)，重试中...", attempt + 1)
                    continue
            except httpx.HTTPStatusError as e:
                logger.error("LLM API HTTP error: %s — %s", e.response.status_code, e.response.text[:500])
                raise

        raise last_err  # type: ignore[misc]

    @staticmethod
    def _extract_response_text(data: dict) -> str:
        """从 OpenAI 格式响应中提取文本"""
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            content = msg.get("content")
            if content:
                return content

        # 兼容 Anthropic 格式降级
        content = data.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "thinking":
                        continue
                    if block.get("type") == "text" and "text" in block:
                        return block["text"]
                if isinstance(block, str):
                    return block
        if isinstance(content, str):
            return content

        logger.error("LLM API unknown response: %s", str(data)[:500])
        raise ValueError("无法解析 LLM 返回格式")

    async def close(self):
        await self.client.aclose()
