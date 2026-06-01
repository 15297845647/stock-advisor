"""多空辩论服务 — Bull/Bear 对抗 → Manager 裁决

参考 TradingAgents-CN 的研究团队辩论机制：
1. Bull Researcher 构建看涨论证
2. Bear Researcher 构建看跌论证
3. Research Manager 综合裁决，输出最终决策
"""

import logging
from pathlib import Path

from domain.decision_parser import extract_decision, format_decision
from infrastructure.minimax_client import MiniMaxClient

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


class DebateService:
    def __init__(self):
        self.minimax = MiniMaxClient()

    async def run_debate(
        self,
        stock_code: str,
        stock_name: str,
        price: float,
        analysis_data: str,
    ) -> str:
        """运行完整辩论流程，返回格式化报告"""

        # 1. Bull 看涨论证
        bull_prompt = _load("debate_bull.txt").format(analysis_data=analysis_data)
        bull_argument = await self.minimax.chat(
            system_prompt="你是一位专业的看涨研究员，擅长发现投资机会。",
            messages=[{"role": "user", "content": bull_prompt}],
        )

        # 2. Bear 看跌论证
        bear_prompt = _load("debate_bear.txt").format(analysis_data=analysis_data)
        bear_argument = await self.minimax.chat(
            system_prompt="你是一位专业的看跌研究员，擅长识别风险。",
            messages=[{"role": "user", "content": bear_prompt}],
        )

        # 3. Manager 裁决
        judge_prompt = _load("debate_judge.txt").format(
            stock_name=stock_name,
            stock_code=stock_code,
            price=price,
            bull_argument=bull_argument,
            bear_argument=bear_argument,
        )
        judge_response = await self.minimax.chat(
            system_prompt="你是经验丰富的投资组合经理，善于综合多方观点做出理性决策。",
            messages=[{"role": "user", "content": judge_prompt}],
        )

        # 4. 风险三方评估
        risk_assessment = await self._risk_debate(judge_response, "\n".join([
            f"股票: {stock_name}({stock_code}) 价格: {price}",
            f"看涨要点: {bull_argument[:300]}",
            f"看跌要点: {bear_argument[:300]}",
        ]))

        # 5. 拼接报告
        report_lines = [
            f"📊 {stock_name}（{stock_code}）深度分析报告\n",
            "═══ 🟢 看涨论证 ═══",
            bull_argument,
            "\n═══ 🔴 看跌论证 ═══",
            bear_argument,
            "\n═══ ⚖️ 综合裁决 ═══",
        ]

        judge_text, decision = extract_decision(judge_response)
        report_lines.append(judge_text)

        if decision:
            report_lines.append(format_decision(decision))

        report_lines.append(f"\n═══ 🛡️ 风险评估 ═══")
        report_lines.append(risk_assessment)

        return "\n".join(report_lines)

    async def _risk_debate(self, decision_text: str, analysis_data: str) -> str:
        """三方风险评估 → Risk Manager 综合"""
        conservative = await self.minimax.chat(
            system_prompt="你是保守型风险分析师。",
            messages=[{"role": "user", "content": _load("risk_conservative.txt").format(
                decision_text=decision_text[:500], analysis_data=analysis_data,
            )}],
        )
        aggressive = await self.minimax.chat(
            system_prompt="你是激进型风险分析师。",
            messages=[{"role": "user", "content": _load("risk_aggressive.txt").format(
                decision_text=decision_text[:500], analysis_data=analysis_data,
            )}],
        )
        neutral = await self.minimax.chat(
            system_prompt="你是中性风险分析师。",
            messages=[{"role": "user", "content": _load("risk_neutral.txt").format(
                decision_text=decision_text[:500], analysis_data=analysis_data,
            )}],
        )

        manager_response = await self.minimax.chat(
            system_prompt="你是风险管理经理，善于综合多方意见给出务实的风控方案。",
            messages=[{"role": "user", "content": _load("risk_manager.txt").format(
                conservative=conservative[:400],
                aggressive=aggressive[:400],
                neutral=neutral[:400],
            )}],
        )

        return manager_response
