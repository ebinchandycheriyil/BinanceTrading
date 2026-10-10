import unittest
from scalp.signals import Candle
from scalp.pair_portfolio import HOUR,simulate,select_pair


class PairPortfolioTests(unittest.TestCase):
    def fixture(self):
        data={s:[Candle(i*HOUR,100,100.1,99.9,100,1e6) for i in range(80)] for s in ('A','B')}
        model=dict(x='A',y='B',z=2.,beta=1.,sd=.01,half=12.)
        return data,{0:{168:[model]}}

    def test_actual_funding_timestamp_order(self):
        data,f=self.fixture()
        events={'A':[dict(ts=t,rate=.001,mark=100) for t in (0,1,72*HOUR,72*HOUR+1)]}
        r=simulate(data,events,f,168,1.75,0,80*HOUR)
        t=r['trades'][0]
        self.assertEqual([e['ts'] for e in t['funding_events']],[1,72*HOUR])
        self.assertAlmostEqual(t['funding_cost'],.2*t['legs']['A']['qty'])
        self.assertAlmostEqual(t['net_pnl'],t['gross_pnl']-t['entry_fee']-t['exit_fee']-t['funding_cost'])

    def test_delayed_entry_does_not_use_future_signal(self):
        data,f=self.fixture()
        events={'A':[dict(ts=HOUR,rate=.001,mark=100),dict(ts=HOUR+1,rate=.001,mark=100)]}
        r=simulate(data,events,f,168,1.75,0,80*HOUR,entry_delay=1)
        t=r['trades'][0]
        self.assertEqual(t['opened'],HOUR)
        self.assertEqual(t['signal_time'],0)
        self.assertEqual([e['ts'] for e in t['funding_events']],[HOUR+1])

    def test_one_basket_no_duplicate_asset_exposure(self):
        data,f=self.fixture()
        f[24*HOUR]=f[0]
        r=simulate(data,{},f,168,1.75,0,80*HOUR)
        self.assertEqual(len(r['trades']),1)
        t=r['trades'][0]
        self.assertLessEqual(sum(l['entry']*l['qty'] for l in t['legs'].values()),801)
        self.assertEqual(len(t['legs']),2)
        self.assertLess(t['net_pnl'],0)

    def test_relationship_failure_exits(self):
        data,f=self.fixture();f[24*HOUR]={168:[]}
        t=simulate(data,{},f,168,1.75,0,80*HOUR)['trades'][0]
        self.assertEqual(t['exit_reason'],'RELATIONSHIP_INVALID')
        self.assertEqual(t['hold_hours'],24)

    def test_selection_rejects_tiny_move_and_extreme_z(self):
        _,f=self.fixture();m=f[0][168][0]
        self.assertEqual(select_pair([m],1.75),m)
        self.assertIsNone(select_pair([dict(m,z=5)],1.75))
        self.assertIsNone(select_pair([dict(m,sd=.0001)],1.75))
