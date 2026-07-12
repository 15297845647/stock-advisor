"""报告分享 Repository"""

import logging
import secrets
import string
from datetime import datetime, timedelta

from domain.models.report_share import ReportShare
from infrastructure.database import get_connection

logger = logging.getLogger(__name__)


class ReportShareRepository:
    """报告分享 CRUD"""

    async def create(
        self,
        stock_code: str, stock_name: str, depth: str,
        report_content: str, pdf_path: str | None = None,
        ttl_days: int = 7,
    ) -> str:
        """创建分享记录，返回 share_token"""
        token = self._gen_token()
        expires = datetime.utcnow() + timedelta(days=ttl_days)

        conn = await get_connection()
        try:
            await conn.execute(
                "INSERT INTO report_shares "
                "(share_token, stock_code, stock_name, depth, "
                " report_content, pdf_path, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    token, stock_code, stock_name, depth,
                    report_content, pdf_path, expires.isoformat(sep=" "),
                ),
            )
            await conn.commit()
            return token
        finally:
            await conn.close()

    async def get(self, share_token: str) -> ReportShare | None:
        """按 token 取记录（自动累加 view_count）"""
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                "SELECT share_token, stock_code, stock_name, depth, "
                "report_content, pdf_path, expires_at, created_at, view_count "
                "FROM report_shares WHERE share_token = ?",
                (share_token,),
            )
            if not rows:
                return None
            r = rows[0]
            expires_at = self._parse_dt(r["expires_at"])
            if expires_at and expires_at < datetime.utcnow():
                return None

            # 累加 view_count
            await conn.execute(
                "UPDATE report_shares SET view_count = view_count + 1 "
                "WHERE share_token = ?", (share_token,),
            )
            await conn.commit()

            return ReportShare(
                share_token=r["share_token"], stock_code=r["stock_code"],
                stock_name=r["stock_name"] or "", depth=r["depth"] or "",
                report_content=r["report_content"] or "",
                pdf_path=r["pdf_path"],
                expires_at=expires_at,
                created_at=self._parse_dt(r["created_at"]) or datetime.utcnow(),
                view_count=int(r["view_count"] or 0),
            )
        finally:
            await conn.close()

    async def cleanup_expired(self) -> int:
        """清理已过期的分享记录"""
        now = datetime.utcnow().isoformat(sep=" ")
        conn = await get_connection()
        try:
            cur = await conn.execute(
                "DELETE FROM report_shares WHERE expires_at < ?", (now,),
            )
            await conn.commit()
            return cur.rowcount
        finally:
            await conn.close()

    # ── 辅助 ──

    @staticmethod
    def _gen_token(length: int = 12) -> str:
        """生成 base62 token"""
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def _parse_dt(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace(" ", "T"))
        except Exception:
            return None
