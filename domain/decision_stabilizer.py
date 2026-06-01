"""决策校准器 — 用资金流 + 支撑阻力位规则层修正 LLM 过激买卖

参考 daily_stock_analysis 的 stabilize_decision_with_structure：
- 价在阻力附近 + 无资金确认 → 买入降级为观望
- 价在支撑附近 + 资金未流出 → 卖出降级为持有
- 资金流缺失 → 买入降低置信度
"""

import logging
from dataclasses import dataclass

from domain.models.stock import FundFlow, StockDecision
from domain.stock_analyzer import TechnicalSnapshot

logger = logging.getLogger(__name__)


@dataclass
class StabilizeResult:
    decision: StockDecision
    adjusted: bool = False
    reason: str = ""


def _capital_flow_bias(flows: list[FundFlow]) -> str:
    """判断近期资金流向：inflow / outflow / neutral / unavailable"""
    if not flows:
        return "unavailable"

    recent = flows[-3:] if len(flows) >= 3 else flows
    total_main = sum(f.main_net_inflow for f in recent)

    if total_main > 5_000_000:
        return "inflow"
    elif total_main < -5_000_000:
        return "outflow"
    return "neutral"


def _near_resistance(price: float, resistance: float, threshold: float = 0.02) -> bool:
    """价格是否接近阻力位（2%以内）"""
    if not resistance or resistance <= 0:
        return False
    return (resistance - price) / price < threshold


def _near_support(price: float, support: float, threshold: float = 0.02) -> bool:
    """价格是否接近支撑位（2%以内）"""
    if not support or support <= 0:
        return False
    return (price - support) / price < threshold


def stabilize_decision(
    decision: StockDecision,
    price: float,
    tech: TechnicalSnapshot | None,
    fund_flows: list[FundFlow],
) -> StabilizeResult:
    """对 LLM 决策做规则层校准"""
    if not tech:
        return StabilizeResult(decision=decision)

    flow_bias = _capital_flow_bias(fund_flows)
    adjusted = decision
    reasons = []

    # 规则1: 买入 + 价在阻力附近 + 无资金确认 → 降级为持有
    if decision.action == "买入" and _near_resistance(price, tech.resistance):
        if flow_bias != "inflow":
            adjusted = StockDecision(
                action="持有",
                target_price=decision.target_price,
                stop_loss=decision.stop_loss,
                confidence=min(decision.confidence, 40),
                risk_score=max(decision.risk_score, 6),
                reasoning=f"价格接近阻力位{tech.resistance}且无资金确认，买入降级为观望",
                key_points=decision.key_points + ["⚠️ 阻力位附近+资金面不支持"],
            )
            reasons.append(f"阻力位{tech.resistance}附近无资金流入确认")

    # 规则2: 卖出 + 价在支撑附近 + 资金未流出 → 降级为持有
    if decision.action == "卖出" and _near_support(price, tech.support):
        if flow_bias != "outflow":
            adjusted = StockDecision(
                action="持有",
                target_price=decision.target_price,
                stop_loss=decision.stop_loss,
                confidence=min(decision.confidence, 50),
                risk_score=decision.risk_score,
                reasoning=f"价格接近支撑位{tech.support}且资金未明显流出，暂不建议卖出",
                key_points=decision.key_points + ["⚠️ 支撑位附近+资金面未恶化"],
            )
            reasons.append(f"支撑位{tech.support}附近资金未流出")

    # 规则3: 买入 + 资金流缺失 → 降低置信度
    if decision.action == "买入" and flow_bias == "unavailable":
        adjusted = StockDecision(
            action=decision.action,
            target_price=decision.target_price,
            stop_loss=decision.stop_loss,
            confidence=max(decision.confidence - 15, 10),
            risk_score=min(decision.risk_score + 1, 10),
            reasoning=decision.reasoning,
            key_points=decision.key_points + ["⚠️ 资金流数据缺失，置信度下调"],
        )
        reasons.append("资金流数据不可用")

    # 规则4: 买入 + RSI > 80 → 超买警告
    if decision.action == "买入" and tech.rsi_14 > 80:
        adjusted = StockDecision(
            action="持有",
            target_price=decision.target_price,
            stop_loss=decision.stop_loss,
            confidence=min(decision.confidence, 30),
            risk_score=max(decision.risk_score, 7),
            reasoning=f"RSI={tech.rsi_14:.0f}严重超买，不宜追高",
            key_points=decision.key_points + [f"⚠️ RSI {tech.rsi_14:.0f} 超买"],
        )
        reasons.append(f"RSI {tech.rsi_14:.0f} 超买区域")

    # 规则5: 卖出 + RSI < 20 → 超卖保护
    if decision.action == "卖出" and tech.rsi_14 < 20:
        adjusted = StockDecision(
            action="持有",
            target_price=decision.target_price,
            stop_loss=decision.stop_loss,
            confidence=min(decision.confidence, 40),
            risk_score=decision.risk_score,
            reasoning=f"RSI={tech.rsi_14:.0f}严重超卖，恐慌卖出不可取",
            key_points=decision.key_points + [f"⚠️ RSI {tech.rsi_14:.0f} 超卖"],
        )
        reasons.append(f"RSI {tech.rsi_14:.0f} 超卖区域")

    is_adjusted = adjusted is not decision
    reason_text = "；".join(reasons) if reasons else ""

    if is_adjusted:
        logger.info("决策校准: %s → %s (%s)", decision.action, adjusted.action, reason_text)

    return StabilizeResult(decision=adjusted, adjusted=is_adjusted, reason=reason_text)
