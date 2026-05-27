"""从 ACP 请求中提取用户身份，加载对应上下文"""

import logging

from domain.models.user_context import UserContext
from repository.user_repository import UserRepository

logger = logging.getLogger(__name__)

# cc-connect 通过 session metadata 或消息前缀传递发送者信息
# 当无法识别时使用默认用户ID
_DEFAULT_USER = "default_user"


class UserRouter:
    def __init__(self):
        self.user_repo = UserRepository()

    def extract_user_id(self, messages: list[dict]) -> str:
        """
        从消息列表提取用户ID。

        cc-connect 在消息中可能以 metadata 或文本前缀形式附带发送者。
        格式举例：
          - 消息 metadata 中的 sender / from 字段
          - 消息文本开头 "[wxid_xxx] 实际消息"

        当前先用简单策略：检查最后一条 user 消息是否含 [wxid_...] 前缀。
        """
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")

            # 尝试从 [wxid_xxx] 前缀提取
            if content.startswith("[") and "]" in content:
                bracket_end = content.index("]")
                user_id = content[1:bracket_end].strip()
                if user_id:
                    return user_id

            # 尝试从 metadata
            meta = msg.get("metadata", {})
            if isinstance(meta, dict):
                for key in ("sender", "from", "user_id", "wechat_id"):
                    if meta.get(key):
                        return str(meta[key])

        return _DEFAULT_USER

    def strip_user_prefix(self, content: str) -> str:
        """去掉消息中的 [wxid_xxx] 前缀，返回纯文本"""
        if content.startswith("[") and "]" in content:
            bracket_end = content.index("]")
            return content[bracket_end + 1:].strip()
        return content

    async def load_context(self, user_id: str) -> UserContext:
        return await self.user_repo.load_context(user_id)
