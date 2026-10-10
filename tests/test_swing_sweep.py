from dataclasses import replace
import unittest

from scalp.signals import Candle
from scalp.swing import HOUR,features
from scalp.swing_sweep import SweepConfig,aggregate,chart_features,candidates,nearby,name,period_changes


class SweepTests(unittest.TestCase):
    def rows(self,n=300):
        return [Candle(i*HOUR,100+i*.02,100.15+i*.02,99.9+i*.02,100+i*.02,10) for i in range(n)]

    def test_aggregate_uses_only_complete_utc_groups(self):
        rows=self.rows(9)
        bars=aggregate(rows,4)
        self.assertEqual(len(bars),2)
        self.assertEqual(bars[0].ts,0)
        self.assertEqual(bars[0].volume,40)
        self.assertEqual(bars[0].close,rows[3].close)
        self.assertEqual(aggregate(rows[:2]+rows[3:],4)[0].ts,4*HOUR)

    def test_hourly_lookback20_matches_original_signals(self):
        rows=self.rows();original=features(rows)
        self.assertEqual(chart_features(rows,1,20,original),original)

    def test_no_signals_between_chart_closes(self):
        rows=self.rows();original=features(rows)
        for hours in (2,4):
            found=chart_features(rows,hours,20,original)
            for ts,f in found.items():
                if ts%(hours*HOUR):
                    self.assertEqual((f['breakout'],f['pullback']),(0,0))
                self.assertEqual(f['trend'],original[ts]['trend'])

    def test_current_and_future_bars_do_not_change_entry(self):
        rows=self.rows();ts=240*HOUR
        for hours in (1,2,4):
            expected=chart_features(rows,hours,20,features(rows))[ts]
            changed=rows[:240]+[Candle(i*HOUR,200,210,190,200,10) for i in range(240,300)]
            self.assertEqual(chart_features(changed,hours,20,features(changed))[ts],expected)

    def test_grid_unique_and_refinements_bounded(self):
        grid=candidates()
        self.assertEqual(len(grid),270)
        self.assertEqual(len({name(c) for c in grid}),270)
        for c in grid:
            for n in nearby(c):
                self.assertLessEqual(n['max_hours'],72)
                self.assertLessEqual(abs(n['atr_multiple']-c['atr_multiple']),.25)

    def test_fractional_or_excessive_holds_rejected(self):
        for h in (5,73,24.5):
            with self.assertRaises(ValueError):SweepConfig(max_hours=h)
        self.assertEqual(SweepConfig(max_hours=30).max_hours,30)

    def test_quarter_attribution_sums_to_total(self):
        from scalp.experiments import stamp
        curve=[dict(ts=stamp('2025-04-01'),equity=1010),dict(ts=stamp('2025-07-01'),equity=1005),
               dict(ts=stamp('2025-10-01'),equity=1020),dict(ts=stamp('2026-01-01'),equity=1015)]
        changes=period_changes(curve,'quarter')
        self.assertEqual(changes,{'2025-Q1':10,'2025-Q2':-5,'2025-Q3':15,'2025-Q4':-5})
        self.assertEqual(sum(changes.values()),15)
