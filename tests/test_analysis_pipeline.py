import importlib.util
import unittest
from unittest.mock import AsyncMock
from copy import deepcopy
from tests.fixtures.research import snapshot


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('application.research_service'))
        from application.research_service import ResearchService
        self.s=snapshot()
        self.source=AsyncMock()
        self.source.collect.side_effect=lambda *args: deepcopy(self.s)
        self.repo=AsyncMock()
        self.model=AsyncMock()
        self.model.identity='fixture'
        self.model.explain.return_value={'claims':[{'evidence_ids':['bars'],'stance':'support', 'text':'趋势待观察'}],
                                         'risk_passed':True,'model_id':'fixture'}
        self.svc=ResearchService(self.source,self.repo,self.model)

    async def test_missing_data_never_calls_model(self):
        self.s.quote={}
        r=await self.svc.analyze_run('600000','deep',as_of=self.s.as_of)
        self.assertEqual(r.status,'blocked')
        self.model.explain.assert_not_awaited()
        self.repo.save_run.assert_awaited_once()

    async def test_distinct_stages(self):
        stages={}
        for depth in ('quick','standard','deep'):
            r=await self.svc.analyze_run('600000',depth,force=True,as_of=self.s.as_of)
            stages[depth]=r.stages
        self.assertLess(len(stages['quick']),len(stages['standard']))
        self.assertLess(len(stages['standard']),len(stages['deep']))
        self.assertIn('risk',stages['deep'])

    async def test_cache_revalidates_and_force_refreshes(self):
        r=await self.svc.analyze_run('600000','quick',as_of=self.s.as_of)
        r2=await self.svc.analyze_run('600000','quick',as_of=self.s.as_of)
        self.assertEqual(r.run_id,r2.run_id)
        self.assertEqual(self.source.collect.await_count,2)
        r3=await self.svc.analyze_run('600000','quick',force=True,as_of=self.s.as_of)
        self.assertNotEqual(r3.run_id,r.run_id)
        self.s.quote['price']=12
        r4=await self.svc.analyze_run('600000','quick',as_of=self.s.as_of)
        self.assertNotEqual(r4.run_id,r.run_id)

    async def test_unknown_evidence_and_risk_failure_not_success(self):
        self.model.explain.return_value={'claims':[{'evidence_ids':['invented'],'text':'x','stance':'support'}]}
        r=await self.svc.analyze_run('600000','deep',as_of=self.s.as_of)
        self.assertEqual(r.status,'partial')
        self.assertNotEqual(r.decision.status,'eligible')
        self.assertEqual(r.annotations,[])

    async def test_rule_gate_not_overridden(self):
        self.model.explain.return_value={'claims':[], 'status':'eligible','risk_passed':True}
        r=await self.svc.analyze_run('600000','quick',as_of=self.s.as_of)
        self.assertEqual(r.decision.status,'observe')

    async def test_timeouts_are_not_success(self):
        self.model.explain.side_effect=TimeoutError('test')
        r=await self.svc.analyze_run('600000','deep',as_of=self.s.as_of)
        self.assertEqual(r.status,'partial')
        self.assertIn('risk', ' '.join(r.errors))

    async def test_data_collection_failure_is_saved(self):
        self.source.collect.side_effect=TimeoutError('offline')
        r=await self.svc.analyze_run('600000','quick',as_of=self.s.as_of)
        self.assertEqual(r.status,'blocked')
        self.repo.save_run.assert_awaited_once()
        self.model.explain.assert_not_awaited()

    async def test_non_dict_model_output_is_partial(self):
        self.model.explain.return_value=['not-an-object']
        r=await self.svc.analyze_run('600000','quick',as_of=self.s.as_of)
        self.assertEqual(r.status,'partial')
