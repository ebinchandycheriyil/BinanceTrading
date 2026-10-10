"""Persistent, quote-driven cash-and-carry PAPER account. Cannot send orders."""
from dataclasses import asdict
from copy import deepcopy
from datetime import datetime, timezone
import json
import sqlite3

from .carry import evaluate, quote_errors


class CarryPaper:
    def __init__(self, cfg, path=':memory:', emit=None):
        cfg.validate()
        self.cfg, self.emit = cfg, emit or (lambda event: None)
        self.conn = sqlite3.connect(path)
        self.conn.execute('PRAGMA journal_mode=WAL')
        if self.conn.execute("SELECT name FROM sqlite_master WHERE name='state'").fetchone():
            self.conn.close()
            raise ValueError('Use a separate carry database, not a scalping account')
        self.conn.execute('CREATE TABLE IF NOT EXISTS carry_state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        self.conn.execute('CREATE TABLE IF NOT EXISTS carry_events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, body TEXT NOT NULL)')
        row = self.conn.execute('SELECT body FROM carry_state WHERE id=1').fetchone()
        self.s = json.loads(row[0]) if row else dict(settings=asdict(cfg), balance=cfg.initial_balance,
            positions={}, trades=[], completed_contracts=[], day=None, day_equity=cfg.initial_balance,
            day_halted=False, next_id=1, last_mark=None)
        if self.s['settings'] != asdict(cfg):
            self.conn.close()
            raise ValueError('Saved carry settings differ; use the original config or a new account')

    def commit(self, now, events):
        events = [dict(event, ts=now, mode='CARRY_PAPER') for event in events]
        with self.conn:
            self.conn.execute('INSERT OR REPLACE INTO carry_state VALUES(1,?)',
                              (json.dumps(self.s, allow_nan=False),))
            self.conn.executemany('INSERT INTO carry_events(ts,body) VALUES(?,?)',
                                  [(now, json.dumps(e, allow_nan=False)) for e in events])
        for event in events:
            self.emit(event)

    def close(self):
        self.conn.close()

    def valuation(self, position, contract, now):
        qty, cfg = position['qty'], self.cfg
        spot_exit = contract['spot']['bid']*(1-cfg.slippage)
        future_exit = contract['future']['ask']*(1+cfg.slippage)
        gross = qty*(spot_exit-position['spot_entry']+position['future_entry']-future_exit)
        exit_fee = qty*(spot_exit*cfg.spot_fee+future_exit*cfg.future_fee)
        capital_charge = position['capital']*cfg.capital_charge_apr*max(0, now-position['opened'])/(365*86400)
        cash_net = gross-exit_fee-position['entry_cost']
        buffer = position['collateral']+qty*(position['future_entry']-contract['mark']['price'])
        buffer -= position['future_entry_fee']+qty*future_exit*cfg.future_fee
        return dict(spot_exit=spot_exit, future_exit=future_exit, gross=gross, exit_fee=exit_fee,
                    cash_net=cash_net, capital_charge=capital_charge, economic_net=cash_net-capital_charge,
                    collateral_buffer=buffer)

    def status(self, contracts, now):
        marked, missing = [], []
        for identity, p in self.s['positions'].items():
            c = contracts.get(identity)
            reasons = ['missing_contract'] if not c else quote_errors(c, now, self.cfg)
            if now >= p['expiry']:
                reasons.append('settlement_required')
            if reasons:
                missing.append(dict(contract=identity, reasons=reasons)); continue
            marked.append(dict(contract=identity, **self.valuation(p, c, now)))
        # Entry fees/dust were already subtracted from balance.
        equity = None if missing else self.s['balance']+sum(r['gross']-r['exit_fee'] for r in marked)
        return dict(event='STATUS', balance=self.s['balance'], cash_equity=equity,
                    capital_reserved=sum(p['capital'] for p in self.s['positions'].values()),
                    positions=len(self.s['positions']), trades=len(self.s['trades']),
                    realized_cash_pnl=sum(t['cash_net'] for t in self.s['trades']),
                    realized_economic_pnl=sum(t['economic_net'] for t in self.s['trades']),
                    marked=marked, unpriced=missing, day_halted=self.s['day_halted'])

    def _exit(self, identity, contract, now, reason):
        p = self.s['positions'][identity]
        p['pending_exit'] = reason
        qty = p['qty']
        if (qty > contract['spot']['bid_size']*self.cfg.depth_fraction
                or qty > contract['future']['ask_size']*self.cfg.depth_fraction):
            return dict(event='DATA_HALT', contract=identity, reasons=['insufficient_exit_depth'], pending_exit=reason)
        for leg, price in (('spot', contract['spot']['bid']), ('future', contract['future']['ask'])):
            limits = contract[leg+'_limits']
            if (qty < limits['min_qty'] or qty > limits['max_qty']
                    or (leg == 'spot' and qty*price < limits['min_notional'])
                    or (limits.get('max_notional') and qty*price > limits['max_notional'])):
                return dict(event='DATA_HALT', contract=identity, reasons=[leg+'_exit_filter'], pending_exit=reason)
        value = self.valuation(p, contract, now)
        self.s['balance'] += value['gross']-value['exit_fee']
        trade = dict(p, **value, exit_time=now, reason=reason,
                     orders=[dict(market='spot', side='SELL', qty=qty, price=value['spot_exit']),
                             dict(market='future', side='BUY', qty=qty, price=value['future_exit'])])
        trade['issues'] = [label for label, applies in (
            ('costs_erased_gross_profit', value['gross'] > 0 and value['cash_net'] <= 0),
            ('capital_cost_erased_profit', value['cash_net'] > 0 and value['economic_net'] <= 0),
            ('basis_widened_or_execution_loss', value['gross'] < 0),
            ('collateral_buffer_exit', reason == 'COLLATERAL_BUFFER')) if applies]
        self.s['trades'].append(trade)
        self.s['completed_contracts'].append(identity)
        del self.s['positions'][identity]
        return dict(event='EXIT', contract=identity, trade=trade)

    def cycle(self, snapshot, now, allow_entries=True, force_close=False):
        previous = deepcopy(self.s)
        try:
            return self._cycle(snapshot, now, allow_entries, force_close)
        except BaseException:
            # Restore committed state if validation/serialization fails mid-cycle.
            # If an output callback failed after commit, the saved state wins.
            row = self.conn.execute('SELECT body FROM carry_state WHERE id=1').fetchone()
            self.s = json.loads(row[0]) if row else previous
            raise

    def _cycle(self, snapshot, now, allow_entries, force_close):
        cfg = self.cfg
        contracts = {c['id']: c for c in snapshot['contracts']}
        events = []
        initial = self.status(contracts, now)
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        if initial['cash_equity'] is not None:
            # Never erase an overnight/restart loss by resetting the daily anchor first.
            hit = initial['cash_equity'] <= self.s['day_equity']*(1-cfg.daily_loss_fraction)
            if day != self.s['day']:
                self.s.update(day=day, day_equity=initial['cash_equity'], day_halted=hit)
            elif hit:
                self.s['day_halted'] = True
        for identity, p in list(self.s['positions'].items()):
            c = contracts.get(identity)
            if force_close or self.s['day_halted']:
                p['pending_exit'] = 'REQUESTED_CLOSE' if force_close else 'DAILY_LOSS_LIMIT'
            if now >= p['expiry']:
                events.append(dict(event='SETTLEMENT_REQUIRED', contract=identity,
                                   reason='No simulated expiry fill; manual reconciliation required'))
                continue
            errors = ['missing_contract'] if not c else quote_errors(c, now, cfg)
            if errors:
                events.append(dict(event='DATA_HALT', contract=identity, reasons=errors)); continue
            value = self.valuation(p, c, now)
            reason = p.get('pending_exit')
            if self.s['day_halted']:
                reason = 'DAILY_LOSS_LIMIT'
            elif value['collateral_buffer'] <= p['collateral']*cfg.collateral_buffer_fraction:
                reason = 'COLLATERAL_BUFFER'
            elif value['economic_net'] <= -p['stop_usdt']:
                reason = 'LOSS_LIMIT'
            elif now >= p['planned_exit']:
                reason = 'PRE_EXPIRY'
            elif value['economic_net'] >= p['take_profit_usdt']:
                reason = 'BASIS_CAPTURED'
            if reason:
                event = self._exit(identity, c, now, reason)
                events.append(event)
        current = self.status(contracts, now)
        if (allow_entries and not force_close and not self.s['day_halted']
                and current['cash_equity'] is not None and not snapshot.get('errors')):
            candidates = []
            equity = current['cash_equity']
            for c in contracts.values():
                if c['id'] in self.s['positions'] or c['id'] in self.s['completed_contracts']:
                    continue
                candidates.append((evaluate(c, now, cfg, max(0, equity*cfg.basket_fraction)), c))
            candidates.sort(key=lambda pair: pair[0].get('base', {}).get('estimated_apr', -1e99), reverse=True)
            for _, c in candidates:
                equity = self.status(contracts, now)['cash_equity']
                estimate = evaluate(c, now, cfg, max(0, equity*(1-cfg.stop_fraction)*cfg.basket_fraction))
                reasons = list(estimate['reasons'])
                if len(self.s['positions']) >= cfg.max_positions:
                    reasons.append('position_limit')
                if any(p['base_asset'] == c['base'] for p in self.s['positions'].values()):
                    reasons.append('same_asset_already_held')
                base = estimate.get('base')
                reserved = sum(p['capital'] for p in self.s['positions'].values())
                if base:
                    immediate_net = self.valuation(dict(base, opened=now), c, now)['cash_net']
                    post_equity = equity+immediate_net
                    risk = min(equity*cfg.stop_fraction, base['estimated_net']*cfg.take_profit_fraction)
                    if reserved+base['capital'] > post_equity*cfg.portfolio_fraction:
                        reasons.append('portfolio_capital_limit')
                    if base['capital'] > post_equity*cfg.basket_fraction:
                        reasons.append('basket_capital_limit')
                    if max(0, -immediate_net) >= risk:
                        reasons.append('execution_cost_exceeds_stop_budget')
                if reasons:
                    events.append(dict(event='REJECT', contract=c['id'], reasons=sorted(set(reasons)), estimate=estimate)); continue
                position = dict(base, id=self.s['next_id'], contract=c['id'], base_asset=c['base'],
                                expiry=c['expiry'], planned_exit=estimate['planned_exit'], opened=now,
                                stop_usdt=risk, take_profit_usdt=risk)
                self.s['next_id'] += 1
                self.s['positions'][c['id']] = position
                self.s['balance'] -= base['entry_cost']
                events.append(dict(event='ENTRY', contract=c['id'], position=position,
                    orders=[dict(market='spot', side='BUY', gross_qty=base['gross_spot_qty'], net_hedge_qty=base['qty'],
                                 fee_asset=c['base'], price=base['spot_entry']),
                            dict(market='future', side='SELL', qty=base['qty'], price=base['future_entry'])],
                    assumption='Both legs fill atomically at sampled quotes plus slippage; dust written off.'))
        status = self.status(contracts, now)
        if status['cash_equity'] is not None:
            self.s['last_mark'] = dict(ts=now, equity=status['cash_equity'])
        events.append(status)
        events.insert(0, dict(event='OBSERVATION', snapshot=snapshot))
        self.commit(now, events)
        return events
