import unittest
import importlib.util
from datetime import datetime
from zoneinfo import ZoneInfo
from tests.test_research_repository import run_fixture


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('domain.execution_rules'))
        from domain.execution_rules import evaluate_run
        self.evaluate=evaluate_run
        self.run=run_fixture()
        self.run.decision.status='eligible'
        self.run.decision.metadata={'signal_date':'2026-10-22','stop_loss':9,'signal_price':10}
        self.dates=['2026-10-23','2026-10-26','2026-10-27','2026-10-28','2026-10-29']
        self.bars=[dict(trade_date=d,open=10,high=11,low=9.5,close=10.5,volume=1e7,
            adjustment='raw',limit_up=11,limit_down=9,corporate_action=False) for d in self.dates]
        self.cfg={'calendar_dates':self.dates,'as_of':'2026-10-29T16:00:00+08:00',
            'capital':100000,'lot_size':100,'slippage_bps':0,
            'fee_schedule':[{'effective_from':'2023-08-28','commission':0,'min_commission':0,
                             'transfer_fee':0,'stamp_tax_sell':0}],
            'benchmark_bars':[{'trade_date':d,'open':100,'close':102} for d in self.dates]}

    def test_next_open_and_costs(self):
        e=self.evaluate(self.run,self.bars,5,self.cfg)
        self.assertEqual(e.status,'evaluated')
        self.assertAlmostEqual(e.metrics['net_return_pct'],5)
        self.assertAlmostEqual(e.metrics['excess_return_pct'],3)
        self.cfg['fee_schedule'][0]['commission']=.001
        self.assertLess(self.evaluate(self.run,self.bars,5,self.cfg).metrics['net_return_pct'],5)

    def test_pending_and_missing_are_distinct(self):
        self.cfg['as_of']='2026-10-27T16:00:00+08:00'
        self.assertEqual(self.evaluate(self.run,self.bars,5,self.cfg).status,'pending')
        self.cfg['as_of']='2026-10-29T16:00:00+08:00'
        self.assertEqual(self.evaluate(self.run,self.bars[1:],5,self.cfg).exclusion_reason,'missing_price')

    def test_tplusone_and_gap(self):
        self.bars[0]['low']=8
        self.bars[1].update(open=8,low=7.5,high=8.5,close=8,limit_down=7)
        e=self.evaluate(self.run,self.bars,5,self.cfg)
        self.assertEqual(e.metrics['exit_date'],'2026-10-26')
        self.assertEqual(e.metrics['exit_price'],8)

    def test_both_levels_stop_first(self):
        self.run.decision.metadata['target_price']=11
        self.bars[1].update(high=12,low=8,limit_up=13,limit_down=7)
        e=self.evaluate(self.run,self.bars,5,self.cfg)
        self.assertEqual(e.metrics['exit_price'],9)
        self.assertEqual(e.metrics['exit_reason'],'stop')

    def test_unfilled_and_locked_exit(self):
        self.bars[0]['open']=11
        self.assertEqual(self.evaluate(self.run,self.bars,5,self.cfg).exclusion_reason,'entry_unfilled')
        self.bars[0]['open']=10
        self.bars[-1].update(open=9,close=9,high=9,low=9)
        self.assertEqual(self.evaluate(self.run,self.bars,5,self.cfg).exclusion_reason,'exit_unfilled')

    def test_no_assumed_rules_corporate_actions_or_short_sales(self):
        del self.bars[2]['limit_up']
        self.assertEqual(self.evaluate(self.run,self.bars,5,self.cfg).status,'unavailable')
        self.bars[2]['limit_up']=11
        self.bars[2]['corporate_action']=True
        self.assertEqual(self.evaluate(self.run,self.bars,5,self.cfg).exclusion_reason,'corporate_action_unsupported')
        self.run.decision.status='observe'
        self.assertEqual(self.evaluate(self.run,self.bars,5,self.cfg).exclusion_reason,'no_long_entry')

    def test_zero_nan_duplicate_and_unknown_fees_rejected(self):
        self.cfg['fee_schedule']=[]
        self.assertEqual(self.evaluate(self.run,self.bars,5,self.cfg).exclusion_reason,'fees_unknown')
        self.setUp()
        self.bars[0]['open']=float('nan')
        self.assertEqual(self.evaluate(self.run,self.bars,5,self.cfg).exclusion_reason,'invalid_price')

    def test_drawdown_does_not_use_prices_after_intraday_exit(self):
        self.bars[0]['close']=10
        self.bars[1].update(high=20,low=8,close=20,limit_up=22,limit_down=7)
        result=self.evaluate(self.run,self.bars,5,self.cfg)
        self.assertEqual(result.metrics['exit_price'],9)
        self.assertAlmostEqual(result.metrics['max_drawdown_pct'],10)

    def test_board_lot_assumptions_are_not_silently_generalized(self):
        self.run.decision.stock_code='688001'
        result=self.evaluate(self.run,self.bars,5,self.cfg)
        self.assertEqual(result.exclusion_reason,'board_execution_unsupported')
