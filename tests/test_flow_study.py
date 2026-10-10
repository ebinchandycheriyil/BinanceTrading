from dataclasses import replace
import unittest

from scalp.flow_study import parse_row, signals
from scalp.settings import Settings
from scalp.signals import Candle


class FlowTests(unittest.TestCase):
    def fixture(self):
        rows = [Candle(i*60000,100,100.1,99.9,100,10) for i in range(65)]
        rows[-2] = Candle(63*60000,100.2,100.7,100.1,100.5,20)
        rows[-1] = Candle(64*60000,100.5,100.8,100.4,100.7,15)
        buys = {c.ts:c.volume*.7 for c in rows}
        return rows, buys

    def test_taker_volume_uses_base_field_and_rejects_invalid(self):
        row = [0,'100','101','99','100','10',59999,'1000',12,'7','700',0]
        self.assertEqual(parse_row(row)['buy'],7)
        for value in (-1,11,float('nan')):
            row[9] = value
            with self.assertRaises(ValueError):
                parse_row(row)

    def test_confirmation_required_and_direction_correct(self):
        rows, buys = self.fixture()
        signal = signals(rows,buys,Settings())['flow_continuation']
        self.assertEqual(signal.side,'BUY')
        self.assertLess(signal.stop_price, rows[-1].close)
        self.assertGreater(signal.target_price, rows[-1].close)
        buys[rows[-1].ts] = rows[-1].volume*.4
        self.assertEqual(signals(rows,buys,Settings())['flow_continuation'].side,'HOLD')

    def test_future_flow_never_read(self):
        rows, buys = self.fixture()
        expected = signals(rows,buys,Settings())
        buys[rows[-1].ts+60000] = float('nan')
        self.assertEqual(signals(rows,buys,Settings()),expected)

    def test_mirror_price_and_flow_produces_sell(self):
        rows, buys = self.fixture()
        mirrored = [Candle(c.ts,200-c.open,200-c.low,200-c.high,200-c.close,c.volume) for c in rows]
        flows = {c.ts:c.volume-buys[c.ts] for c in rows}
        self.assertEqual(signals(mirrored,flows,Settings())['flow_continuation'].side,'SELL')

    def test_missing_minute_fails(self):
        rows,buys = self.fixture()
        rows[20] = replace(rows[20],ts=rows[19].ts)
        with self.assertRaises(ValueError):
            signals(rows,buys,Settings())

    def test_zero_volume_suppresses_signal(self):
        rows,buys = self.fixture()
        rows[-1] = replace(rows[-1],volume=0)
        self.assertTrue(all(s.side=='HOLD' for s in signals(rows,buys,Settings()).values()))

    def test_failed_breakout_requires_reclaim_and_opposing_flow(self):
        rows, buys = self.fixture()
        rows[-2] = Candle(63*60000,99.8,100,99.5,99.95,20)
        rows[-1] = Candle(64*60000,99.95,100,99.8,99.97,15)
        buys[rows[-2].ts] = 6
        self.assertEqual(signals(rows,buys,Settings())['absorption_reversal'].side,'BUY')
        rows[-2] = replace(rows[-2],close=99.85)
        self.assertEqual(signals(rows,buys,Settings())['absorption_reversal'].side,'HOLD')

    def test_quiet_shock_can_trigger_but_high_volume_disables_it(self):
        rows = [Candle(i*60000,100,100.1,99.9,100+.04*(i%2),10) for i in range(65)]
        for i, close in ((61,99.9),(62,99.8),(63,99.65),(64,99.7)):
            rows[i] = Candle(i*60000,close,close+.05,close-.05,close,5)
        buys = {c.ts:c.volume*.7 for c in rows}
        cfg = replace(Settings(),min_stop_fraction=.001)
        self.assertEqual(signals(rows,buys,cfg)['quiet_shock_reversal'].side,'BUY')
        rows[-2] = replace(rows[-2],volume=50)
        buys[rows[-2].ts] = 35
        self.assertEqual(signals(rows,buys,cfg)['quiet_shock_reversal'].side,'HOLD')
