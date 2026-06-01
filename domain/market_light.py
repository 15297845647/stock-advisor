"""市场红绿灯 — 涨跌家数 + 指数 + 涨跌停 三维评分

参考 daily_stock_analysis 的 MarketLightSnapshot：
- 涨跌家数权重 45%
- 指数涨跌权重 35%
- 涨跌停比例权重 20%
- 综合评分 0-100 → 绿/黄/红
"""

from dataclasses import dataclass


@dataclass
class MarketLight:
    score: int          # 0-100
    signal: str         # green / yellow / red
    breadth_score: int  # 涨跌家数得分 0-100
    index_score: int    # 指数得分 0-100
    limit_score: int    # 涨跌停得分 0-100
    rise_count: int
    fall_count: int
    limit_up: int
    limit_down: int
    index_change_pct: float
    summary: str


def compute_market_light(
    rise_count: int,
    fall_count: int,
    limit_up: int,
    limit_down: int,
    index_change_pct: float,
) -> MarketLight:
    total = rise_count + fall_count
    if total == 0:
        total = 1

    # 涨跌家数得分 (45%)
    rise_ratio = rise_count / total
    breadth_score = int(rise_ratio * 100)

    # 指数得分 (35%)
    if index_change_pct >= 1.0:
        index_score = 90
    elif index_change_pct >= 0.3:
        index_score = 70
    elif index_change_pct >= -0.3:
        index_score = 50
    elif index_change_pct >= -1.0:
        index_score = 30
    else:
        index_score = 10

    # 涨跌停比例得分 (20%)
    limit_total = limit_up + limit_down
    if limit_total == 0:
        limit_score = 50
    else:
        limit_score = int(limit_up / limit_total * 100)

    # 加权综合
    score = int(breadth_score * 0.45 + index_score * 0.35 + limit_score * 0.20)

    if score >= 65:
        signal = "green"
    elif score >= 40:
        signal = "yellow"
    else:
        signal = "red"

    # 生成摘要
    emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[signal]
    label = {"green": "偏多", "yellow": "震荡", "red": "偏空"}[signal]

    summary = (
        f"{emoji} 市场信号：{label}（{score}分）\n"
        f"  涨跌家数：{rise_count}涨 / {fall_count}跌（{rise_ratio:.0%}）\n"
        f"  指数涨跌：{index_change_pct:+.2f}%\n"
        f"  涨停{limit_up}家 / 跌停{limit_down}家"
    )

    return MarketLight(
        score=score, signal=signal,
        breadth_score=breadth_score, index_score=index_score, limit_score=limit_score,
        rise_count=rise_count, fall_count=fall_count,
        limit_up=limit_up, limit_down=limit_down,
        index_change_pct=index_change_pct, summary=summary,
    )
