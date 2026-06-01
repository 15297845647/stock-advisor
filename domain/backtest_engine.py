"""回测引擎 — 评估历史 LLM 建议 vs 实际涨跌

参考 daily_stock_analysis 的 BacktestEngine：
- 从 analysis_reports 取历史建议
- 用后 N 日 K 线评估方向是否正确、是否触达目标/止损
- 汇总胜率和方向准确率
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger(__name__)

_DECISION_RE = re.compile(r"\[DECISION\]\s*(\{.*?\})\s*\[/DECISION\]", re.DOTALL)


@dataclass
class BacktestRecord:
    stock_code: str
    report_date: date
    action: str
    confidence: float
    target_price: float
    stop_loss: float
    entry_price: float


@dataclass
class BacktestEval:
    record: BacktestRecord
    exit_price: float
    actual_return_pct: float
    direction_correct: bool
    hit_target: bool
    hit_stop_loss: bool
    eval_window_days: int


@dataclass
class BacktestSummary:
    total: int
    evaluated: int
    direction_accuracy: float
    win_rate: float
    avg_return_pct: float
    target_hit_rate: float
    stop_loss_hit_rate: float


def extract_decision_from_report(report_content: str) -> dict | None:
    """从历史报告中提取 [DECISION] JSON"""
    match = _DECISION_RE.search(report_content)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def infer_expected_direction(action: str) -> str | None:
    """推断预期方向：up / down / None(观望)"""
    action = action.strip()
    buy_kw = {"买入", "做多", "加仓", "BUY", "LONG"}
    sell_kw = {"卖出", "做空", "减仓", "清仓", "SELL", "SHORT"}

    if any(k in action for k in buy_kw):
        return "up"
    if any(k in action for k in sell_kw):
        return "down"
    return None


def evaluate_single(
    record: BacktestRecord,
    future_bars: list[dict],
    window_days: int = 5,
) -> BacktestEval | None:
    """用后 N 日 K 线评估单条建议"""
    if not future_bars:
        return None

    bars = future_bars[:window_days]
    if not bars:
        return None

    direction = infer_expected_direction(record.action)
    if direction is None:
        return None

    # 窗口内最高/最低/收盘
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    exit_price = bars[-1]["close"]
    max_price = max(highs)
    min_price = min(lows)

    actual_return = (exit_price - record.entry_price) / record.entry_price * 100

    # 方向是否正确
    if direction == "up":
        direction_correct = exit_price > record.entry_price
    else:
        direction_correct = exit_price < record.entry_price

    # 目标价触达
    hit_target = False
    if record.target_price > 0:
        if direction == "up":
            hit_target = max_price >= record.target_price
        else:
            hit_target = min_price <= record.target_price

    # 止损触达
    hit_stop_loss = False
    if record.stop_loss > 0:
        if direction == "up":
            hit_stop_loss = min_price <= record.stop_loss
        else:
            hit_stop_loss = max_price >= record.stop_loss

    return BacktestEval(
        record=record,
        exit_price=exit_price,
        actual_return_pct=round(actual_return, 2),
        direction_correct=direction_correct,
        hit_target=hit_target,
        hit_stop_loss=hit_stop_loss,
        eval_window_days=len(bars),
    )


def compute_summary(evals: list[BacktestEval]) -> BacktestSummary | None:
    if not evals:
        return None

    total = len(evals)
    correct = sum(1 for e in evals if e.direction_correct)
    wins = sum(1 for e in evals if e.actual_return_pct > 0)
    targets = sum(1 for e in evals if e.hit_target)
    stops = sum(1 for e in evals if e.hit_stop_loss)
    avg_ret = sum(e.actual_return_pct for e in evals) / total

    return BacktestSummary(
        total=total,
        evaluated=total,
        direction_accuracy=round(correct / total * 100, 1),
        win_rate=round(wins / total * 100, 1),
        avg_return_pct=round(avg_ret, 2),
        target_hit_rate=round(targets / total * 100, 1),
        stop_loss_hit_rate=round(stops / total * 100, 1),
    )


def format_summary(summary: BacktestSummary) -> str:
    return (
        f"📈 回测报告（近 {summary.total} 条建议）\n\n"
        f"方向准确率：{summary.direction_accuracy}%\n"
        f"胜率（盈利比例）：{summary.win_rate}%\n"
        f"平均收益：{summary.avg_return_pct:+.2f}%\n"
        f"目标价触达率：{summary.target_hit_rate}%\n"
        f"止损触发率：{summary.stop_loss_hit_rate}%"
    )
