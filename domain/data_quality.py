"""Pure point-in-time checks. Never infer freshness from download time."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import math

SH = ZoneInfo('Asia/Shanghai')


def parse_time(value):
    try:
        result = datetime.fromisoformat(str(value))
        return result if result.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def completed_session(as_of, calendar):
    if as_of.tzinfo is None or not calendar:
        return None
    local = as_of.astimezone(SH)
    days = [d for d in calendar if d < local.date().isoformat() or
            (d == local.date().isoformat() and local.time() >= time(15, 0))]
    return max(days) if days else None


def finite(value, positive=False):
    return isinstance(value, (int, float)) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def validate_snapshot(snapshot):
    errors = []
    now = snapshot.as_of
    if now.tzinfo is None:
        return ['as_of_timezone_unknown']
    last = completed_session(now, snapshot.calendar_dates)
    if not last:
        errors.append('calendar_unknown')
    if not snapshot.quote or not finite(snapshot.quote.get('price'), True):
        errors.append('quote_invalid')
    observed = parse_time(snapshot.quote.get('observed_at'))
    if observed is None:
        errors.append('quote_time_unknown')
    elif observed > now:
        errors.append('quote_future')
    elif snapshot.market_session in ('intraday', 'lunch'):
        reference = now
        if snapshot.market_session == 'lunch':
            reference = now.astimezone(SH).replace(hour=11, minute=30, second=0, microsecond=0)
        if snapshot.quote.get('kind') != 'realtime' or reference - observed > timedelta(minutes=5):
            errors.append('quote_stale')
    elif last and observed.astimezone(SH).date().isoformat() != last:
        errors.append('quote_stale')
    bars = snapshot.bars
    if len(bars) < 120:
        errors.append('insufficient_history')
    dates = [b.get('trade_date', '') for b in bars]
    if dates != sorted(set(dates)):
        errors.append('daily_order_or_duplicate')
    if last and any(d > last for d in dates):
        errors.append('future_or_unfinished_bar')
    if last and (not dates or dates[-1] != last):
        errors.append('daily_stale')
    if any(d not in snapshot.calendar_dates for d in dates):
        errors.append('nontrading_bar')
    for field, allowed, flag in [('adjustment', {'raw', 'qfq', 'hfq'}, 'mixed_adjustment'),
                                  ('volume_unit', {'share'}, 'mixed_volume_unit'),
                                  ('amount_unit', {'CNY'}, 'mixed_amount_unit')]:
        values = {b.get(field) for b in bars}
        if len(values) != 1 or not values.issubset(allowed):
            errors.append(flag)
    for b in bars:
        if (not all(finite(b.get(k), True) for k in ('open', 'high', 'low', 'close')) or
                not all(finite(b.get(k)) for k in ('volume', 'amount'))):
            errors.append('invalid_bar')
            break
        if not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']:
            errors.append('invalid_bar')
            break
    return list(dict.fromkeys(errors))
