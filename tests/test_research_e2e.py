import importlib.util
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock
from tests.fixtures.research import snapshot
from repository.research_repository import ResearchRepository
from application.research_service import ResearchService


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(importlib.util.find_spec('application.evaluation_service'))
        self.assertIsNotNone(importlib.util.find_spec('application.research_selection_service'))
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=ResearchRepository(str(Path(self.temp.name)/'research.sqlite'))

    async def test_snapshot_to_report_to_mature_evaluation_is_idempotent(self):
        from application.evaluation_service import EvaluationService
        s=snapshot()
        for b in s.bars[-5:]: b['volume']=1e7
        src=AsyncMock();src.collect.return_value=s
        run=await ResearchService(src,self.repo).analyze_run('600000','quick',as_of=s.as_of,use_model=False)
        self.assertEqual(run.decision.status,'eligible')
        stored=await self.repo.get_run(run.run_id)
        self.assertIn('运行ID',stored.report)
        dates=['2026-10-23','2026-10-26','2026-10-27','2026-10-28','2026-10-29']
        loader=AsyncMock(return_value={'bars':[dict(trade_date=d,open=12,close=12.5,high=13,low=11.5,
            volume=1e7,adjustment='raw',limit_up=14,limit_down=10,corporate_action=False) for d in dates],
            'calendar_dates':dates, 'benchmark_bars':[]})
        service=EvaluationService(self.repo,loader=loader)
        now=datetime.fromisoformat('2026-10-29T16:00:00+08:00')
        result=await service.evaluate_mature_runs(now,(5,))
        self.assertEqual(result[0].status,'evaluated')
        await service.evaluate_mature_runs(now,(5,))
        self.assertEqual(len(await self.repo.list_evaluations()),1)

    async def test_selection_same_snapshots_empty_and_outside_rank_rejected(self):
        from application.research_selection_service import ResearchSelectionService
        a=snapshot();b=snapshot();b.stock_code='600001'
        for s in (a,b):
            for bar in s.bars[-5:]: bar['volume']=1e7
            s.seal()
        source=AsyncMock()
        source.collect.side_effect=lambda code,now: deepcopy(a if code=='600000' else b)
        ranker=AsyncMock(return_value=['999999'])
        service=ResearchSelectionService(source,self.repo,ranker=ranker)
        result=await service.screen(['600001','600000'],as_of=a.as_of)
        self.assertEqual(result['rule_ranking'],['600000','600001'])
        self.assertEqual(result['ai_ranking'],[])
        self.assertIn('ranking_invalid',result['errors'])
        self.assertEqual(source.collect.await_count,2)
        self.assertEqual(result['legacy']['status'],'unavailable')
        source.collect.side_effect=lambda *args: snapshot()
        empty=await ResearchSelectionService(source,self.repo).screen(['600000'],as_of=a.as_of,use_model=False)
        self.assertEqual(empty['rule_ranking'],[])

    async def test_no_candidate_does_not_fall_back_to_unrestricted_llm(self):
        from application.chat_service import ChatService
        from unittest.mock import patch
        svc=ChatService.__new__(ChatService)
        svc.recommend_svc=AsyncMock()
        svc.recommend_svc.recommend.return_value=([],'零候选')
        svc._unified_chat_with_snapshot=AsyncMock()
        with patch.dict('os.environ',{'STOCK_ANALYSIS_ENGINE':'v2'}):
            self.assertEqual(await svc._two_stage_recommend('选股',None),'零候选')
        svc._unified_chat_with_snapshot.assert_not_awaited()

    async def test_unmatured_forward_windows_do_not_fetch_execution_data(self):
        from application.evaluation_service import EvaluationService
        s=snapshot()
        for bar in s.bars[-5:]: bar['volume']=1e7
        source=AsyncMock();source.collect.return_value=s
        await ResearchService(source,self.repo).analyze_run('600000','quick',as_of=s.as_of,use_model=False)
        loader=AsyncMock()
        results=await EvaluationService(self.repo,loader=loader).evaluate_mature_runs(s.as_of)
        self.assertEqual([r.status for r in results],['pending']*3)
        loader.assert_not_awaited()
