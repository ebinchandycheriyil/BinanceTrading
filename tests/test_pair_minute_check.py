import unittest

from scalp.pair_minute_check import MINUTE, evaluate_basket, exit_mark
from scalp.signals import Candle


class PairMinuteCheckTests(unittest.TestCase):
    def trade(self, deadline=3*MINUTE-1):
        return dict(opened=0, exit_time=deadline/1000, exit_reason='RELATIONSHIP_INVALID',
                    net_pnl=-.2, risk=1., loss_budget=1.2, entry_fee=.1,
                    legs={'A-P': dict(sign=1, qty=1., entry=100.),
                          'B-P': dict(sign=-1, qty=1., entry=100.)})

    def data(self, a=(100., 100., 100.), b=(100., 100., 100.)):
        return {symbol: [Candle(i*MINUTE, p, p+.1, p-.1, p, 1e5) for i, p in enumerate(prices)]
                for symbol, prices in [('A-P', a), ('B-P', b)]}

    def funding(self):
        return {'A-P': [], 'B-P': []}

    def test_minute_stop_and_all_four_fee_accounting(self):
        r = evaluate_basket(self.trade(), self.data(a=(100., 98., 101.)), self.funding())
        self.assertEqual(r['minute_exit_time'], MINUTE)
        self.assertEqual(r['minute_exit_reason'], 'STOP')
        self.assertTrue(r['earlier_exit'])
        self.assertAlmostEqual(r['minute_net'], r['gross_pnl']-.1-r['exit_fee']-r['funding_cost'])

    def test_target_and_short_leg_direction(self):
        r = evaluate_basket(self.trade(), self.data(b=(100., 98., 103.)), self.funding())
        self.assertEqual(r['minute_exit_time'], MINUTE)
        self.assertEqual(r['minute_exit_reason'], 'TARGET')
        self.assertGreater(r['minute_net'], 0)

    def test_original_open_deadline_excludes_later_bar_close(self):
        data = self.data()
        data['A-P'][1] = Candle(MINUTE, 100, 105, 99, 105, 1e5)
        r = evaluate_basket(self.trade(MINUTE), data, self.funding())
        self.assertEqual(r['minute_exit_time'], MINUTE)
        self.assertEqual(r['minute_exit_reason'], 'RELATIONSHIP_INVALID')
        self.assertLess(r['gross_pnl'], 0)

    def test_funding_exact_entry_and_revised_exit_boundaries(self):
        f = {'A-P': [dict(ts=0, mark=100., rate=.1),
                     dict(ts=MINUTE, mark=100., rate=.001),
                     dict(ts=MINUTE+1, mark=100., rate=.3)],
             'B-P': [dict(ts=MINUTE, mark=100., rate=.002)]}
        r = evaluate_basket(self.trade(), self.data(a=(100., 98., 101.)), f)
        self.assertEqual(len(r['funding_events']), 2)
        self.assertAlmostEqual(r['funding_cost'], -.1)

    def test_intraminute_envelope_is_ambiguity_not_fill(self):
        data = self.data()
        data['A-P'][0] = Candle(0, 100, 100, 98, 100, 1e5)
        r = evaluate_basket(self.trade(), data, self.funding())
        self.assertTrue(r['adverse_envelope_ambiguous'])
        self.assertEqual(r['minute_exit_time'], 3*MINUTE-1)
        self.assertEqual(r['minute_exit_reason'], 'RELATIONSHIP_INVALID')

    def test_missing_or_duplicate_minutes_rejected(self):
        data = self.data(); data['B-P'].pop(1)
        with self.assertRaises(ValueError): evaluate_basket(self.trade(), data, self.funding())
        data = self.data(); data['A-P'].insert(1, data['A-P'][0])
        with self.assertRaises(ValueError): evaluate_basket(self.trade(), data, self.funding())

    def test_prices_after_original_deadline_never_change_result(self):
        data = self.data()
        before = evaluate_basket(self.trade(MINUTE), data, self.funding())
        for s in data:
            data[s][2] = Candle(2*MINUTE, 1000, 1100, 990, 1000, 1e5)
        self.assertEqual(evaluate_basket(self.trade(MINUTE), data, self.funding()), before)

    def test_multiplicative_exit_slippage_matches_portfolio(self):
        gross, fees, exits = exit_mark(self.trade()['legs'], {'A-P': 100, 'B-P': 100})
        self.assertAlmostEqual(exits['A-P'], 100*.9998*.9998)
        self.assertAlmostEqual(exits['B-P'], 100*1.0002*1.0002)
        self.assertAlmostEqual(fees, sum(exits.values())*.0005)
        self.assertAlmostEqual(gross, -.08)


if __name__ == '__main__':
    unittest.main()
