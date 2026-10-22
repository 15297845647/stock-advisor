import unittest
from unittest.mock import AsyncMock, patch
from copy import deepcopy
from tests.fixtures.research import snapshot
from application.snapshot_service import SnapshotService


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_raw_close_and_news_age(self):
        s=snapshot()
        source=AsyncMock()
        source.collect.return_value={'bars':s.bars,'calendar_dates':s.calendar_dates,
            'sources':{'bars':'fixture'},'security_status':s.security_status,
            'news':[{'title':'new','published_at':'2026-10-22T10:00:00+08:00'},
                    {'title':'old','published_at':'2026-10-01T10:00:00+08:00'},
                    {'title':'unknown','published_at':None}]}
        result=await SnapshotService(source).collect('600000',s.as_of)
        self.assertEqual(result.quote['observed_at'],'2026-10-22T15:00:00+08:00')
        self.assertEqual([n['is_catalyst'] for n in result.news],[True,False])
        self.assertNotEqual(result.quality_status,'blocked')

    async def test_adjusted_bars_need_raw_quote(self):
        s=snapshot()
        for b in s.bars: b['adjustment']='qfq'
        source=AsyncMock()
        source.collect.return_value={'bars':s.bars,'raw_close':{'price':11.39,'trade_date':'2026-10-22'},
                                    'calendar_dates':s.calendar_dates}
        result=await SnapshotService(source).collect('600000',s.as_of)
        self.assertEqual(result.quote.get('price'),11.39)

    async def test_invalid_numeric_snapshot_is_blocked_and_serializable(self):
        s=snapshot(); s.bars[-1]['close']=float('nan')
        source=AsyncMock();source.collect.return_value={'bars':s.bars,'calendar_dates':s.calendar_dates}
        result=await SnapshotService(source).collect('600000',s.as_of)
        self.assertEqual(result.quality_status,'blocked')
        self.assertTrue(result.content_hash)

    async def test_backend_fetch_time_does_not_claim_historical_knowledge(self):
        from infrastructure.research_source import ResearchSource
        from datetime import datetime
        source=ResearchSource()
        source._call=AsyncMock(return_value=None)
        with self.assertRaisesRegex(ValueError,'historical'):
            await source.collect('600000',datetime.fromisoformat('2000-01-01T16:00:00+08:00'))
