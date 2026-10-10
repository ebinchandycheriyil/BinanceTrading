"""Independent accounting and market-boundary checks for dated carry."""
from dataclasses import replace
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from scalp.carry import CarrySettings, evaluate, quote_errors, scenario
from scalp.carry_market import common_step, discover
from scalp.carry_paper import CarryPaper


NOW = 1_800_000_000.


def contract():
    def book(bid, ask):
        return dict(bid=bid, ask=ask, bid_size=100., ask_size=100.,
                    requested_at=NOW-.2, ts=NOW-.1, exchange_ts=NOW-.1)
    limits = dict(step='0.001', min_qty=.001, max_qty=100.,
                  min_notional=5., max_notional=None)
    return dict(id='BTCUSDT_270625', contract_type='NEXT_QUARTER',
                spot_symbol='BTCUSDT', base='BTC', expiry=NOW+90*86400,
                spot=book(100., 100.05), future=book(120., 120.05),
                mark=dict(price=120.025, ts=NOW-.1),
                spot_limits=dict(limits), future_limits=dict(limits))


def fresh(c, now):
    c = deepcopy(c)
    for leg in ('spot', 'future'):
        c[leg].update(requested_at=now-.2, ts=now-.1, exchange_ts=now-.1)
    c['mark']['ts'] = now-.1
    return c


class CarryIndependentReviewTests(unittest.TestCase):
    def test_convergence_profit_matches_independent_cash_flow(self):
        c = contract(); cfg = CarrySettings(); qty = 1.237
        r = scenario(c, qty, NOW, cfg)
        # Sell retained base, cover the short at a common terminal price.
        # Entry commission paid in base must not be subtracted twice.
        terminal = (c['spot']['bid']+c['spot']['ask'])/2
        cash_profit = (qty*terminal*(1-cfg.spot_fee)-r['gross_spot_qty']*r['spot_entry']
                       +qty*(r['future_entry']-terminal)
                       -qty*r['future_entry']*cfg.future_fee-qty*terminal*cfg.future_fee)
        expected = (cash_profit-r['exit_slippage']-r['exit_spread']
                    -r['residual_basis_reserve']-r['capital_charge'])
        self.assertAlmostEqual(r['estimated_net'], expected, places=10)
        self.assertAlmostEqual(r['spot_fee']+r['dust_cost'],
                               (r['gross_spot_qty']-qty)*r['spot_entry'], places=10)

    def test_fee_in_base_leaves_equal_hedge_and_explicit_dust(self):
        c = contract(); cfg = CarrySettings(); c['spot_limits']['step'] = '.03'
        for multiplier in (1., cfg.stress_multiplier):
            r = scenario(c, 1.26, NOW, cfg, multiplier)
            received = r['gross_spot_qty']*(1-cfg.spot_fee*multiplier)
            self.assertGreaterEqual(received+1e-12, r['qty'])
            self.assertAlmostEqual(received-r['qty'], r['dust_qty'], places=12)
            self.assertEqual(Decimal(str(r['gross_spot_qty'])) % Decimal('.03'), 0)

    def test_incompatible_lot_steps_and_stressed_capital_are_respected(self):
        c = contract(); cfg = CarrySettings()
        c['spot_limits']['step'] = '.03'; c['future_limits']['step'] = '.02'
        r = evaluate(c, NOW, cfg, budget=137.)
        self.assertEqual(common_step('.03', '.02'), '0.06')
        self.assertIn('base', r)
        self.assertEqual(Decimal(str(r['base']['qty'])) % Decimal('.06'), 0)
        for label in ('base', 'stress'):
            self.assertLessEqual(r[label]['capital'], 137.+1e-8)
        self.assertLessEqual(r['stress']['estimated_net'], r['base']['estimated_net'])

    def test_fresh_but_unsynchronized_books_rejected(self):
        c = contract()
        c['spot'].update(requested_at=NOW-5, ts=NOW-4.9, exchange_ts=NOW-4.9)
        c['future'].update(requested_at=NOW-1, ts=NOW-.9, exchange_ts=NOW-.9)
        self.assertEqual(quote_errors(c, NOW, CarrySettings()), ['unsynchronized_books'])

    def test_stale_exchange_timestamp_cannot_be_refreshed_by_local_receipt(self):
        c = contract(); c['future']['exchange_ts'] = NOW-30
        self.assertIn('future_stale_exchange_book', quote_errors(c, NOW, CarrySettings()))

    def test_entry_depth_and_net_spot_exit_minimum_are_enforced(self):
        c = contract(); c['future']['bid_size'] = .001
        self.assertIn('quantity_below_step', evaluate(c, NOW, CarrySettings())['reasons'])
        c = contract(); c['spot_limits']['min_notional'] = 100.2
        r = evaluate(c, NOW, CarrySettings(), budget=220.2)
        self.assertEqual(r['decision'], 'REJECT')
        self.assertIn('spot_exit_below_minimum', r['reasons'])

    def test_capital_charge_changes_economic_profit_without_changing_entry_cash(self):
        c = contract(); low = CarrySettings(); high = replace(low, capital_charge_apr=.08)
        a = scenario(c, 1., NOW, low); b = scenario(c, 1., NOW, high)
        self.assertEqual(a['spot_cash'], b['spot_cash'])
        self.assertEqual(a['capital'], b['capital'])
        self.assertAlmostEqual(a['estimated_net']-b['estimated_net'], a['capital_charge'])

    def test_discovery_excludes_perpetual_and_coin_margined_contracts(self):
        filters = [dict(filterType='LOT_SIZE', minQty='.001', maxQty='100', stepSize='.001'),
                   dict(filterType='MIN_NOTIONAL', minNotional='5')]
        spot = dict(symbol='BTCUSDT', status='TRADING', filters=filters,
                    isSpotTradingAllowed=True)
        dated = dict(symbol='BTCUSDT_270625', contractType='NEXT_QUARTER', status='TRADING',
                     baseAsset='BTC', marginAsset='USDT', quoteAsset='USDT',
                     deliveryDate=int((NOW+90*86400)*1000), filters=filters)
        futures = [dated, dict(dated, symbol='BTCUSDT', contractType='PERPETUAL'),
                   dict(dated, symbol='BTCUSD_270625', marginAsset='BTC', quoteAsset='USD')]
        found = discover(dict(symbols=[spot]), dict(symbols=futures), NOW)
        self.assertEqual([r['id'] for r in found], ['BTCUSDT_270625'])

    def test_restart_and_unwind_reconcile_to_actual_cash_flows(self):
        cfg = CarrySettings(); c = contract()
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/'review.db')
            paper = CarryPaper(cfg, path)
            paper.cycle(dict(contracts=[c], errors=[]), NOW)
            self.assertEqual(len(paper.s['positions']), 1)
            entry = deepcopy(paper.s['positions'][c['id']])
            after_entry = paper.s['balance']
            paper.close()
            paper = CarryPaper(cfg, path)
            try:
                self.assertEqual(paper.s['balance'], after_entry)
                paper.cycle(dict(contracts=[fresh(c, NOW+1)], errors=[]), NOW+1)
                self.assertEqual(len(paper.s['positions']), 1)
                self.assertEqual(paper.s['balance'], after_entry)
                close_time = NOW+86400
                converged = fresh(c, close_time)
                converged['future'].update(bid=100., ask=100.05)
                converged['mark']['price'] = 100.025
                paper.cycle(dict(contracts=[converged], errors=[]), close_time,
                            allow_entries=False, force_close=True)
                self.assertFalse(paper.s['positions'])
                trade = paper.s['trades'][0]; qty = entry['qty']
                cash_profit = (qty*trade['spot_exit']*(1-cfg.spot_fee)
                               -entry['gross_spot_qty']*entry['spot_entry']
                               +qty*(entry['future_entry']-trade['future_exit'])
                               -qty*entry['future_entry']*cfg.future_fee
                               -qty*trade['future_exit']*cfg.future_fee)
                self.assertAlmostEqual(trade['cash_net'], cash_profit, places=10)
                self.assertAlmostEqual(paper.s['balance'], 1000.+cash_profit, places=10)
                self.assertAlmostEqual(trade['economic_net'], cash_profit-entry['capital']*.04/365, places=10)
                self.assertEqual(paper.conn.execute(
                    "SELECT count(*) FROM carry_events WHERE json_extract(body, '$.event')='ENTRY'").fetchone()[0], 1)
            finally:
                paper.close()

    def test_missing_mark_blocks_exit_without_removing_position(self):
        paper = CarryPaper(CarrySettings())
        try:
            c = contract(); paper.cycle(dict(contracts=[c]), NOW)
            broken = fresh(c, NOW+1); broken['mark'] = None
            events = paper.cycle(dict(contracts=[broken]), NOW+1,
                                 allow_entries=False, force_close=True)
            self.assertEqual(len(paper.s['positions']), 1)
            self.assertFalse(paper.s['trades'])
            self.assertTrue(any(e['event']=='DATA_HALT' for e in events))
            self.assertIsNone(paper.status({c['id']: broken}, NOW+1)['cash_equity'])
        finally:
            paper.close()

    def test_expired_contract_is_unresolved_not_fabricated_settlement(self):
        paper = CarryPaper(CarrySettings())
        try:
            c = contract(); paper.cycle(dict(contracts=[c]), NOW)
            events = paper.cycle(dict(contracts=[]), c['expiry']+1,
                                 allow_entries=False, force_close=True)
            self.assertEqual(len(paper.s['positions']), 1)
            self.assertFalse(paper.s['trades'])
            self.assertTrue(any(e['event']=='SETTLEMENT_REQUIRED' for e in events))
        finally:
            paper.close()

    def test_pending_exit_survives_missing_depth_and_restart(self):
        cfg = CarrySettings(); c = contract()
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/'pending.db')
            paper = CarryPaper(cfg, path)
            paper.cycle(dict(contracts=[c]), NOW)
            thin = fresh(c, NOW+1); thin['spot']['bid_size'] = .001
            events = paper.cycle(dict(contracts=[thin]), NOW+1,
                                 allow_entries=False, force_close=True)
            self.assertTrue(any(e['event']=='DATA_HALT' for e in events))
            self.assertEqual(len(paper.s['positions']), 1)
            paper.close()
            paper = CarryPaper(cfg, path)
            try:
                # No repeated force-close request. Restored pending intent must act.
                paper.cycle(dict(contracts=[fresh(c, NOW+2)]), NOW+2, allow_entries=False)
                self.assertFalse(paper.s['positions'])
                self.assertEqual(len(paper.s['trades']), 1)
                self.assertEqual(paper.s['trades'][0]['reason'], 'REQUESTED_CLOSE')
            finally:
                paper.close()

    def test_force_exit_requested_without_quotes_is_sticky(self):
        paper = CarryPaper(CarrySettings()); c = contract()
        try:
            paper.cycle(dict(contracts=[c]), NOW)
            paper.cycle(dict(contracts=[]), NOW+1, allow_entries=False, force_close=True)
            paper.cycle(dict(contracts=[fresh(c, NOW+2)]), NOW+2, allow_entries=False)
            self.assertFalse(paper.s['positions'])
            self.assertEqual(paper.s['trades'][0]['reason'], 'REQUESTED_CLOSE')
        finally:
            paper.close()

    def test_capital_cap_uses_post_entry_equity_for_two_baskets(self):
        cfg = CarrySettings(); c = contract(); eth = contract()
        eth.update(id='ETHUSDT_270625', base='ETH', spot_symbol='ETHUSDT')
        paper = CarryPaper(cfg)
        try:
            paper.cycle(dict(contracts=[c, eth]), NOW)
            status = paper.status({c['id']:c, eth['id']:eth}, NOW)
            self.assertEqual(status['positions'], 2)
            self.assertLessEqual(status['capital_reserved'], status['cash_equity']*cfg.portfolio_fraction)
            for p in paper.s['positions'].values():
                self.assertEqual(p['stop_usdt'], p['take_profit_usdt'])
        finally:
            paper.close()


if __name__ == '__main__':
    unittest.main()
