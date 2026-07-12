"""报告导出服务 — 生成 HTML 分享链 + (可选) PDF

单一职责：编排 renderer + repo，返回可分享的 URL。
"""

import logging
import os
from datetime import datetime

from domain.report_renderer import ReportRenderer
from repository.report_share_repository import ReportShareRepository

logger = logging.getLogger(__name__)


class ReportExportService:
    """报告分享/导出编排"""

    def __init__(self):
        self.renderer = ReportRenderer()
        self.share_repo = ReportShareRepository()

    async def create_share(
        self,
        stock_code: str,
        stock_name: str,
        depth: str,
        report_content: str,
        ttl_days: int = 7,
        base_url: str | None = None,
    ) -> dict:
        """
        创建报告分享链接
        返回：{ token, share_url, expires_at }
        """
        token = await self.share_repo.create(
            stock_code=stock_code, stock_name=stock_name,
            depth=depth, report_content=report_content,
            ttl_days=ttl_days,
        )

        share_url = self._build_share_url(token, base_url)
        expires_at = (
            datetime.now().replace(microsecond=0).isoformat()
        )
        return {
            "token": token,
            "share_url": share_url,
            "expires_days": ttl_days,
            "created_at": expires_at,
        }

    async def render_html(self, share_token: str) -> str | None:
        """按 token 取报告并渲染成 HTML"""
        share = await self.share_repo.get(share_token)
        if share is None:
            return None
        return self.renderer.render_html(
            stock_code=share.stock_code,
            stock_name=share.stock_name,
            depth=share.depth,
            report_content=share.report_content,
        )

    @staticmethod
    def _build_share_url(token: str, base_url: str | None) -> str:
        """构造分享 URL"""
        base = base_url or os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        if not base:
            base = f"http://localhost:{os.getenv('ADMIN_PORT', '8900')}"
        return f"{base}/r/{token}"
