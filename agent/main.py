"""ACP Agent 入口 — 直接实现 stdio JSON-RPC，兼容 cc-connect"""

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.user_router import UserRouter  # noqa: E402
from application.chat_service import ChatService  # noqa: E402
from infrastructure.database import init_db  # noqa: E402
from admin.startup import start_admin_server  # noqa: E402
from scheduler.daily_push import setup_scheduler  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("stock-advisor")


class StockAdvisorAgent:
    """直接通过 stdio JSON-RPC 与 cc-connect 通信的 ACP Agent"""

    def __init__(self):
        self.chat_service = ChatService()
        self.user_router = UserRouter()
        self._db_ready = False
        self._sessions: dict[str, dict] = {}

    async def _ensure_db(self):
        if not self._db_ready:
            await init_db()
            self._db_ready = True

    # ── JSON-RPC 主循环 ──

    async def run(self):
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("invalid JSON: %s", line[:200])
                continue

            response = await self._dispatch(request)
            if response is not None:
                self._send(response)

    def _send(self, obj: dict):
        out = json.dumps(obj, ensure_ascii=False) + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()

    # ── 分发 JSON-RPC method ──

    async def _dispatch(self, req: dict) -> dict | None:
        method = req.get("method", "")
        req_id = req.get("id")
        params = req.get("params", {})

        try:
            if method == "initialize":
                result = await self._handle_initialize(params)
            elif method == "session/new":
                result = await self._handle_session_new(params)
            elif method == "session/prompt":
                result = await self._handle_session_prompt(req_id, params)
                return None  # prompt 通过 notification 流式返回
            elif method == "session/stop":
                result = self._handle_session_stop(params)
            else:
                logger.warning("unknown method: %s", method)
                return self._rpc_error(req_id, -32601, f"Method not found: {method}")

            return self._rpc_result(req_id, result)
        except Exception as e:
            logger.exception("error handling %s", method)
            return self._rpc_error(req_id, -32000, str(e))

    # ── RPC handlers ──

    async def _handle_initialize(self, params: dict) -> dict:
        await self._ensure_db()
        setup_scheduler()
        start_admin_server()
        logger.info("StockAdvisorAgent initialized")
        return {
            "protocolVersion": 1,
            "agentInfo": {"name": "stock-advisor", "version": "1.0.0"},
        }

    async def _handle_session_new(self, params: dict) -> dict:
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = {"messages": []}
        logger.info("session created: %s", session_id)
        return {"sessionId": session_id}

    async def _handle_session_prompt(self, req_id, params: dict):
        session_id = params.get("sessionId", "")
        messages = params.get("messages", [])

        if not messages:
            self._send(self._rpc_result(req_id, self._make_update(session_id, "没有收到消息内容。")))
            return

        # 提取最后一条用户消息
        last_msg = messages[-1]
        raw_content = ""
        content = last_msg.get("content", "")
        if isinstance(content, str):
            raw_content = content
        elif isinstance(content, list):
            raw_content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )

        # 提取用户身份
        user_id = self.user_router.extract_user_id(messages)
        user_message = self.user_router.strip_user_prefix(raw_content)

        if not user_message.strip():
            self._send(self._rpc_result(req_id, self._make_update(session_id, "请输入你想查询的内容。")))
            return

        logger.info("User=%s Message=%s", user_id, user_message[:80])

        try:
            await self._ensure_db()
            ctx = await self.user_router.load_context(user_id)
            response = await self.chat_service.handle(user_id, user_message, ctx)
        except Exception as e:
            logger.exception("chat service error")
            response = f"处理出错：{e}"

        self._send(self._rpc_result(req_id, self._make_update(session_id, response)))

    def _handle_session_stop(self, params: dict) -> dict:
        session_id = params.get("sessionId", "")
        self._sessions.pop(session_id, None)
        return {"stopped": True}

    # ── helpers ──

    @staticmethod
    def _make_update(session_id: str, text: str) -> dict:
        return {
            "sessionId": session_id,
            "messages": [{
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            }],
            "state": "completed",
        }

    @staticmethod
    def _rpc_result(req_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _rpc_error(req_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def main():
    agent = StockAdvisorAgent()
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
