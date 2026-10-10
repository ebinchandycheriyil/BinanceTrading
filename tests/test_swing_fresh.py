import unittest
from scalp.swing_fresh import BASE,candidates,select,protocol
from scalp.swing_sweep import name


class FreshStudyTests(unittest.TestCase):
    def row(self,label,jan,feb,trades=20):
        return dict(id=label,months={'January':jan,'February':feb},summary=dict(trades=trades,net_pnl=jan+feb))

    def test_small_grid_unique_and_includes_original(self):
        grid=candidates()
        self.assertEqual(len(grid),27)
        self.assertEqual(len({name(c) for c in grid}),27)
        self.assertIn(BASE,grid)
        for c in grid:
            self.assertLessEqual(abs(c['lookback']-BASE['lookback']),2)
            self.assertLessEqual(abs(c['atr_multiple']-BASE['atr_multiple']),.25)
            self.assertLessEqual(abs(c['max_hours']-BASE['max_hours']),6)

    def test_selection_favors_monthly_stability(self):
        stable=self.row('stable',2,3)
        spike=self.row('spike',50,-1)
        self.assertEqual(select([spike,stable]),stable)

    def test_minimum_activity_and_deterministic_tie(self):
        active=self.row('active',-1,-1)
        idle=self.row('idle',0,0,0)
        self.assertEqual(select([idle,active]),active)
        self.assertEqual(select([self.row('b',1,2),self.row('a',1,2)])['id'],'a')

    def test_final_holdout_follows_development(self):
        p=protocol()
        self.assertLess(p['start'],p['split'])
        self.assertLess(p['split'],p['end'])
