"""技术指标计算 — 纯领域逻辑，不依赖外部 IO"""

from dataclasses import dataclass

from domain.models.stock import StockDailyBar


@dataclass
class TechnicalSnapshot:
    """技术面快照"""
    ma5: float
    ma10: float
    ma20: float
    ma60: float | None
    macd: float
    macd_signal: float
    macd_hist: float
    rsi_14: float
    kdj_k: float
    kdj_d: float
    kdj_j: float
    boll_upper: float
    boll_mid: float
    boll_lower: float
    trend: str           # up / down / sideways
    support: float
    resistance: float


def _ema(values: list[float], period: int) -> list[float]:
    """指数移动平均"""
    result = [values[0]]
    multiplier = 2.0 / (period + 1)
    for v in values[1:]:
        result.append(v * multiplier + result[-1] * (1 - multiplier))
    return result


def _sma(values: list[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values)
    return sum(values[-period:]) / period


def compute_macd(closes: list[float]) -> tuple[float, float, float]:
    """MACD(12,26,9)"""
    if len(closes) < 26:
        return 0.0, 0.0, 0.0
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    macd_hist = 2 * (dif[-1] - dea[-1])
    return dif[-1], dea[-1], macd_hist


def compute_rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_kdj(
    highs: list[float], lows: list[float], closes: list[float], period: int = 9
) -> tuple[float, float, float]:
    """KDJ 指标"""
    if len(closes) < period:
        return 50.0, 50.0, 50.0

    highest = max(highs[-period:])
    lowest = min(lows[-period:])
    denom = highest - lowest
    rsv = ((closes[-1] - lowest) / denom * 100) if denom != 0 else 50.0

    k, d = 50.0, 50.0
    k = 2 / 3 * k + 1 / 3 * rsv
    d = 2 / 3 * d + 1 / 3 * k
    j = 3 * k - 2 * d
    return k, d, j


def compute_bollinger(closes: list[float], period: int = 20) -> tuple[float, float, float]:
    """布林带"""
    if len(closes) < period:
        mid = sum(closes) / len(closes)
        return mid + mid * 0.02, mid, mid - mid * 0.02

    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance ** 0.5
    return mid + 2 * std, mid, mid - 2 * std


def _judge_trend(ma5: float, ma10: float, ma20: float) -> str:
    if ma5 > ma10 > ma20:
        return "up"
    if ma5 < ma10 < ma20:
        return "down"
    return "sideways"


def _find_support_resistance(bars: list[StockDailyBar]) -> tuple[float, float]:
    """近期低点作为支撑，高点作为压力"""
    recent = bars[-20:] if len(bars) >= 20 else bars
    support = min(b.low for b in recent)
    resistance = max(b.high for b in recent)
    return support, resistance


def analyze_technical(bars: list[StockDailyBar]) -> TechnicalSnapshot | None:
    """给定日K数据，计算完整技术面快照"""
    if len(bars) < 10:
        return None

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]

    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60) if len(closes) >= 60 else None

    dif, dea, hist = compute_macd(closes)
    rsi = compute_rsi(closes)
    k, d, j = compute_kdj(highs, lows, closes)
    upper, mid, lower = compute_bollinger(closes)

    trend = _judge_trend(ma5, ma10, ma20)
    support, resistance = _find_support_resistance(bars)

    return TechnicalSnapshot(
        ma5=round(ma5, 2),
        ma10=round(ma10, 2),
        ma20=round(ma20, 2),
        ma60=round(ma60, 2) if ma60 else None,
        macd=round(dif, 4),
        macd_signal=round(dea, 4),
        macd_hist=round(hist, 4),
        rsi_14=round(rsi, 2),
        kdj_k=round(k, 2),
        kdj_d=round(d, 2),
        kdj_j=round(j, 2),
        boll_upper=round(upper, 2),
        boll_mid=round(mid, 2),
        boll_lower=round(lower, 2),
        trend=trend,
        support=round(support, 2),
        resistance=round(resistance, 2),
    )
