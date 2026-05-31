"""分析编排 — 拉数据(行情+技术+资金+新闻) → 计算指标 → MiniMax → 结构化决策 → 存报告"""

import logging
from datetime import date

from domain.decision_parser import extract_decision, format_decision
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
        """综合分析：技术面 + 资金面 + 消息面 → 结构化决策"""

        if not force:
            cached = await self.report_repo.get_today_report(stock_code)
            if cached:
                return cached.content

        # 1. 实时行情
        quote = await self.akshare.get_realtime_quote(stock_code)
        if not quote:
            return f"未找到股票 {stock_code} 的行情数据，请确认代码是否正确。"

        # 2. 日K + 缓存
        bars = await self.akshare.get_stock_history(stock_code, days=60)
        if bars:
            await self.stock_repo.save_daily_bars(bars)

        # 3. 技术指标
        tech = analyze_technical(bars)
        if not tech:
            return f"{quote.name}({stock_code}) 数据不足，无法完成技术分析。"

        # 4. 资金流向
        fund_flows = await self.akshare.get_fund_flow(stock_code)

        # 5. 新闻/公告
        news = await self.akshare.get_stock_news(stock_code, limit=20)

        # 6. 构造 prompt → MiniMax
        analysis_prompt = build_analysis_prompt(quote, tech, fund_flows, bars, news)
        system = build_system_prompt()

        raw_response = await self.minimax.chat(
            system_prompt=system,
            messages=[{"role": "user", "content": analysis_prompt}],
        )

        # 7. 解析结构化决策
        report_text, decision = extract_decision(raw_response)

        # 8. 拼接最终输出
        if decision:
            report_text = report_text + format_decision(decision)

        # 9. 存储报告
        report = AnalysisReport(
            stock_code=stock_code,
            report_date=date.today(),
            content=report_text,
        )
        await self.report_repo.save_report(report)

        return report_text

    async def get_market_overview(self) -> str:
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
