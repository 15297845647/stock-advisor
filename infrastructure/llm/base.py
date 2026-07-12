"""LLM Provider 抽象基类

单一职责：定义所有 provider 必须实现的接口 + token 估算。
"""

from abc import ABC, abstractmethod

from domain.models.llm_task import LLMRequest


class LLMProvider(ABC):
    """LLM 供应商抽象接口"""

    name: str = ""

    def __init__(self, base_url: str, api_key: str, enabled: bool = True):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.enabled = enabled

    def is_available(self) -> bool:
        """是否可用（启用 + 有 key）"""
        return self.enabled and bool(self.api_key)

    @abstractmethod
    async def chat(
        self, req: LLMRequest, model: str,
    ) -> tuple[str, int, int]:
        """
        发送 chat 请求，返回 (content, prompt_tokens, completion_tokens)。
        失败时应抛异常，由 Router 层捕获处理降级。
        """
        ...

    @staticmethod
    def rough_token_count(text: str) -> int:
        """粗略估算 token 数（中英混合：1 中文=2 token，1 英文单词=1.3 token）"""
        if not text:
            return 0
        chinese_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_chars = len(text) - chinese_count
        return int(chinese_count * 2 + other_chars * 0.4)
