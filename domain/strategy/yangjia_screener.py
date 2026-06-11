"""养家最笨选股法 — 纯领域筛选逻辑，无 IO

可量化的三条硬规则：
- 规则1：近 N 天内曾涨停，但排除连板数 >= 阈值的（防资金获利跑掉）
- 规则2：量比 > 阈值（活跃度，由调用方在全量行情阶段先筛）
- 规则4：未跌破 5 日线（5 日线是生命线）
操作时点规则（3/5）为提示文本，不在此量化。
"""

from dataclasses import dataclass, field

from domain.models.stock import StockDailyBar
from domain.strategy.strategy_config import YangjiaConfig

# 涨停阈值按板块区分，留 buffer 防四舍五入误差
_LIMIT_MAIN = 9.8     # 主板 10%
_LIMIT_GROWTH = 19.8  # 创业板(300/301)、科创板(688) 20%
_LIMIT_BJ = 29.8      # 北交所(8/4/920) 30%


def limit_up_threshold(code: str) -> float:
    """按股票代码前缀返回涨停判定阈值（%）"""
    if code.startswith(("688", "300", "301")):
        return _LIMIT_GROWTH
    if code.startswith(("8", "4", "920")):
        return _LIMIT_BJ
    return _LIMIT_MAIN


def is_limit_up(bar: StockDailyBar, code: str) -> bool:
    """单日是否涨停"""
    return bar.change_pct >= limit_up_threshold(code)


def count_consecutive_boards(bars: list[StockDailyBar], code: str) -> int:
    """从最新一根往前数，连续涨停的天数（连板数）"""
    count = 0
    for bar in reversed(bars):
        if is_limit_up(bar, code):
            count += 1
        else:
            break
    return count


def had_recent_limit_up(bars: list[StockDailyBar], code: str, lookback: int) -> bool:
    """回溯窗口内是否出现过涨停"""
    window = bars[-lookback:] if len(bars) >= lookback else bars
    return any(is_limit_up(b, code) for b in window)


def is_active(volume_ratio: float, threshold: float) -> bool:
    """规则2：量比是否达到活跃阈值"""
    return volume_ratio >= threshold


def volume_ratio_from_bars(bars: list[StockDailyBar], period: int = 5) -> float | None:
    """用日K估算量比代理：最新成交量 / 前 N 日均量

    实时量比需分时数据，这里用日级近似（源无关，腾讯/东财K线均可），
    数据不足返回 None（由调用方决定是否放行）。
    """
    if len(bars) < period + 1:
        return None
    today_vol = bars[-1].volume
    prev = [b.volume for b in bars[-period - 1:-1]]
    avg = sum(prev) / len(prev) if prev else 0
    if avg <= 0:
        return None
    return round(today_vol / avg, 2)


def below_ma5(price: float, ma5: float | None) -> bool:
    """规则4：是否跌破 5 日线"""
    if not ma5 or ma5 <= 0:
        return False
    return price < ma5


@dataclass
class ScreenResult:
    """筛选结果 + 命中明细"""
    passed: bool
    recent_limit_up: bool
    consecutive_boards: int
    broke_ma5: bool
    volume_ratio: float | None = None
    reasons: list[str] = field(default_factory=list)


def screen(
    bars: list[StockDailyBar],
    code: str,
    price: float,
    ma5: float | None,
    cfg: YangjiaConfig,
) -> ScreenResult:
    """对单只候选股做养家规则判定（规则1 涨停回溯 + 规则2 量比 + 规则4 5日线）"""
    recent = had_recent_limit_up(bars, code, cfg.lookback_days)
    boards = count_consecutive_boards(bars, code)
    broke = below_ma5(price, ma5)
    vr = volume_ratio_from_bars(bars)

    reasons: list[str] = []
    # 规则1：近期有涨停 且 连板数未达剔除线
    rule1_ok = recent and boards < cfg.max_boards
    if recent and boards >= cfg.max_boards:
        reasons.append(f"已 {boards} 连板，防资金获利跑掉，剔除")
    elif not recent:
        reasons.append(f"近 {cfg.lookback_days} 天无涨停")
    else:
        reasons.append(f"近 {cfg.lookback_days} 天内曾涨停（{boards} 连板）")

    # 规则2：量比达标（数据不足则放行，不误杀）
    rule2_ok = vr is None or vr >= cfg.volume_ratio_min
    if vr is None:
        reasons.append("量比数据不足，跳过该项")
    elif vr >= cfg.volume_ratio_min:
        reasons.append(f"量比 {vr}（活跃）")
    else:
        reasons.append(f"量比 {vr} 低于 {cfg.volume_ratio_min}，资金未动，剔除")

    # 规则4：未跌破 5 日线
    rule4_ok = not broke
    if broke:
        reasons.append("已跌破 5 日线，生命线告破，剔除")
    else:
        reasons.append("站稳 5 日线之上")

    return ScreenResult(
        passed=rule1_ok and rule2_ok and rule4_ok,
        recent_limit_up=recent,
        consecutive_boards=boards,
        broke_ma5=broke,
        volume_ratio=vr,
        reasons=reasons,
    )
