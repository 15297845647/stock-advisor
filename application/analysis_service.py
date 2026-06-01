"""分析编排 — 拉数据 → 计算指标 → MiniMax → 结构化决策

深度分析走 Bull/Bear 辩论 + Manager 裁决。
"""

import logging
from datetime import date

from application.debate_service import DebateService
from domain.decision_parser import extract_decision, format_decision
from domain.decision_stabilizer import stabilize_decision
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
        self.debate = DebateService()

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

        # 7. 解析结构化决策 + 规则层校准
        report_text, decision = extract_decision(raw_response)

        if decision:
            result = stabilize_decision(decision, quote.price, tech, fund_flows)
            if result.adjusted:
                report_text += f"\n\n⚠️ 决策校准：{result.reason}"
            report_text += format_decision(result.decision)

        # 9. 存储报告
        report = AnalysisReport(
            stock_code=stock_code,
            report_date=date.today(),
            content=report_text,
        )
        await self.report_repo.save_report(report)

        return report_text

    async def analyze_stock_deep(self, stock_code: str) -> str:
        """深度分析 — Bull/Bear 辩论 + Manager 裁决（耗时约 30s）"""

        quote = await self.akshare.get_realtime_quote(stock_code)
        if not quote:
            return f"未找到股票 {stock_code} 的行情数据。"

        bars = await self.akshare.get_stock_history(stock_code, days=60)
        tech = analyze_technical(bars) if bars else None
        fund_flows = await self.akshare.get_fund_flow(stock_code)
        news = await self.akshare.get_stock_news(stock_code, limit=15)

        # 拼接分析数据文本（供 Bull/Bear 使用）
        analysis_prompt = build_analysis_prompt(
            quote, tech, fund_flows, bars, news
        ) if tech else f"股票: {quote.name}({stock_code}) 价格: {quote.price}"

        report = await self.debate.run_debate(
            stock_code=stock_code,
            stock_name=quote.name,
            price=quote.price,
            analysis_data=analysis_prompt,
        )

        # 存报告
        await self.report_repo.save_report(AnalysisReport(
            stock_code=stock_code, report_date=date.today(), content=report,
        ))

        return report

    async def analyze_futures(self, text: str) -> str:
        """期货品种分析 — 从文本识别品种 → 拉数据 → MiniMax 分析"""
        resolved = self.akshare.resolve_futures_code(text)
        if not resolved:
            return "未识别到期货品种。支持的品种：欧线集运、螺纹钢、铁矿石、原油、黄金、白银、铜、豆粕、焦煤、焦炭、纯碱、玻璃等。"

        symbol, name = resolved

        # 实时行情
        quote = await self.akshare.get_futures_quote(symbol, name)

        # 日K
        bars = await self.akshare.get_futures_history(symbol, days=60)

        # 技术指标
        tech = analyze_technical(bars) if bars else None

        # 构造 prompt
        lines = [f"请对以下期货品种进行综合分析：\n"]
        lines.append(f"【品种信息】")
        lines.append(f"- 品种：{name}（{symbol}）")
        if quote:
            lines.append(f"- 最新价：{quote.price}  涨跌幅：{quote.change_pct:+.2f}%")
            lines.append(f"- 今开：{quote.open_price}  最高：{quote.high}  最低：{quote.low}")

        if tech:
            lines.append(f"\n【技术指标】")
            lines.append(f"- 均线：MA5={tech.ma5} MA10={tech.ma10} MA20={tech.ma20}")
            lines.append(f"- MACD：DIF={tech.macd} DEA={tech.macd_signal} 柱状={tech.macd_hist}")
            lines.append(f"- RSI(14)：{tech.rsi_14}")
            lines.append(f"- 趋势：{tech.trend}  支撑：{tech.support}  压力：{tech.resistance}")

        if bars:
            lines.append(f"\n【近10日K线】")
            for b in bars[-10:]:
                lines.append(
                    f"  {b.trade_date}: 开{b.open} 高{b.high} 低{b.low} "
                    f"收{b.close} 量{b.volume:.0f} 涨跌{b.change_pct:+.2f}%"
                )

        lines.append(f"\n请分析趋势、关键价位、技术信号，给出操作建议。")
        lines.append(
            f"在最后输出结构化决策："
            f'[DECISION]{{"action":"做多/做空/观望","target_price":目标价,'
            f'"stop_loss":止损价,"confidence":置信度0到100,'
            f'"risk_score":风险1到10,"reasoning":"理由",'
            f'"key_points":["要点1","要点2"]}}[/DECISION]'
        )

        from domain.decision_parser import extract_decision, format_decision
        system = build_system_prompt()
        raw = await self.minimax.chat(
            system_prompt=system,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )

        report_text, decision = extract_decision(raw)
        if decision:
            report_text = report_text + format_decision(decision)

        return report_text

    async def get_market_overview(self) -> str:
        """大盘概览 + 市场红绿灯"""
        from domain.market_light import compute_market_light

        indices = [
            ("000001", "上证指数"),
            ("399001", "深证成指"),
            ("399006", "创业板指"),
        ]
        lines = ["📊 今日大盘概览\n"]
        sh_change = 0.0
        for code, name in indices:
            q = await self.akshare.get_market_index(code)
            if q:
                arrow = "🔴" if q.change_pct >= 0 else "🟢"
                lines.append(f"{arrow} {name}: {q.price:.2f} ({q.change_pct:+.2f}%)")
                if code == "000001":
                    sh_change = q.change_pct

        # 市场红绿灯
        breadth = await self.akshare.get_market_breadth()
        if breadth["rise"] + breadth["fall"] > 0:
            light = compute_market_light(
                rise_count=breadth["rise"], fall_count=breadth["fall"],
                limit_up=breadth["limit_up"], limit_down=breadth["limit_down"],
                index_change_pct=sh_change,
            )
            lines.append(f"\n{light.summary}")

        lines.append("\n以上分析仅供参考，不构成投资建议，投资有风险，入市需谨慎。")
        return "\n".join(lines)
