import importlib.util
import unittest
from tests.fixtures.research import snapshot


class SwingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('domain.strategy.swing_rules'))
        from domain.strategy.swing_rules import evaluate_strategy, load_config
        self.evaluate = evaluate_strategy
        self.config = load_config()

    def test_shrink_pullback_and_determinism(self):
        s = snapshot()
        for b in s.bars[-5:]: b['volume'] = 1e7
        d = self.evaluate(s,'pullback',self.config)
        self.assertEqual(d.status,'eligible')
        self.assertEqual(d.rule_score,100)
        self.assertLess(d.metadata['stop_loss'],s.bars[-1]['close'])
        self.assertEqual(self.evaluate(s,'pullback',self.config).metadata,d.metadata)

    def test_breakout(self):
        s = snapshot()
        s.bars[-1].update(open=11.8,close=12,high=12.2,low=11.7,volume=4e7)
        s.quote['price']=12
        self.assertEqual(self.evaluate(s,'breakout',self.config).status,'eligible')
        s.bars[-1]['volume']=1
        self.assertEqual(self.evaluate(s,'breakout',self.config).status,'observe')

    def test_missing_status_short_history_and_nan_never_eligible(self):
        for change in ('status','short','nan','st','zero'):
            s=snapshot()
            for b in s.bars[-5:]: b['volume']=1e7
            if change=='status': s.security_status={}
            if change=='short': s.bars=s.bars[-60:]
            if change=='nan': s.bars[-1]['close']=float('nan')
            if change=='st': s.security_status['is_st']=True
            if change=='zero': s.bars[-1]['volume']=0
            self.assertNotEqual(self.evaluate(s,'pullback',self.config).status,'eligible',change)

    def test_liquidity_boundary(self):
        s=snapshot()
        for b in s.bars[-5:]: b['volume']=1e7
        for b in s.bars[-20:]: b['amount']=1e8
        self.assertEqual(self.evaluate(s,'pullback',self.config).status,'eligible')
        s.bars[-1]['amount']=0
        self.assertNotEqual(self.evaluate(s,'pullback',self.config).status,'eligible')
