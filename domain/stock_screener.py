"""条件筛选引擎 — 技术指标 + 基本面组合筛选

参考 TradingAgents-CN 的 DSL 条件树，支持：
- 比较操作: >, <, >=, <=, ==, between
- 交叉检测: cross_up (金叉), cross_down (死叉)
- 组合逻辑: AND / OR
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScreenCondition:
    """筛选条件"""
    field: str        # ma5, rsi_14, change_pct, price, volume...
    op: str           # >, <, >=, <=, ==, between, cross_up, cross_down
    value: float | None = None
    value2: float | None = None   # between 的上界
    right_field: str | None = None  # cross_up/down 的右侧字段


# 预设筛选策略
PRESET_STRATEGIES = {
    "金叉选股": {
        "name": "MACD金叉 + RSI未超买",
        "conditions": [
            ScreenCondition(field="macd_hist", op=">", value=0),
            ScreenCondition(field="rsi_14", op="<", value=70),
            ScreenCondition(field="change_pct", op=">", value=0),
        ],
    },
    "超跌反弹": {
        "name": "RSI超卖 + 价格接近支撑",
        "conditions": [
            ScreenCondition(field="rsi_14", op="<", value=30),
            ScreenCondition(field="change_pct", op="<", value=-2),
        ],
    },
    "强势突破": {
        "name": "价格站上MA20 + 放量 + MACD多头",
        "conditions": [
            ScreenCondition(field="price_above_ma20", op="==", value=1),
            ScreenCondition(field="macd_hist", op=">", value=0),
            ScreenCondition(field="volume_ratio", op=">", value=1.5),
        ],
    },
    "均线多头": {
        "name": "MA5>MA10>MA20 均线多头排列",
        "conditions": [
            ScreenCondition(field="ma5", op=">", right_field="ma10"),
            ScreenCondition(field="ma10", op=">", right_field="ma20"),
        ],
    },
}


def evaluate_condition(
    condition: ScreenCondition,
    indicators: dict,
    prev_indicators: dict | None = None,
) -> bool:
    """评估单个条件"""
    val = indicators.get(condition.field)
    if val is None:
        return False

    if condition.op in (">", "<", ">=", "<=", "=="):
        # 字段间比较
        if condition.right_field:
            right_val = indicators.get(condition.right_field)
            if right_val is None:
                return False
            compare_val = right_val
        else:
            compare_val = condition.value
            if compare_val is None:
                return False

        if condition.op == ">":
            return val > compare_val
        elif condition.op == "<":
            return val < compare_val
        elif condition.op == ">=":
            return val >= compare_val
        elif condition.op == "<=":
            return val <= compare_val
        elif condition.op == "==":
            return val == compare_val

    elif condition.op == "between":
        return condition.value <= val <= condition.value2

    elif condition.op in ("cross_up", "cross_down"):
        # 需要前一根 K 线数据
        if not prev_indicators:
            return False
        prev_val = prev_indicators.get(condition.field)
        right_field = condition.right_field or ""
        right_val = indicators.get(right_field)
        prev_right = prev_indicators.get(right_field)

        if None in (prev_val, right_val, prev_right):
            return False

        if condition.op == "cross_up":
            return prev_val <= prev_right and val > right_val
        else:
            return prev_val >= prev_right and val < right_val

    return False


def evaluate_all(
    conditions: list[ScreenCondition],
    indicators: dict,
    prev_indicators: dict | None = None,
    logic: str = "AND",
) -> bool:
    """评估所有条件（AND/OR 逻辑）"""
    results = [evaluate_condition(c, indicators, prev_indicators) for c in conditions]

    if logic == "AND":
        return all(results)
    return any(results)


def get_preset_names() -> list[str]:
    return list(PRESET_STRATEGIES.keys())


def get_preset(name: str) -> dict | None:
    return PRESET_STRATEGIES.get(name)
