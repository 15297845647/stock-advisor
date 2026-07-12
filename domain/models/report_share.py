"""报告分享 DTO"""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ReportShare:
    """报告分享记录"""

    share_token: str
    stock_code: str
    stock_name: str
    depth: str
    report_content: str
    pdf_path: str | None
    expires_at: datetime
    created_at: datetime
    view_count: int = 0
