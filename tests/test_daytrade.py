import math
import unittest

import numpy as np
import pandas as pd

from daytrade import indicators as ind
from daytrade.backtest import Costs, simulate
from daytrade.strategies import _orders

NAN = float('nan')
ZERO = Costs(taker=0.0, maker=0.0, slip=0.0, funding=True)


def bars(rows, start='2024-01-01'):
    idx = pd.date_range(start, periods=len(rows), freq='5min', tz='UTC').as_unit('ms')
    df = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close'], index=idx, dtype=float)
    df['volume'] = 1.0; df['quote_volume'] = 1.0; df['taker_buy_volume'] = 0.5
    return df


def one(t=1, side=1, kind='market', price=NAN, stop=99.0, target=102.0, max_bars=10, be_r=NAN, expire=1):
    return _orders(np.array([t]), side, kind, price, expire, stop, target, max_bars, be_r)


NOFUND = pd.Series(dtype=float)


class SimulatorTest(unittest.TestCase):
    def test_market_long_hits_target(self):
        df = bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 103, 100, 102.5)])
        tr = simulate(df, NOFUND, one(), ZERO)
        self.assertEqual(tr.reason[0], 'target')
        self.assertAlmostEqual(tr.exit[0], 102.0)
        self.assertAlmostEqual(tr.net_r[0], 2.0)

    def test_stop_wins_when_both_touch_same_bar(self):
        df = bars([(100, 100, 100, 100), (100, 103, 98, 100)])
        tr = simulate(df, NOFUND, one(), ZERO)
        self.assertEqual(tr.reason[0], 'stop')
        self.assertAlmostEqual(tr.net_r[0], -1.0)

    def test_gap_through_stop_fills_at_open(self):
        df = bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100), (97, 97.5, 96, 97)])
        tr = simulate(df, NOFUND, one(), ZERO)
        self.assertAlmostEqual(tr.exit[0], 97.0)
        self.assertAlmostEqual(tr.net_r[0], -3.0)

    def test_limit_needs_trade_through(self):
        touch = bars([(101, 101, 101, 101), (101, 101.2, 100.0, 100.5), (100.5, 101, 100.2, 100.8)])
        o = one(kind='limit', price=100.0, stop=98.0, target=103.0, expire=2)
        self.assertTrue(simulate(touch, NOFUND, o, ZERO).empty)
        through = bars([(101, 101, 101, 101), (101, 101.2, 99.9, 100.5), (100.5, 104, 100.2, 103.5)])
        tr = simulate(through, NOFUND, o, ZERO)
        self.assertAlmostEqual(tr.entry[0], 100.0)
        self.assertEqual(tr.reason[0], 'target')

    def test_limit_entry_bar_cannot_take_target(self):
        df = bars([(101, 101, 101, 101), (101, 104, 99.5, 101), (101, 101.5, 100.5, 101)])
        o = one(kind='limit', price=100.0, stop=98.0, target=103.0, expire=1, max_bars=1)
        self.assertEqual(simulate(df, NOFUND, o, ZERO).reason[0], 'time')

    def test_stop_entry_fills_at_trigger_or_gap(self):
        df = bars([(100, 100, 100, 100), (102, 103, 101.5, 102.5), (102.5, 106, 102, 105)])
        o = one(kind='stop', price=101.0, stop=99.0, target=105.0)
        tr = simulate(df, NOFUND, o, ZERO)
        self.assertAlmostEqual(tr.entry[0], 102.0)   # opened above trigger

    def test_costs_and_cost_filter(self):
        df = bars([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 103, 100, 102.5)])
        c = Costs(taker=5e-4, maker=2e-4, slip=0.0, funding=False)
        tr = simulate(df, NOFUND, one(), c)
        self.assertAlmostEqual(tr.fees[0], 5e-4 + 2e-4 * 102 / 100)
        self.assertTrue(simulate(df, NOFUND, one(stop=99.9), c, max_cost_r=0.3).empty)

    def test_breakeven_moves_stop_after_trigger_bar(self):
        df = bars([(100, 100, 100, 100), (100, 101.2, 99.5, 101), (101, 101.1, 99.9, 100)])
        tr = simulate(df, NOFUND, one(stop=99.0, target=103.0, be_r=1.0), ZERO)
        self.assertEqual(tr.reason[0], 'breakeven')
        self.assertAlmostEqual(tr.net_r[0], 0.0)

    def test_funding_sign_and_window(self):
        df = bars([(100, 100, 100, 100)] * 4, start='2024-01-01 07:50')
        f = pd.Series([0.001, 0.002], index=pd.to_datetime(['2024-01-01 08:00', '2024-01-01 16:00'], utc=True).as_unit('ms'))
        long = simulate(df, f, one(target=NAN, max_bars=2), ZERO)
        short = simulate(df, f, one(side=-1, stop=101.0, target=NAN, max_bars=2), ZERO)
        self.assertAlmostEqual(long.funding[0], 0.001)
        self.assertAlmostEqual(short.funding[0], -0.001)

    def test_trailing_stop_ratchets_after_bar_close(self):
        df = bars([(100, 100, 100, 100), (100, 103, 99.5, 102.8), (102.8, 104, 102, 103), (103, 103.2, 101.9, 102)])
        o = _orders(np.array([1]), 1, 'market', NAN, 1, 98.0, NAN, 10, NAN, 2.0)
        tr = simulate(df, NOFUND, o, ZERO)
        self.assertEqual(tr.reason[0], 'trail')
        self.assertAlmostEqual(tr.exit[0], 102.0)      # 104 high - 2.0 trail, hit on the next bar

    def test_one_position_at_a_time(self):
        df = bars([(100, 100, 100, 100)] * 6)
        o = pd.concat([one(t=1, target=NAN, max_bars=3), one(t=2, target=NAN, max_bars=1)])
        self.assertEqual(len(simulate(df, NOFUND, o, ZERO)), 1)


class LiveCtxTest(unittest.TestCase):
    def test_placeholder_bar_is_positionable_and_inert(self):
        from daytrade.live import live_ctx
        df = bars([(i, i + 0.5, i - 0.5, i) for i in range(24)])           # two full hours
        now = df.index[-1] + pd.Timedelta(minutes=5, seconds=25)
        c = live_ctx('X', df, None, None, now)
        self.assertEqual(len(c.df), 25)
        self.assertEqual(c.pos(pd.DatetimeIndex([df.index[-1] + pd.Timedelta(minutes=5)]))[0], 24)
        self.assertEqual(len(c.bars('1h')), 2)                              # no partial hour created
        self.assertEqual(len(live_ctx('X', df, None, None).df), 24)


class IndicatorTest(unittest.TestCase):
    def test_higher_timeframe_has_no_lookahead(self):
        rows = [(i, i + 0.5, i - 0.5, i) for i in range(48)]
        df = bars(rows)
        h1 = ind.resample(df, '1h')
        mapped = ind.to_base(h1['close'], '1h', df.index)
        self.assertTrue(math.isnan(mapped.iloc[10]))     # first hour still forming
        self.assertEqual(mapped.iloc[11], 11)           # complete at close of 12th bar
        self.assertEqual(mapped.iloc[22], 11)
        self.assertEqual(mapped.iloc[23], 23)

    def test_prior_day_levels_use_previous_day(self):
        rows = [(1, 2, 0.5, 1)] * 288 + [(5, 9, 4, 5)] * 288
        lv = ind.prior_day_levels(bars(rows))
        self.assertEqual(lv['high'].iloc[288], 2)
        self.assertEqual(lv['high'].iloc[-1], 2)


if __name__ == '__main__':
    unittest.main()
