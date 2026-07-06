"""中长线选股 — 纯领域筛选逻辑，无 IO

规则（趋势 + 基本面）：
- 均线多头排列：MA5 > MA10 > MA20，且 MA20 > MA60（有 MA60 时）
- 趋势向上：技术面趋势判定为 up
- 基本面：ROE 达标、PE 合理、营收同比增长达标（数据缺失则跳过该子项）
持股周期以周/月计，不看量比打板。
"""

from dataclasses import dataclass, field

from domain.models.stock import StockFundamental
from domain.stock_analyzer import TechnicalSnapshot
from domain.strategy.strategy_config import MidLongConfig


def is_ma_bullish(tech: TechnicalSnapshot) -> bool:
    """均线多头排列：MA5>MA10>MA20，且 MA20>MA60（若有 MA60）"""
    if not (tech.ma5 > tech.ma10 > tech.ma20):
        return False
    if tech.ma60 and tech.ma20 <= tech.ma60:
        return False
    return True


def passes_fundamentals(f: StockFundamental | None, cfg: MidLongConfig) -> tuple[bool, list[str]]:
    """基本面校验，返回 (是否通过, 命中原因)。数据缺失的子项跳过不拦。"""
    reasons: list[str] = []
    if f is None:
        reasons.append("基本面数据缺失，跳过基本面校验")
        return True, reasons

    # 注：StockFundamental 数值字段默认 0 表示缺失，仅在有真实值(非0)时校验，避免误杀

    # ROE（负值=亏损也应剔除）
    if f.roe:
        if f.roe < cfg.min_roe:
            return False, [f"ROE {f.roe:.1f}% 低于 {cfg.min_roe}%"]
        reasons.append(f"ROE {f.roe:.1f}%")

    # PE：有值时要求落在 (0, 上限]，剔除亏损(负PE)与高估
    if f.pe_ratio:
        if f.pe_ratio <= 0 or f.pe_ratio > cfg.max_pe:
            return False, [f"PE {f.pe_ratio:.1f} 不在 (0, {cfg.max_pe}] 区间"]
        reasons.append(f"PE {f.pe_ratio:.1f}")

    # 营收增长（有值时校验；缺失=0 跳过）
    if f.revenue_growth:
        if f.revenue_growth < cfg.min_revenue_growth:
            return False, [f"营收增长 {f.revenue_growth:.1f}% 低于 {cfg.min_revenue_growth}%"]
        reasons.append(f"营收增长 {f.revenue_growth:.1f}%")

    return True, reasons


@dataclass
class MidLongResult:
    """中长线筛选结果 + 命中明细"""
    passed: bool
    ma_bull: bool
    uptrend: bool
    fundamental_ok: bool
    reasons: list[str] = field(default_factory=list)


def screen_technical(tech: TechnicalSnapshot | None, cfg: MidLongConfig) -> tuple[bool, list[str]]:
    """先做技术面判定（便宜），不通过则无需再拉基本面"""
    if tech is None:
        return False, ["技术数据不足"]

    reasons: list[str] = []
    ma_bull = is_ma_bullish(tech)
    uptrend = tech.trend == "up"

    ma_ok = ma_bull or not cfg.require_ma_bull
    trend_ok = uptrend or not cfg.require_uptrend

    reasons.append("均线多头排列" if ma_bull else "均线未多头排列")
    reasons.append(f"趋势{tech.trend}")
    return (ma_ok and trend_ok), reasons


def screen_midlong(
    tech: TechnicalSnapshot | None,
    fundamentals: StockFundamental | None,
    cfg: MidLongConfig,
) -> MidLongResult:
    """完整中长线判定（技术面 + 基本面）"""
    tech_ok, tech_reasons = screen_technical(tech, cfg)
    if not tech_ok:
        return MidLongResult(
            passed=False, ma_bull=False, uptrend=False,
            fundamental_ok=False, reasons=tech_reasons,
        )

    fund_ok, fund_reasons = passes_fundamentals(fundamentals, cfg)
    ma_bull = is_ma_bullish(tech) if tech else False
    uptrend = tech.trend == "up" if tech else False

    return MidLongResult(
        passed=fund_ok,
        ma_bull=ma_bull,
        uptrend=uptrend,
        fundamental_ok=fund_ok,
        reasons=tech_reasons + fund_reasons,
    )
