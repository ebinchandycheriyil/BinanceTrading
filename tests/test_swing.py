from dataclasses import replace
import unittest

from scalp.signals import Candle
from scalp.swing import HOUR,SwingConfig,features,simulate,fill
from scalp.swing_study import rank


class SwingTests(unittest.TestCase):
    def fixture(self,sign=1,hours=30):
        rows=[Candle(i*HOUR,100,100.1,99.9,100,10) for i in range(hours)]
        prepared={c.ts:dict(trend=sign,breakout=sign if i==0 else 0,pullback=0,atr_fraction=.005) for i,c in enumerate(rows)}
        return {'BTC':rows},{'BTC':prepared}

    def test_ratio_time_limit_costs_and_accounting(self):
        for sign in (1,-1):
            data,f=self.fixture(sign)
            result=simulate(data,f,{},SwingConfig(max_hours=24),0,30*HOUR)
            t=result['trades'][0]
            self.assertEqual(t['exit_reason'],'TIME_LIMIT')
            self.assertEqual(t['hold_hours'],24)
            self.assertAlmostEqual(abs(t['entry']-t['stop']),abs(t['target']-t['entry']))
            self.assertLess(t['net_pnl'],0)
            self.assertAlmostEqual(result['equity'][-1]['equity'],1000+t['net_pnl'])

    def test_funding_sign_and_no_entry_time_charge(self):
        for sign in (1,-1):
            data,f=self.fixture(sign)
            events={'BTC':[dict(ts=0,mark=100,rate=.1),dict(ts=8*HOUR,mark=100,rate=.001)]}
            result=simulate(data,f,events,SwingConfig(max_hours=24),0,30*HOUR)
            t=result['trades'][0]
            self.assertAlmostEqual(t['funding_cost'],sign*t['qty']*.1)
            self.assertAlmostEqual(t['net_pnl'],t['gross_pnl']-t['entry_fee']-t['exit_fee']-t['funding_cost'])

    def test_stop_wins_intrabar_tie(self):
        data,f=self.fixture()
        data['BTC'][0]=Candle(0,100,102,98,100,10)
        t=simulate(data,f,{},SwingConfig(),0,30*HOUR)['trades'][0]
        self.assertEqual(t['exit_reason'],'STOP')
        self.assertEqual(t['exit'],t['stop'])

    def test_gap_is_not_filled_at_stop(self):
        data,f=self.fixture()
        data['BTC'][1]=Candle(HOUR,98,98.1,97.9,98,10)
        t=simulate(data,f,{},SwingConfig(),0,30*HOUR)['trades'][0]
        self.assertEqual(t['exit_reason'],'STOP_GAP')
        self.assertLess(t['exit'],t['stop'])

    def test_hourly_trend_exit(self):
        data,f=self.fixture()
        f['BTC'][HOUR]['trend']=-1
        t=simulate(data,f,{},SwingConfig(),0,30*HOUR)['trades'][0]
        self.assertEqual(t['exit_reason'],'TREND_INVALIDATION')
        self.assertEqual(t['hold_hours'],1)

    def test_current_and_future_prices_cannot_change_current_features(self):
        rows=[Candle(i*HOUR,100+i*.01,100.1+i*.01,99.9+i*.01,100+i*.01,10) for i in range(250)]
        expected=features(rows)[240*HOUR]
        changed=rows[:240]+[Candle(i*HOUR,200,210,190,200,10) for i in range(240,250)]
        self.assertEqual(features(changed)[240*HOUR],expected)

    def test_selection_rejects_zero_activity_if_eligible_exists(self):
        def folds(n,p):return [dict(summary=dict(trades=n,net_pnl=p))]*2
        self.assertEqual(rank({'idle':folds(0,0),'active':folds(25,-1)}),'active')

    def test_fill_charges_adverse_cost_each_direction(self):
        cfg=SwingConfig()
        self.assertGreater(fill(100,1,cfg),100)
        self.assertLess(fill(100,-1,cfg),100)

    def test_72_hour_limit_and_no_same_bar_reentry(self):
        data,f=self.fixture(hours=80)
        result=simulate(data,f,{},SwingConfig(max_hours=72),0,80*HOUR)
        self.assertEqual(len(result['trades']),1)
        self.assertEqual(result['trades'][0]['hold_hours'],72)

    def test_two_symbols_share_risk_and_gross_limits(self):
        data,f=self.fixture()
        data['ETH']=list(data['BTC']);f['ETH']=dict(f['BTC'])
        result=simulate(data,f,{},SwingConfig(max_hours=24),0,30*HOUR)
        self.assertEqual(len(result['trades']),2)
        self.assertLessEqual(sum(t['risk'] for t in result['trades']),5)
        self.assertLessEqual(sum(t['entry']*t['qty'] for t in result['trades']),1000)

    def test_closed_four_hour_features_do_not_change_inside_bucket(self):
        rows=[Candle(i*HOUR,100+i*.01,100.1+i*.01,99.9+i*.01,100+i*.01,10) for i in range(250)]
        f=features(rows)
        self.assertEqual(f[240*HOUR]['trend'],f[243*HOUR]['trend'])
