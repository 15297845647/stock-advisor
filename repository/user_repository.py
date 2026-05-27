import logging

from infrastructure.database import get_connection
from domain.models.user_context import ChatMessage, UserContext, UserProfile
from agent.config import CHAT_HISTORY_ROUNDS, MEMORY_LOAD_LIMIT

logger = logging.getLogger(__name__)


class UserRepository:
    """用户数据 CRUD — 记忆、关注列表、对话历史"""

    # ── 用户基本信息 ──

    async def ensure_user(self, wechat_id: str, nickname: str = "") -> UserProfile:
        """首次见到新用户时自动创建"""
        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO users (wechat_id, nickname) VALUES (?, ?)",
                (wechat_id, nickname),
            )
            await conn.commit()
            row = await conn.execute_fetchall(
                "SELECT * FROM users WHERE wechat_id = ?", (wechat_id,)
            )
            r = row[0]
            return UserProfile(
                wechat_id=r["wechat_id"],
                nickname=r["nickname"] or "",
                risk_level=r["risk_level"],
                trade_style=r["trade_style"],
            )
        finally:
            await conn.close()

    async def update_profile(self, wechat_id: str, **fields):
        allowed = {"nickname", "risk_level", "trade_style"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [wechat_id]
        conn = await get_connection()
        try:
            await conn.execute(
                f"UPDATE users SET {set_clause} WHERE wechat_id = ?", values
            )
            await conn.commit()
        finally:
            await conn.close()

    # ── 长期记忆 ──

    async def add_memory(self, wechat_id: str, content: str, category: str = "preference"):
        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT INTO user_memory (wechat_id, content, category) VALUES (?, ?, ?)",
                (wechat_id, content, category),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_memories(self, wechat_id: str, limit: int = MEMORY_LOAD_LIMIT) -> list[str]:
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT content FROM user_memory WHERE wechat_id = ? ORDER BY created_at DESC LIMIT ?",
                (wechat_id, limit),
            )
            return [r["content"] for r in rows]
        finally:
            await conn.close()

    # ── 关注列表 ──

    async def subscribe(self, wechat_id: str, stock_code: str, stock_name: str = ""):
        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT OR IGNORE INTO user_watchlist (wechat_id, stock_code, stock_name) VALUES (?, ?, ?)",
                (wechat_id, stock_code, stock_name),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def unsubscribe(self, wechat_id: str, stock_code: str):
        conn = await get_connection()
        try:
            await conn.execute(
                "DELETE FROM user_watchlist WHERE wechat_id = ? AND stock_code = ?",
                (wechat_id, stock_code),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_watchlist(self, wechat_id: str) -> list[str]:
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT stock_code FROM user_watchlist WHERE wechat_id = ? ORDER BY added_at",
                (wechat_id,),
            )
            return [r["stock_code"] for r in rows]
        finally:
            await conn.close()

    # ── 对话历史 ──

    async def append_chat(self, wechat_id: str, role: str, content: str):
        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT INTO chat_history (wechat_id, role, content) VALUES (?, ?, ?)",
                (wechat_id, role, content),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_recent_chat(self, wechat_id: str, rounds: int = CHAT_HISTORY_ROUNDS) -> list[ChatMessage]:
        limit = rounds * 2  # 每轮 = user + assistant
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT role, content FROM chat_history WHERE wechat_id = ? ORDER BY id DESC LIMIT ?",
                (wechat_id, limit),
            )
            messages = [ChatMessage(role=r["role"], content=r["content"]) for r in reversed(rows)]
            return messages
        finally:
            await conn.close()

    # ── 完整上下文组装 ──

    async def load_context(self, wechat_id: str) -> UserContext:
        """加载一个用户的完整对话上下文"""
        profile = await self.ensure_user(wechat_id)
        memories = await self.get_memories(wechat_id)
        watchlist = await self.get_watchlist(wechat_id)
        recent_chat = await self.get_recent_chat(wechat_id)
        return UserContext(
            profile=profile,
            memories=memories,
            watchlist=watchlist,
            recent_chat=recent_chat,
        )

    async def get_all_users_with_watchlist(self) -> list[tuple[str, list[str]]]:
        """获取所有有关注列表的用户（用于定时推送）"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT DISTINCT wechat_id FROM user_watchlist"
            )
            result = []
            for r in rows:
                wid = r["wechat_id"]
                codes = await self.get_watchlist(wid)
                if codes:
                    result.append((wid, codes))
            return result
        finally:
            await conn.close()
