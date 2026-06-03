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
        logger.info("[%s] 实时行情: %s", stock_code, "OK" if quote else "FAIL")

        # 2. 日K
        bars = await self.akshare.get_stock_history(stock_code, days=60)
        logger.info("[%s] 日K: %d 条", stock_code, len(bars))
        if bars:
            await self.stock_repo.save_daily_bars(bars)

        # 3. 技术指标
        tech = analyze_technical(bars) if bars else None
        logger.info("[%s] 技术指标: %s", stock_code, "OK" if tech else "FAIL")

        # 4. 资金流向
        fund_flows = await self.akshare.get_fund_flow(stock_code)
        logger.info("[%s] 资金流: %d 条", stock_code, len(fund_flows))

        # 5. 新闻
        news = await self.akshare.get_stock_news(stock_code, limit=20)
        logger.info("[%s] 新闻: %d 条", stock_code, len(news))

        # 6. 基本面
        fundamentals = await self.akshare.get_fundamentals(stock_code)
        logger.info("[%s] 基本面: %s", stock_code, "OK" if fundamentals else "FAIL")

        # 7. 构造 prompt（数据不全也能分析，降级为纯 LLM）
        system = build_system_prompt()
        if tech and quote:
            analysis_prompt = build_analysis_prompt(quote, tech, fund_flows, bars, news, fundamentals)
        elif quote:
            # 有行情无技术指标 → 简化 prompt
            analysis_prompt = (
                f"请分析股票 {quote.name}({stock_code})，"
                f"当前价格 {quote.price}，涨跌幅 {quote.change_pct:+.2f}%。"
                f"日K数据暂时无法获取，请基于已知信息给出分析。"
            )
        else:
            # 行情也拉不到 → 纯 LLM
            analysis_prompt = (
                f"请分析股票代码 {stock_code}，"
                f"行情数据暂时无法获取，请基于你的知识给出该股票的基本面和近期走势分析。"
            )

        raw_response = await self.minimax.chat(
            system_prompt=system,
            messages=[{"role": "user", "content": analysis_prompt}],
        )

        # 7. 解析结构化决策 + 规则层校准
        report_text, decision = extract_decision(raw_response)

        if decision and tech and quote:
            result = stabilize_decision(decision, quote.price, tech, fund_flows)
            if result.adjusted:
                report_text += f"\n\n⚠️ 决策校准：{result.reason}"
            report_text += format_decision(result.decision)
        elif decision:
            report_text += format_decision(decision)

        # 9. 存储报告
        report = AnalysisReport(
            stock_code=stock_code,
            report_date=date.today(),
            content=report_text,
        )
        await self.report_repo.save_report(report)

        return report_text

    async def analyze_stock_deep(self, stock_code: str) -> str:
        """深度分析 — 4分析师并行 → Bull/Bear 辩论 → 风控（耗时约 40s）"""
        from application.analyst_agents import AnalystPipeline

        quote = await self.akshare.get_realtime_quote(stock_code)
        if not quote:
            return f"未找到股票 {stock_code} 的行情数据。"

        bars = await self.akshare.get_stock_history(stock_code, days=60)
        tech = analyze_technical(bars) if bars else None
        fund_flows = await self.akshare.get_fund_flow(stock_code)
        news = await self.akshare.get_stock_news(stock_code, limit=15)
        fundamentals = await self.akshare.get_fundamentals(stock_code)

        # 构建各维度文本
        tech_text = self._build_tech_text(tech) if tech else "技术数据不可用"
        fund_flow_text = self._build_fund_flow_text(fund_flows)
        kline_text = self._build_kline_text(bars)
        news_text = self._build_news_text(news)
        fundamental_text = self._build_fundamental_text(fundamentals)

        pipeline = AnalystPipeline()
        report = await pipeline.run(
            stock_name=quote.name,
            stock_code=stock_code,
            price=quote.price,
            tech_text=tech_text,
            fundamental_text=fundamental_text,
            news_text=news_text,
            fund_flow_text=fund_flow_text,
            kline_text=kline_text,
            tech_snapshot=tech,
            fund_flows=fund_flows,
        )

        await self.report_repo.save_report(AnalysisReport(
            stock_code=stock_code, report_date=date.today(), content=report,
        ))

        return report

    # ── 文本构建辅助方法 ──

    @staticmethod
    def _build_tech_text(tech) -> str:
        ma60 = f"MA60={tech.ma60}" if tech.ma60 else "MA60=数据不足"
        return (
            f"均线：MA5={tech.ma5} MA10={tech.ma10} MA20={tech.ma20} {ma60}\n"
            f"MACD：DIF={tech.macd} DEA={tech.macd_signal} 柱状={tech.macd_hist}\n"
            f"RSI(14)：{tech.rsi_14}\n"
            f"KDJ：K={tech.kdj_k} D={tech.kdj_d} J={tech.kdj_j}\n"
            f"布林带：上轨={tech.boll_upper} 中轨={tech.boll_mid} 下轨={tech.boll_lower}\n"
            f"趋势：{tech.trend}  支撑：{tech.support}  压力：{tech.resistance}"
        )

    @staticmethod
    def _build_fund_flow_text(fund_flows) -> str:
        if not fund_flows:
            return "暂无数据"
        lines = []
        for f in fund_flows:
            lines.append(
                f"{f.trade_date}: 主力净流入{f.main_net_inflow/1e4:.0f}万 "
                f"超大单{f.super_large_net/1e4:.0f}万 大单{f.large_net/1e4:.0f}万"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_kline_text(bars) -> str:
        if not bars:
            return "暂无K线数据"
        recent = bars[-10:] if len(bars) >= 10 else bars
        lines = []
        for b in recent:
            lines.append(
                f"{b.trade_date}: 开{b.open} 高{b.high} 低{b.low} "
                f"收{b.close} 量{b.volume:.0f} 涨跌{b.change_pct:+.2f}%"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_news_text(news) -> str:
        if not news:
            return "暂无近期新闻"
        lines = []
        for n in news[:15]:
            tag = "📰" if n.news_type == "news" else "📋"
            lines.append(f"{tag} [{n.time}] {n.title}（{n.source}）")
        return "\n".join(lines)

    @staticmethod
    def _build_fundamental_text(fundamentals) -> str:
        if not fundamentals:
            return "暂无基本面数据"
        lines = []
        if fundamentals.industry:
            lines.append(f"行业：{fundamentals.industry}")
        if fundamentals.total_market_cap:
            lines.append(f"总市值：{fundamentals.total_market_cap/1e8:.1f}亿")
        if fundamentals.pe_ratio:
            lines.append(f"PE(市盈率)：{fundamentals.pe_ratio:.1f}")
        if fundamentals.pb_ratio:
            lines.append(f"PB(市净率)：{fundamentals.pb_ratio:.2f}")
        if fundamentals.roe:
            lines.append(f"ROE：{fundamentals.roe:.1f}%")
        if fundamentals.revenue:
            lines.append(f"营收：{fundamentals.revenue/1e8:.1f}亿 同比{fundamentals.revenue_growth:+.1f}%")
        if fundamentals.net_profit:
            lines.append(f"净利润：{fundamentals.net_profit/1e8:.1f}亿 同比{fundamentals.profit_growth:+.1f}%")
        if fundamentals.eps:
            lines.append(f"每股收益：{fundamentals.eps:.2f}元")
        if fundamentals.debt_ratio:
            lines.append(f"资产负债率：{fundamentals.debt_ratio:.1f}%")
        if fundamentals.report_period:
            lines.append(f"最新报告期：{fundamentals.report_period}")
        return "\n".join(lines) if lines else "暂无基本面数据"

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
