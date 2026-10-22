import importlib.util
import unittest
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo


class DataQualityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('domain.data_quality'), 'quality gate is not implemented')
        from tests.fixtures.research import snapshot
        from domain.data_quality import validate_snapshot
        self.make = snapshot
        self.validate = validate_snapshot

    def test_complete_close(self):
        self.assertEqual(self.validate(self.make()), [])

    def test_stale_and_unknown_quote(self):
        s = self.make()
        s.market_session = 'intraday'
        s.quote['observed_at'] = (s.as_of - timedelta(minutes=6)).isoformat()
        self.assertIn('quote_stale', self.validate(s))
        s.quote['observed_at'] = None
        self.assertIn('quote_time_unknown', self.validate(s))

    def test_mixed_adjustment_units_and_nan(self):
        for key, value, expected in [('adjustment', 'qfq', 'mixed_adjustment'),
                                      ('volume_unit', 'lot', 'mixed_volume_unit'),
                                      ('close', float('nan'), 'invalid_bar')]:
            s = self.make()
            s.bars[0][key] = value
            self.assertIn(expected, self.validate(s))

    def test_future_short_history_and_unknown_calendar(self):
        s = self.make()
        s.bars[-1]['trade_date'] = '2099-01-01'
        self.assertIn('future_or_unfinished_bar', self.validate(s))
        s = self.make()
        s.bars = s.bars[:50]
        self.assertIn('insufficient_history', self.validate(s))
        s = self.make()
        s.calendar_dates = []
        self.assertIn('calendar_unknown', self.validate(s))

    def test_weekend_and_lunch(self):
        s = self.make()
        s.as_of = datetime(2026, 10, 24, 10, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertIn('daily_stale', self.validate(s))  # Friday is missing
        s = self.make()
        s.as_of = datetime(2026, 10, 23, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
        s.market_session = 'lunch'
        s.quote['observed_at'] = '2026-10-23T11:30:00+08:00'
        s.quote['kind'] = 'realtime'
        self.assertEqual(self.validate(s), [])

    def test_quote_fetched_time_is_not_observed_time(self):
        s = self.make()
        s.quote['observed_at'] = None
        s.quote['fetched_at'] = s.as_of.isoformat()
        self.assertIn('quote_time_unknown', self.validate(s))

    def test_missing_financials_remain_unknown(self):
        from infrastructure.financial_parser import parse_financial_abstract
        rows = [{'指标': '营业收入', '20260630': 100, '20251231': 80},
                {'指标': '净利润', '20260630': 10, '20251231': 8}]
        data = parse_financial_abstract(rows)
        self.assertEqual(data['report_period'], '20260630')
        self.assertEqual(data['revenue'], 100)
        self.assertIsNone(data['roe'])
        self.assertIsNone(data['available_at'])
