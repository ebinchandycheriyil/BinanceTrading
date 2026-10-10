import copy
import unittest
from unittest.mock import patch

import numpy as np

from scalp.pair_screen import HOUR, _fit, benjamini_hochberg, build_pair_features
from scalp.signals import Candle


class PairScreenTests(unittest.TestCase):
    def data(self, n=193, symbols=('A-P', 'B-P', 'C-P')):
        rng = np.random.default_rng(482)
        x = 5 + np.cumsum(rng.normal(0, .007, n))
        residual = np.zeros(n)
        for i in range(1, n):
            residual[i] = .8 * residual[i-1] + rng.normal(0, .002)
        return {s: [Candle(i*HOUR, p, p*1.001, p*.999, p, 1e6)
                    for i, p in enumerate(np.exp(.1*j + (1+.1*j)*x + (j+1)*residual))]
                for j, s in enumerate(symbols)}

    def fake_fit(self, *_):
        return dict(beta=1.2, intercept=.1, sd=.01, p=.001, half=8., phi=.9, z=2.)

    def test_bh_denominator_contains_all_tests(self):
        self.assertEqual(benjamini_hochberg([.01]), [True])
        self.assertEqual(benjamini_hochberg([.01] + [1]*27), [False]*28)
        self.assertEqual(benjamini_hochberg([.001, None, float('nan')]), [True, False, False])

    def test_bh_step_up_and_order_are_correct(self):
        self.assertEqual(benjamini_hochberg([.049, .034, .9]), [False]*3)
        self.assertEqual(benjamini_hochberg([.049, .001, .025]), [True]*3)
        self.assertEqual(benjamini_hochberg([]), [])

    @patch('scalp.pair_screen._adf_p', return_value=.4)
    def test_failed_fits_do_not_shrink_hypothesis_family(self, _):
        data = self.data(symbols=tuple(f'{x}-P' for x in 'ABCDEFGH'))
        result = self.fake_fit(); result['p'] = .01
        with patch('scalp.pair_screen._fit', side_effect=[result] + [None]*27):
            features, stats = build_pair_features({s: rows[:169] for s, rows in data.items()}, (168,))
        self.assertEqual(features[168*HOUR][168], [])
        self.assertEqual(stats['totals']['tested_pairs'], 28)
        self.assertEqual(stats['totals']['fit_failed'], 27)

    @patch('scalp.pair_screen._adf_p', return_value=.4)
    @patch('scalp.pair_screen._fit')
    def test_fixed_orientation_and_liquidity_use_prior_hours(self, fit, _):
        fit.side_effect = self.fake_fit
        data = self.data(169, ('C-P', 'A-P', 'B-P'))
        data['C-P'] = [Candle(c.ts, c.open, c.high, c.low, c.close, 0) for c in data['C-P']]
        data['C-P'][-1] = Candle(168*HOUR, 200, 201, 199, 200, 1e10)
        features, stats = build_pair_features(data, (168,))
        models = features[168*HOUR][168]
        self.assertEqual([(m['x'], m['y']) for m in models], [('A-P', 'B-P')])
        self.assertEqual(stats['totals']['tested_pairs'], 1)
        self.assertEqual(models[0]['formation_end'], 167*HOUR)

    def test_current_and_future_changes_leave_earlier_models_unchanged(self):
        data = self.data()
        before = build_pair_features(data, (144, 168))[0][168*HOUR]
        self.assertGreater(sum(len(models) for models in before.values()), 0)
        modified = copy.deepcopy(data)
        for s, rows in modified.items():
            modified[s] = rows[:168] + [Candle(c.ts, 2000, 2010, 1990, 2000, 0) for c in rows[168:]]
        after = build_pair_features(modified, (144, 168))[0][168*HOUR]
        self.assertEqual(after, before)
        self.assertEqual(before, build_pair_features({s: r[:169] for s, r in data.items()}, (144, 168))[0][168*HOUR])

    def test_fit_recovers_cointegrating_relationship(self):
        rng = np.random.default_rng(42)
        x = 5 + np.cumsum(rng.normal(0, .01, 600))
        residual = np.zeros(600)
        for i in range(1, 600):
            residual[i] = .85*residual[i-1] + rng.normal(0, .001)
        result = _fit(x, .3 + 1.2*x + residual)
        self.assertAlmostEqual(result['beta'], 1.2, delta=.03)
        self.assertLess(result['p'], .01)
        self.assertTrue(2 < result['half'] < 12)
        self.assertIsNone(_fit(np.ones(100), np.ones(100)))

    @patch('scalp.pair_screen._adf_p', return_value=.01)
    @patch('scalp.pair_screen._fit')
    def test_price_stationarity_blocks_cointegration_interpretation(self, fit, _):
        fit.side_effect = self.fake_fit
        features, stats = build_pair_features(self.data(169), (168,))
        self.assertEqual(features[168*HOUR][168], [])
        self.assertEqual(stats['totals']['price_adf_stationary'], 3)

    def test_misaligned_and_gapped_data_rejected(self):
        data = self.data()
        data['A-P'] = data['A-P'][1:]
        with self.assertRaises(ValueError): build_pair_features(data)
        data = self.data()
        data = {s: rows[:50]+rows[51:] for s, rows in data.items()}
        with self.assertRaises(ValueError): build_pair_features(data)


if __name__ == '__main__':
    unittest.main()
