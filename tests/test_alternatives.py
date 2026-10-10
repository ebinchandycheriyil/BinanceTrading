import unittest
import numpy as np

from scalp.alternatives import HOUR,pair_model,proposal,simulate,build_features
from scalp.signals import Candle
from scalp.alternative_study import grid,neighbors,identity,select


class AlternativeTests(unittest.TestCase):
    def data(self,n=80):
        return {s:[Candle(i*HOUR,100,100.1,99.9,100,1e6) for i in range(n)] for s in ('BTC-P','BTC-S','ETH-P','ETH-S')}

    def test_carry_is_equal_quantity_and_principal_not_profit(self):
        data=self.data();daily={0:dict(carry={'BTC-P':.2,'ETH-P':.1})}
        cfg=dict(family='carry',threshold=.05,hold=48)
        result=simulate(data,{},daily,cfg,0,80*HOUR)
        self.assertEqual(len(result['trades']),1)
        t=result['trades'][0]
        self.assertEqual(t['legs']['BTC-P']['qty'],t['legs']['BTC-S']['qty'])
        self.assertLess(t['net_pnl'],0)
        self.assertEqual(t['hold_hours'],48)
        self.assertGreater(t['futures_collateral'],0)
        self.assertAlmostEqual(result['equity'][-1]['equity'],1000+t['net_pnl'])

    def test_funding_pays_short_and_no_entry_boundary_charge(self):
        cfg=dict(family='carry',threshold=.05,hold=48)
        events={'BTC-P':[dict(ts=0,mark=100,rate=.9),dict(ts=8*HOUR,mark=100,rate=.001)]}
        result=simulate(self.data(),events,{0:dict(carry={'BTC-P':.2,'ETH-P':.1})},cfg,0,80*HOUR)
        t=result['trades'][0]
        self.assertAlmostEqual(t['funding_cost'],-t['legs']['BTC-P']['qty']*.1)
        self.assertAlmostEqual(t['net_pnl'],t['gross_pnl']-t['entry_fee']-t['exit_fee']-t['funding_cost'])

    def test_pair_hedge_direction_and_weights(self):
        m=dict(z=2,beta=1.5,sd=.01)
        p=proposal(dict(family='pairs',window=168,z=1.5),dict(pairs={168:m}))
        self.assertLess(p['weights']['ETH-P'],0)
        self.assertGreater(p['weights']['BTC-P'],0)
        self.assertAlmostEqual(sum(abs(w) for w in p['weights'].values()),1)
        m['z']=-2
        self.assertGreater(proposal(dict(family='pairs',window=168,z=1.5),dict(pairs={168:m}))['weights']['ETH-P'],0)

    def test_pair_stationarity_screen(self):
        rng=np.random.default_rng(42);x=np.cumsum(rng.normal(0,.01,600))+5
        e=np.zeros(600)
        for i in range(1,600):e[i]=.8*e[i-1]+rng.normal(0,.001)
        m=pair_model(np.exp(x),np.exp(.3+1.2*x+e))
        self.assertIsNotNone(m)
        self.assertLess(m['p'],.05)
        self.assertAlmostEqual(m['beta'],1.2,delta=.05)
        self.assertIsNone(pair_model([100]*200,[100]*200))

    def test_cross_momentum_signs_and_dollar_neutrality(self):
        daily=dict(momentum={168:[('A-P',-.2,.01),('B-P',-.1,.02),('C-P',.1,.03),('D-P',.2,.04)]})
        p=proposal(dict(family='momentum',window=168,hold=72),daily)
        self.assertAlmostEqual(sum(p['weights'].values()),0)
        self.assertAlmostEqual(sum(abs(w) for w in p['weights'].values()),1)
        self.assertLess(p['weights']['A-P'],0)
        self.assertGreater(p['weights']['D-P'],0)

    def test_features_ignore_current_future_prices_and_funding(self):
        data=self.data(220);funding={'BTC-P':[],'ETH-P':[]}
        before=build_features(data,funding,(168,))[0][192*HOUR]
        for s,rows in data.items():
            data[s]=rows[:192]+[Candle(i*HOUR,200,201,199,200,1e6) for i in range(192,220)]
        funding['BTC-P']=[dict(ts=192*HOUR,rate=.5,mark=200)]
        self.assertEqual(build_features(data,funding,(168,))[0][192*HOUR],before)

    def test_grid_and_neighborhoods_bounded(self):
        self.assertEqual(len(grid()),21)
        self.assertEqual(len({identity(c) for c in grid()}),21)
        for c in grid():self.assertEqual(len(neighbors(c)),9)

    def test_selection_does_not_reward_no_trades(self):
        rows=[dict(id='idle',quarters={'a':0},summary=dict(trades=0,net_pnl=0)),
              dict(id='active',quarters={'a':-1},summary=dict(trades=10,net_pnl=-1))]
        self.assertEqual(select(rows)['id'],'active')

    def test_missing_spot_quote_blocks_entry_and_defers_exit(self):
        cfg=dict(family='carry',threshold=.05,hold=48)
        daily={0:dict(carry={'BTC-P':.2,'ETH-P':.1})}
        result=simulate(self.data(),{},daily,cfg,0,80*HOUR,unavailable_spot=[0])
        self.assertEqual(result['trades'],[])
        result=simulate(self.data(),{},daily,cfg,0,80*HOUR,unavailable_spot=[48*HOUR])
        self.assertEqual(result['trades'][0]['hold_hours'],49)
        self.assertTrue(result['equity'][48]['stale_spot'])

    def test_funding_still_settles_during_spot_outage(self):
        cfg=dict(family='carry',threshold=.05,hold=48)
        events={'BTC-P':[dict(ts=8*HOUR,mark=100,rate=.001)]}
        result=simulate(self.data(),events,{0:dict(carry={'BTC-P':.2,'ETH-P':.1})},cfg,0,80*HOUR,unavailable_spot=[8*HOUR])
        t=result['trades'][0]
        self.assertAlmostEqual(t['funding_cost'],-t['legs']['BTC-P']['qty']*.1)
