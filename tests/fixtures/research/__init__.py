from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from domain.models.research import AnalysisSnapshot, EvidenceItem


def snapshot():
    now = datetime(2026, 10, 22, 16, tzinfo=ZoneInfo('Asia/Shanghai'))
    # Explicit synthetic calendar, not a production weekday approximation.
    dates = [(date(2026, 1, 1)+timedelta(days=i)).isoformat() for i in range(330)
             if (date(2026, 1, 1)+timedelta(days=i)).weekday() < 5]
    days = [d for d in dates if d <= '2026-10-22'][-140:]
    bars = [dict(trade_date=d, open=10+i*.01, high=10.2+i*.01, low=9.8+i*.01,
                 close=10+i*.01, volume=2e7, amount=2e8, adjustment='raw',
                 volume_unit='share', amount_unit='CNY') for i, d in enumerate(days)]
    return AnalysisSnapshot('fixture', '600000', now, 'closed',
          dict(price=bars[-1]['close'], name='示例', kind='close', observed_at='2026-10-22T15:00:00+08:00'),
          bars, evidence=[EvidenceItem('bars', 'daily', 'fixture', now.isoformat(), {'count':140}),
                          EvidenceItem('quote', 'quote', 'fixture', now.isoformat(), {'price':bars[-1]['close']})],
          calendar_dates=dates, security_status={'is_st':False, 'suspended':False}).seal()
