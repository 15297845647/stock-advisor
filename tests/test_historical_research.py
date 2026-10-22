import unittest
from dataclasses import asdict
from unittest.mock import AsyncMock
from tests.fixtures.research import snapshot
from domain.strategy.swing_rules import load_config
from domain.models.research import digest,json_text
import json


class HistoricalTests(unittest.IsolatedAsyncioTestCase):
    def dataset(self):
        s=snapshot()
        for e in s.evidence: e.available_at=s.as_of.isoformat()
        return {'config_hash':digest(load_config()),'config_frozen_at':'2026-10-01T16:00:00+08:00',
            'test_start':'2026-10-15T00:00:00+08:00','as_of':'2026-10-22T16:00:00+08:00',
            'universe_description':'synthetic fixture','survivorship_bias':'not representative',
            'records':[{'snapshot':json.loads(json_text(asdict(s))),
                        'execution':{'calendar_dates':s.calendar_dates,'bars':[]}}]}

    async def test_frozen_oos_rules_are_separate_from_forward_ai(self):
        from application.historical_research_service import HistoricalResearchService
        repo=AsyncMock()
        result=await HistoricalResearchService(repo).replay(self.dataset())
        self.assertEqual(result['out_of_sample']['5']['total'],1)
        run=repo.save_run.await_args.args[0]
        self.assertEqual(run.group,'historical_rules')
        self.assertEqual(run.model_id,'none')

    async def test_unknown_availability_and_late_freeze_are_rejected(self):
        from application.historical_research_service import HistoricalResearchService
        svc=HistoricalResearchService(AsyncMock())
        data=self.dataset();data['records'][0]['snapshot']['evidence'][0]['available_at']=None
        with self.assertRaisesRegex(ValueError,'available_at'):
            await svc.replay(data)
        data=self.dataset();data['config_frozen_at']='2026-10-20T00:00:00+08:00'
        with self.assertRaisesRegex(ValueError,'freeze'):
            await svc.replay(data)
