"""多 Agent 分析流水线 — 4 分析师并行 → Bull/Bear 辩论 → 风控

参考 TradingAgents-CN 的 5 团队架构，适配 MiniMax 单模型：
1. 技术分析师 — 均线/MACD/KDJ/支撑阻力
2. 基本面分析师 — 财务/估值/行业
3. 消息面分析师 — 新闻/公告解读
4. 资金面分析师 — 资金流向/主力动向
→ Bull/Bear 辩论 → Manager 裁决 → 三方风控
"""

import asyncio
import logging

from domain.decision_parser import extract_decision, format_verdict
from domain.decision_stabilizer import stabilize_decision
from infrastructure.minimax_client import MiniMaxClient

logger = logging.getLogger(__name__)


class AnalystPipeline:
    def __init__(self):
        self.minimax = MiniMaxClient()

    async def run(
        self,
        stock_name: str,
        stock_code: str,
        price: float,
        tech_text: str,
        fundamental_text: str,
        news_text: str,
        fund_flow_text: str,
        kline_text: str,
        tech_snapshot=None,
        fund_flows=None,
    ) -> str:
        """完整分析管线：4分析师并行 → 多空辩论 → Manager 裁决 → 结论版输出"""

        # Phase 1: 4 分析师并行
        tasks = [
            self._technical_analyst(tech_text, kline_text, stock_name),
            self._fundamental_analyst(fundamental_text, stock_name),
            self._news_analyst(news_text, stock_name),
            self._capital_analyst(fund_flow_text, stock_name),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tech_report = results[0] if not isinstance(results[0], Exception) else "技术分析不可用"
        fund_report = results[1] if not isinstance(results[1], Exception) else "基本面分析不可用"
        news_report = results[2] if not isinstance(results[2], Exception) else "消息面分析不可用"
        capital_report = results[3] if not isinstance(results[3], Exception) else "资金面分析不可用"

        combined_data = (
            f"股票：{stock_name}（{stock_code}）当前价：{price}\n\n"
            f"【技术面分析】\n{tech_report}\n\n"
            f"【基本面分析】\n{fund_report}\n\n"
            f"【消息面分析】\n{news_report}\n\n"
            f"【资金面分析】\n{capital_report}"
        )

        # Phase 2: Bull/Bear 辩论
        bull, bear = await asyncio.gather(
            self._bull_researcher(combined_data),
            self._bear_researcher(combined_data),
        )

        # Phase 3: Manager 裁决
        judge = await self._judge(stock_name, stock_code, price, bull, bear)

        # 输出结论版：内部多 agent 论证不展开，只给可执行要点
        return self._build_verdict_report(
            stock_name, stock_code, price, judge, tech_snapshot, fund_flows
        )

    def _build_verdict_report(
        self, stock_name, stock_code, price, judge, tech_snapshot, fund_flows
    ) -> str:
        """从裁决结果提炼结论版报告（小白友好）"""
        header = f"📊 {stock_name}（{stock_code}）\n"

        _, decision = extract_decision(judge)
        if not decision:
            return header + "本次分析未生成明确操作建议，建议保持观望。"

        # 有完整技术面+资金面 → 先过风控规则校准
        if tech_snapshot and fund_flows is not None:
            result = stabilize_decision(decision, price, tech_snapshot, fund_flows)
            body = format_verdict(result.decision, price, tech_snapshot)
            if result.adjusted:
                body += "\n（已按风控规则自动校准）"
            return header + body

        return header + format_verdict(decision, price, tech_snapshot)

    async def _technical_analyst(self, tech_text: str, kline_text: str, name: str) -> str:
        return await self.minimax.chat(
            system_prompt="你是专业的技术分析师，擅长趋势研判和关键价位识别。简洁输出。",
            messages=[{"role": "user", "content":
                f"请对{name}做技术面分析：\n{tech_text}\n\n近期K线：\n{kline_text}\n\n"
                f"输出：1.趋势判断 2.关键价位 3.技术信号 4.短期方向判断"}],
        )

    async def _fundamental_analyst(self, fundamental_text: str, name: str) -> str:
        return await self.minimax.chat(
            system_prompt="你是专业的基本面分析师，擅长财务分析和估值判断。简洁输出。",
            messages=[{"role": "user", "content":
                f"请对{name}做基本面分析：\n{fundamental_text}\n\n"
                f"输出：1.估值水平 2.盈利能力 3.成长性 4.财务健康度"}],
        )

    async def _news_analyst(self, news_text: str, name: str) -> str:
        return await self.minimax.chat(
            system_prompt="你是专业的消息面分析师，擅长解读新闻和公告对股价的影响。简洁输出。",
            messages=[{"role": "user", "content":
                f"请解读{name}的近期消息面：\n{news_text}\n\n"
                f"输出：1.利好因素 2.利空因素 3.消息面总体倾向(利好/利空/中性)"}],
        )

    async def _capital_analyst(self, fund_flow_text: str, name: str) -> str:
        return await self.minimax.chat(
            system_prompt="你是专业的资金面分析师，擅长分析主力资金动向。简洁输出。",
            messages=[{"role": "user", "content":
                f"请分析{name}的资金流向：\n{fund_flow_text}\n\n"
                f"输出：1.主力动向 2.资金面强弱 3.是否有主力建仓/出货迹象"}],
        )

    async def _bull_researcher(self, data: str) -> str:
        return await self.minimax.chat(
            system_prompt="你是看涨研究员，擅长发现投资机会。基于数据构建论据，不要空泛。",
            messages=[{"role": "user", "content":
                f"基于以下四维分析，构建看涨论证：\n{data}\n\n"
                f"要求：1.核心看涨逻辑 2.上涨催化剂 3.目标价位 4.反驳看空观点 5.看涨置信度(0-100)"}],
        )

    async def _bear_researcher(self, data: str) -> str:
        return await self.minimax.chat(
            system_prompt="你是看跌研究员，擅长识别风险。基于数据构建论据，不要空泛。",
            messages=[{"role": "user", "content":
                f"基于以下四维分析，构建看跌论证：\n{data}\n\n"
                f"要求：1.核心风险因素 2.下跌催化剂 3.止损价位 4.反驳看涨观点 5.看跌置信度(0-100)"}],
        )

    async def _judge(self, name: str, code: str, price: float, bull: str, bear: str) -> str:
        return await self.minimax.chat(
            system_prompt="你是投资组合经理，善于综合多方观点做出理性决策。",
            messages=[{"role": "user", "content":
                f"股票：{name}（{code}）当前价：{price}\n\n"
                f"【看涨论证】\n{bull[:800]}\n\n【看跌论证】\n{bear[:800]}\n\n"
                f"请综合裁决：1.评估双方论证质量 2.做出投资决策 3.给出目标价和止损价\n\n"
                f"在最后输出：\n"
                f'[DECISION]{{"action":"买入/卖出/持有","target_price":目标价,'
                f'"stop_loss":止损价,"confidence":置信度0到100,'
                f'"risk_score":风险1到10,"reasoning":"理由",'
                f'"key_points":["要点1","要点2","要点3"]}}[/DECISION]'}],
        )

