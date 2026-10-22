import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from domain.technical_scorer import TechnicalScorer


class RegressionTests(unittest.TestCase):
    def test_broken_support_is_not_a_pullback(self):
        self.assertEqual(TechnicalScorer._support_score(90, SimpleNamespace(support=100)), 0)


class PipelineBaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_passes_explicit_depth(self):
        from application.analysis_service import AnalysisService
        svc = AnalysisService.__new__(AnalysisService)
        pipeline = SimpleNamespace(run=AsyncMock(return_value='report'))
        data = dict(quote=SimpleNamespace(name='test', code='600000', price=10),
                    tech=None, fundamentals=None, news=[], fund_flows=[], bars=[])
        await svc._run_pipeline(pipeline, data, SimpleNamespace(emit=AsyncMock()), deep=False)
        self.assertEqual(pipeline.run.call_args.kwargs.get('deep'), False)

    async def test_formatted_decision_is_evaluable(self):
        from domain.models.stock import StockDecision
        from domain.decision_parser import format_decision
        from domain.backtest_engine import extract_decision_from_report
        text = format_decision(StockDecision('买入', 12, 9, 60, 3, 'example'))
        # Existing text output cannot round-trip; new runs must persist the object separately.
        self.assertIsNotNone(extract_decision_from_report(text))


if __name__ == '__main__':
    unittest.main()
