"""分析编排 — 拉数据 → 计算指标 → 构造prompt → 调MiniMax → 存报告"""

import logging
from datetime import date

from domain.models.analysis_report import AnalysisReport
from domain.prompt_builder import build_analysis_prompt, build_system_prompt
from domain.stock_analyzer import analyze_technical
from infrastructure.akshare_client import AKShareClient
from infrastructure.minimax_client import MiniMaxClient
from repository.report_repository import ReportRepository
from repository.stock_repository import StockRepository

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(self):
        self.akshare = AKShareClient()
        self.minimax = MiniMaxClient()
        self.stock_repo = StockRepository()
        self.report_repo = ReportRepository()

    async def analyze_stock(self, stock_code: str, force: bool = False) -> str:
        """对一只股票做完整分析，返回报告文本"""

        # 今日已有报告且非强制刷新，直接返回缓存
        if not force:
            cached = await self.report_repo.get_today_report(stock_code)
            if cached:
                return cached.content

        # 1. 获取行情数据
        quote = await self.akshare.get_realtime_quote(stock_code)
        if not quote:
            return f"未找到股票 {stock_code} 的行情数据，请确认代码是否正确。"

        # 2. 获取日K + 缓存
        bars = await self.akshare.get_stock_history(stock_code, days=60)
        if bars:
            await self.stock_repo.save_daily_bars(bars)

        # 3. 计算技术指标
        tech = analyze_technical(bars)
        if not tech:
            return f"{quote.name}({stock_code}) 数据不足，无法完成技术分析。"

        # 4. 获取资金流向
        fund_flows = await self.akshare.get_fund_flow(stock_code)

        # 5. 构造分析prompt并调用MiniMax
        analysis_prompt = build_analysis_prompt(quote, tech, fund_flows, bars)
        system = build_system_prompt()

        report_text = await self.minimax.chat(
            system_prompt=system,
            messages=[{"role": "user", "content": analysis_prompt}],
        )

        # 6. 存储报告
        report = AnalysisReport(
            stock_code=stock_code,
            report_date=date.today(),
            content=report_text,
        )
        await self.report_repo.save_report(report)

        return report_text

    async def get_market_overview(self) -> str:
        """大盘概览"""
        indices = [
            ("000001", "上证指数"),
            ("399001", "深证成指"),
            ("399006", "创业板指"),
        ]
        lines = ["📊 今日大盘概览\n"]
        for code, name in indices:
            q = await self.akshare.get_market_index(code)
            if q:
                arrow = "🔴" if q.change_pct >= 0 else "🟢"
                lines.append(f"{arrow} {name}: {q.price:.2f} ({q.change_pct:+.2f}%)")

        lines.append("\n以上分析仅供参考，不构成投资建议，投资有风险，入市需谨慎。")
        return "\n".join(lines)
