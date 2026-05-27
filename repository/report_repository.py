from datetime import date

from infrastructure.database import get_connection
from domain.models.analysis_report import AnalysisReport


class ReportRepository:
    """分析报告存取"""

    async def save_report(self, report: AnalysisReport):
        conn = await get_connection()
        try:
            await conn.execute(
                """INSERT INTO analysis_reports (stock_code, report_date, report_content)
                   VALUES (?, ?, ?)""",
                (report.stock_code, report.report_date.isoformat(), report.content),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def get_latest_report(self, stock_code: str) -> AnalysisReport | None:
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                """SELECT * FROM analysis_reports
                   WHERE stock_code = ?
                   ORDER BY report_date DESC LIMIT 1""",
                (stock_code,),
            )
            if not rows:
                return None
            r = rows[0]
            return AnalysisReport(
                stock_code=r["stock_code"],
                report_date=date.fromisoformat(r["report_date"]),
                content=r["report_content"],
            )
        finally:
            await conn.close()

    async def get_today_report(self, stock_code: str) -> AnalysisReport | None:
        today = date.today().isoformat()
        conn = await get_connection()
        try:
            rows = await conn.execute_fetchall(
                """SELECT * FROM analysis_reports
                   WHERE stock_code = ? AND report_date = ?""",
                (stock_code, today),
            )
            if not rows:
                return None
            r = rows[0]
            return AnalysisReport(
                stock_code=r["stock_code"],
                report_date=date.fromisoformat(r["report_date"]),
                content=r["report_content"],
            )
        finally:
            await conn.close()
