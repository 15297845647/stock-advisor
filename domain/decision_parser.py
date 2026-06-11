"""从 MiniMax 分析响应中解析 [DECISION] 结构化决策"""

import json
import logging
import re

from domain.models.stock import StockDecision

logger = logging.getLogger(__name__)

_DECISION_RE = re.compile(r"\[DECISION\]\s*(\{.*?\})\s*\[/DECISION\]", re.DOTALL)


def extract_decision(response: str) -> tuple[str, StockDecision | None]:
    """从响应中提取结构化决策，返回 (清理后文本, 决策对象)"""
    match = _DECISION_RE.search(response)
    if not match:
        return response, None

    clean_text = _DECISION_RE.sub("", response).strip()

    try:
        data = json.loads(match.group(1))
        decision = StockDecision(
            action=data.get("action", "持有"),
            target_price=float(data.get("target_price", 0)),
            stop_loss=float(data.get("stop_loss", 0)),
            confidence=float(data.get("confidence", 50)),
            risk_score=int(data.get("risk_score", 5)),
            reasoning=data.get("reasoning", ""),
            key_points=data.get("key_points", []),
        )
        return clean_text, decision
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("解析 DECISION 标记失败: %s", e)
        return clean_text, None


def _risk_label(risk_score: int) -> str:
    """风险评分(1-10) → 小白可读等级"""
    if risk_score <= 3:
        return "低"
    if risk_score <= 6:
        return "中"
    return "高"


def format_verdict(decision: StockDecision, price: float, tech=None) -> str:
    """面向非专业用户的结论版输出 — 只给可执行要点，不展开论证

    tech 为技术面快照（含 support），仅用于推导买入区间下沿。
    """
    action_label = {
        "买入": "🟢 建议买入",
        "卖出": "🔴 建议卖出",
        "持有": "🟡 建议持有",
    }.get(decision.action, f"⚪ {decision.action}")

    lines = [action_label, f"现价：{price:.2f}"]

    # 买入区间：支撑位（无效则现价下浮3%）到现价
    if decision.action == "买入":
        support = getattr(tech, "support", None)
        lower = support if (support and 0 < support < price) else round(price * 0.97, 2)
        lines.append(f"建议买入区间：{lower:.2f} ~ {price:.2f}")

    if decision.target_price > 0:
        lines.append(f"目标价：{decision.target_price:.2f}")
    if decision.stop_loss > 0:
        lines.append(f"止损价：{decision.stop_loss:.2f}")

    lines.append(f"风险等级：{_risk_label(decision.risk_score)}（{decision.risk_score}/10）")

    # 理由只取首句，避免冗长
    if decision.reasoning:
        first_sentence = decision.reasoning.split("。")[0].strip()
        if first_sentence:
            lines.append(f"理由：{first_sentence}")

    lines.append("仅供参考，不构成投资建议。")
    return "\n".join(lines)


def format_decision(decision: StockDecision) -> str:
    """将结构化决策格式化为用户可读文本"""
    action_emoji = {"买入": "🟢", "卖出": "🔴", "持有": "🟡"}.get(decision.action, "⚪")

    lines = [
        f"\n{'─' * 30}",
        f"{action_emoji} 操作建议：{decision.action}",
        f"📊 置信度：{decision.confidence:.0f}%  风险评分：{decision.risk_score}/10",
    ]

    if decision.target_price > 0:
        lines.append(f"🎯 目标价：{decision.target_price:.2f}")
    if decision.stop_loss > 0:
        lines.append(f"🛑 止损价：{decision.stop_loss:.2f}")

    if decision.key_points:
        lines.append("📌 要点：")
        for p in decision.key_points:
            lines.append(f"   • {p}")

    if decision.reasoning:
        lines.append(f"💡 {decision.reasoning}")

    return "\n".join(lines)
