"""Bounded exchange calendar, derived from annual SSE closure notices.

No make-up weekend is a stock trading day. Fail outside reviewed coverage.
"""
from datetime import date, timedelta

SOURCES = [
    'https://www.sse.com.cn/disclosure/announcement/general/c/c_20241223_10767108.shtml',
    'https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml',
]
VERSION = 'sse-2025-2026-v1'
CLOSURES = [
    ('2025-01-01','2025-01-01'),('2025-01-28','2025-02-04'),
    ('2025-04-04','2025-04-06'),('2025-05-01','2025-05-05'),
    ('2025-05-31','2025-06-02'),('2025-10-01','2025-10-08'),
    ('2026-01-01','2026-01-03'),('2026-02-15','2026-02-23'),
    ('2026-04-04','2026-04-06'),('2026-05-01','2026-05-05'),
    ('2026-06-19','2026-06-21'),('2026-09-25','2026-09-27'),
    ('2026-10-01','2026-10-07'),
]


def trading_dates(as_of):
    if not '2025-01-01' <= as_of <= '2026-12-31':
        raise ValueError('exchange calendar coverage is 2025-01-01 through 2026-12-31')
    day=date(2025,1,1); result=[]
    while day<=date(2026,12,31):
        value=day.isoformat()
        if day.weekday()<5 and not any(start<=value<=end for start,end in CLOSURES):
            result.append(value)
        day+=timedelta(days=1)
    return result
