"""ACP Agent 入口 — cc-connect 通过 stdio JSON-RPC 调用此进程"""

import asyncio
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from acp import run_agent  # noqa: E402
from acp.interfaces import Agent  # noqa: E402

from agent.user_router import UserRouter  # noqa: E402
from application.chat_service import ChatService  # noqa: E402
from infrastructure.database import init_db  # noqa: E402
from admin.startup import start_admin_server  # noqa: E402
from scheduler.daily_push import setup_scheduler  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,  # ACP 协议占用 stdout，日志走 stderr
)
logger = logging.getLogger("stock-advisor")


class StockAdvisorAgent(Agent):
    """A股/期货 AI 分析 Agent"""

    def __init__(self):
        self.chat_service = ChatService()
        self.user_router = UserRouter()
        self._db_initialized = False

    async def _ensure_db(self):
        if not self._db_initialized:
            await init_db()
            self._db_initialized = True

    async def initialize(self, params):
        await self._ensure_db()
        setup_scheduler()
        start_admin_server()
        logger.info("StockAdvisorAgent initialized")
        return {
            "protocolVersion": 1,
            "agentInfo": {"name": "stock-advisor", "version": "1.0.0"},
        }

    async def prompt(self, session_id: str, request, **kwargs):
        await self._ensure_db()

        messages = []
        if hasattr(request, "messages"):
            messages = [
                {"role": m.role, "content": getattr(m, "content", str(m))}
                for m in request.messages
            ]
        elif isinstance(request, dict):
            messages = request.get("messages", [])

        if not messages:
            yield self._make_update("没有收到消息内容。")
            return

        # 提取用户身份
        user_id = self.user_router.extract_user_id(messages)
        raw_content = messages[-1].get("content", "")
        user_message = self.user_router.strip_user_prefix(raw_content)

        if not user_message.strip():
            yield self._make_update("请输入你想查询的内容。")
            return

        logger.info("User=%s Message=%s", user_id, user_message[:80])

        # 加载上下文并处理
        ctx = await self.user_router.load_context(user_id)
        response = await self.chat_service.handle(user_id, user_message, ctx)

        yield self._make_update(response)

    @staticmethod
    def _make_update(text: str):
        """构造 ACP session/update 格式的响应"""
        from acp.helpers import content_block_text, session_update, message_part

        return session_update(
            messages=[message_part(role="assistant", content=[content_block_text(text)])]
        )


async def main():
    agent = StockAdvisorAgent()
    await run_agent(agent)


if __name__ == "__main__":
    asyncio.run(main())
