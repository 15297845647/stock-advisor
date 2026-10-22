import unittest
from infrastructure.exchange_calendar import trading_dates


class CalendarTests(unittest.TestCase):
    def test_exchange_holidays_and_compensating_weekends(self):
        dates=trading_dates('2026-10-22')
        for day in ('2026-02-23','2026-02-28','2026-09-25','2026-10-07','2026-10-10'):
            self.assertNotIn(day,dates)
        self.assertIn('2026-10-08',dates)
        self.assertIn('2025-10-09',dates)

    def test_unknown_year_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'coverage'):
            trading_dates('2027-01-04')
