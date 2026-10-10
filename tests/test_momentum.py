from dataclasses import replace
import unittest

from scalp.engine import PaperEngine, Quote
from scalp.momentum import momentum_signal
from scalp.momentum_study import configs
from scalp.settings import Settings
from scalp.signals import Candle, Signal, analyze


class MomentumTests(unittest.TestCase):
    def test_long_and_short_momentum(self):
        rows = [Candle(i*60000,100+i*.06,100+i*.06+.04,100+i*.06-.04,
                       100+i*.06,20 if i==64 else 10) for i in range(65)]
        for cfg in configs().values():
            self.assertEqual(analyze(rows,cfg).side,'BUY')
            reverse = [Candle(c.ts,210-c.open,210-c.low,210-c.high,210-c.close,c.volume) for c in rows]
            self.assertEqual(momentum_signal(reverse,cfg).side,'SELL')
            quiet = rows[:-1]+[replace(rows[-1],volume=1)]
            self.assertEqual(momentum_signal(quiet,cfg).side,'HOLD')

    def test_gap_and_warmup_reject(self):
        cfg = configs()['momentum5']
        self.assertEqual(momentum_signal([],cfg).side,'HOLD')
        rows = [Candle(i*60000,100,101,99,100,10) for i in range(65)]
        rows[30] = replace(rows[30],ts=rows[29].ts)
        self.assertEqual(momentum_signal(rows,cfg).reason,'missing momentum candles')

    def test_equal_distances_from_slipped_entry_and_costs_remain(self):
        for side in ('BUY','SELL'):
            cfg = configs()['momentum5']
            engine = PaperEngine(cfg,persist=False,emit=lambda e:None)
            self.addCleanup(engine.journal.close)
            now=1800000000
            quotes={s:Quote(99.98,100.02,now) for s in cfg.symbols}
            engine.monitor(quotes,now)
            engine.enter(cfg.symbols[0],Signal(side,'test',.004),int((now-60)*1000),quotes,now)
            p=engine.s['positions'][cfg.symbols[0]]
            self.assertAlmostEqual(abs(p['target']-p['entry']),abs(p['entry']-p['stop']))
            self.assertLess(p['planned_net_reward_risk'],1)
            self.assertGreater(p['entry_fee'],0)
            fresh={s:Quote(q.bid,q.ask,now+60) for s,q in quotes.items()}
            engine.monitor(fresh,now+60,force_reason='TEST')
            self.assertLess(engine.s['trades'][0]['net_pnl'],0)

    def test_invalid_ratio_basis(self):
        with self.assertRaises(ValueError):
            Settings(reward_risk_basis='unknown')

    def test_default_preserves_cost_adjusted_behavior(self):
        self.assertEqual(Settings().reward_risk_basis,'net')
