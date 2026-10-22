import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from domain.technical_scorer import TechnicalScorer


class RegressionTests(unittest.TestCase):
    def test_broken_support_is_not_a_pullback(self):
        self.assertEqual(TechnicalScorer._support_score(90, SimpleNamespace(support=100)), 0)


class PipelineBaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_passes_explicit_depth(self):
        from application.analysis_service import AnalysisService
        from domain.models.research_depth import ResearchDepth
        svc = AnalysisService.__new__(AnalysisService)
        svc._research = SimpleNamespace(analyze_run=AsyncMock(return_value=SimpleNamespace(report='report')))
        with patch.dict('os.environ',{'STOCK_ANALYSIS_ENGINE':'v2'}):
            await svc.analyze_stock_deep('600000',depth=ResearchDepth.STANDARD)
        self.assertEqual(svc._research.analyze_run.call_args.args[1],ResearchDepth.STANDARD)

    async def test_formatted_decision_is_evaluable(self):
        from application.research_service import ResearchService
        from tests.fixtures.research import snapshot
        repo=AsyncMock()
        source=SimpleNamespace(collect=AsyncMock(return_value=snapshot()))
        run=await ResearchService(source,repo).analyze_run('600000','quick',use_model=False)
        saved=repo.save_run.call_args.args[0]
        self.assertEqual(saved.decision.stock_code,'600000')
        self.assertEqual(saved.run_id,run.run_id)
        self.assertNotIn('[DECISION]',saved.report)


if __name__ == '__main__':
    unittest.main()
