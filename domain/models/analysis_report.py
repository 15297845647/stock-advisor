from dataclasses import dataclass
from datetime import date


@dataclass
class AnalysisReport:
    """个股分析报告"""
    stock_code: str
    report_date: date
    content: str
