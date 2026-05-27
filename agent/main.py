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


def _extract_text(content) -> str:
    """从 ACP content 字段提取纯文本，兼容 str / list 格式"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content) if content else ""


class StockAdvisorAgent:
    """直接通过 stdio JSON-RPC 与 cc-connect 通信的 ACP Agent"""

    def __init__(self):
        self.chat_service = ChatService()
        self.user_router = UserRouter()
        self._db_ready = False

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

            logger.debug("recv: %s", json.dumps(request, ensure_ascii=False)[:500])
            response = await self._dispatch(request)
            if response is not None:
                logger.debug("send: %s", json.dumps(response, ensure_ascii=False)[:500])
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
                result = self._handle_session_new(params)
            elif method == "session/prompt":
                result = await self._handle_session_prompt(params)
            elif method == "session/stop":
                result = self._handle_session_stop(params)
            elif method == "ping":
                result = {"pong": True}
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

    def _handle_session_new(self, params: dict) -> dict:
        session_id = str(uuid.uuid4())
        logger.info("session created: %s", session_id)
        return {"sessionId": session_id}

    async def _handle_session_prompt(self, params: dict) -> dict:
        session_id = params.get("sessionId", "")

        # cc-connect 用 "prompt" 字段传递用户消息
        raw_content = ""
        prompt_field = params.get("prompt", "")
        messages = params.get("messages", [])

        if isinstance(prompt_field, str) and prompt_field.strip():
            raw_content = prompt_field.strip()
        elif isinstance(prompt_field, list):
            raw_content = _extract_text(prompt_field)
        elif messages:
            last_msg = messages[-1]
            raw_content = _extract_text(last_msg.get("content", ""))

        if not raw_content:
            self._send_update(session_id, "没有收到消息内容。")
            return {"stopReason": "end_turn"}

        # 提取用户身份
        user_id = self._get_user_id(params, messages)
        user_message = self.user_router.strip_user_prefix(raw_content)

        if not user_message.strip():
            self._send_update(session_id, "请输入你想查询的内容。")
            return {"stopReason": "end_turn"}

        logger.info("User=%s Message=%s", user_id, user_message[:80])

        try:
            await self._ensure_db()
            ctx = await self.user_router.load_context(user_id)
            response = await self.chat_service.handle(user_id, user_message, ctx)
        except Exception as e:
            logger.exception("chat service error")
            response = f"处理出错：{e}"

        logger.info("Response length: %d chars", len(response))
        self._send_update(session_id, response)
        return {"stopReason": "end_turn"}

    def _handle_session_stop(self, params: dict) -> dict:
        return {"stopped": True}

    def _get_user_id(self, params: dict, messages: list[dict]) -> str:
        """从 params metadata 或消息内容提取用户 ID"""
        # cc-connect 可能在 params 级别附带 session metadata
        meta = params.get("metadata", {})
        if isinstance(meta, dict):
            for key in ("sender", "from", "user_id", "userId"):
                if meta.get(key):
                    return str(meta[key])

        # 从消息中提取
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            text = _extract_text(msg.get("content", ""))
            if text.startswith("[") and "]" in text:
                bracket_end = text.index("]")
                uid = text[1:bracket_end].strip()
                if uid:
                    return uid
            # 消息级 metadata
            msg_meta = msg.get("metadata", {})
            if isinstance(msg_meta, dict):
                for key in ("sender", "from", "user_id", "userId"):
                    if msg_meta.get(key):
                        return str(msg_meta[key])

        return "default_user"

    # ── helpers ──

    def _send_update(self, session_id: str, text: str):
        """发送 session/update 通知（ACP 协议：文本内容通过通知下发）"""
        notification = {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": session_id,
                "kind": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            },
        }
        self._send(notification)

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
