"""Forward PAPER run of the frozen funding-carry rule (long spot + short perp) on live public Binance data.

Decision logic is CarryBook.on_settlement, shared with the historical check in `verify()`.
Each cycle (every 5 min) pulls newly settled funding for every coin and processes settlement timestamps in
order, exactly like carry_study.run. Live-only measurements: spot/perp quotes at entry/exit and mark-to-market
of the hedge (basis drift), which the backtest assumed to be zero. No keys, no orders.

  cd playground
  ..\\.venv\\Scripts\\python.exe -m daytrade.carry_live start --run artifacts\\daytrade\\live_carry
  ..\\.venv\\Scripts\\python.exe -m daytrade.carry_live report --run artifacts\\daytrade\\live_carry
  ..\\.venv\\Scripts\\python.exe -m daytrade.carry_live verify
(stop: create STOP in the run folder; restart resumes from state.json)
"""
import argparse
import json
import math
import os
import time
import urllib.request

import numpy as np
import pandas as pd

from . import carry_study as CS

FAPI, SAPI = 'https://fapi.binance.com', 'https://api.binance.com'
RULE = dict(n=3, enter=0.0003, exit_=0.00005, k=1)        # frozen carry_study selection
FEE = 'bnb'
START_INR = 10_000


def _get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(2 * (i + 1))
    raise RuntimeError(url)


class CarryBook:
    """Paper account. Same arithmetic as carry_study.run."""

    def __init__(self, equity_usd, n, enter, exit_, k, fee=FEE):
        self.eq, self.n, self.enter, self.exit_, self.k, self.fee = equity_usd, n, enter, exit_, k, fee
        self.held = {}                  # coin -> dict(notional, ...)
        self.income = self.costs = 0.0
        self.entries = 0

    def on_settlement(self, rates, signals, quote=None):
        """rates: {coin: settled rate at this ts}; signals: {coin: trailing mean (carried forward)}.
        Returns list of (action, coin, position)."""
        acts = []
        for c, h in self.held.items():
            v = rates.get(c)
            if v is not None and not np.isnan(v):
                self.eq += v * h['notional']; self.income += v * h['notional']
                h['funding'] = h.get('funding', 0) + v * h['notional']
        for c in [c for c in self.held if not (signals.get(c, np.nan) > self.exit_)]:
            h = self.held.pop(c); cost = CS.FEES[self.fee] / 2 * h['notional']
            self.eq -= cost; self.costs += cost; acts.append(('EXIT', c, h))
        if len(self.held) < self.k:
            cand = sorted(((v, c) for c, v in signals.items() if v > self.enter and c not in self.held), reverse=True)
            for v, c in cand[:self.k - len(self.held)]:
                nt = self.eq / (2 * self.k)
                if nt < CS.MIN_NOTIONAL.get(c, 5.0):
                    continue
                cost = CS.FEES[self.fee] / 2 * nt
                self.eq -= cost; self.costs += cost; self.entries += 1
                self.held[c] = dict(notional=nt, signal=v, **(quote(c) if quote else {}))
                acts.append(('ENTRY', c, self.held[c]))
        return acts

    def state(self):
        return dict(eq=self.eq, held=self.held, income=self.income, costs=self.costs, entries=self.entries)

    def load(self, s):
        self.eq, self.held, self.income, self.costs, self.entries = s['eq'], s['held'], s['income'], s['costs'], s['entries']


def verify():
    """Replay history through CarryBook and compare with carry_study.run (must match exactly)."""
    rates = CS.load_rates(); sig = CS.signals(rates, RULE['n'])
    for per, (a, b) in CS.PERIODS.items():
        ref = CS.run(rates, sig, a, b, fee=FEE, start_inr=START_INR, **RULE)
        book = CarryBook(START_INR / CS.USDINR, **RULE)
        idx = rates.index[(rates.index >= pd.Timestamp(a, tz='UTC')) & (rates.index < pd.Timestamp(b, tz='UTC'))]
        for ts in idx:
            book.on_settlement(rates.loc[ts].dropna().to_dict(), sig.loc[ts].dropna().to_dict())
        for h in book.held.values():
            book.eq -= CS.FEES[FEE] / 2 * h['notional']
        got = book.eq / (START_INR / CS.USDINR) - 1
        print(f"{per}: backtest {ref['ret'] * 100:+.4f}%  live-logic {got * 100:+.4f}%  entries {ref['entries']}/{book.entries}  "
              f"match={abs(got - ref['ret']) < 1e-9}")


# --------------------------------------------------------------------------- live run
def spot_symbols():
    return {s['symbol'] for s in _get(f'{SAPI}/api/v3/exchangeInfo?permissions=SPOT')['symbols'] if s['status'] == 'TRADING'}


def quote(c):
    f = _get(f'{FAPI}/fapi/v1/ticker/bookTicker?symbol={c}'); s = _get(f'{SAPI}/api/v3/ticker/bookTicker?symbol={c}')
    return dict(spot_ask=float(s['askPrice']), spot_bid=float(s['bidPrice']),
                perp_bid=float(f['bidPrice']), perp_ask=float(f['askPrice']), at=pd.Timestamp.now(tz='UTC').isoformat())


class Live:
    def __init__(self, root):
        self.root = root; os.makedirs(root, exist_ok=True)
        p = os.path.join(root, 'protocol.json')
        if not os.path.exists(p):
            coins = json.load(open(CS.R.EXTENDED)) + CS.data.UNIVERSE
            spot = spot_symbols()
            json.dump(dict(kind='FORWARD_PAPER_CARRY', rule=RULE, fee=FEE, start_inr=START_INR, usdinr=CS.USDINR,
                           start=pd.Timestamp.now(tz='UTC').isoformat(),
                           coins=[c for c in coins if c in spot and c.isascii()],
                           excluded_no_spot=[c for c in coins if c not in spot]), open(p, 'w'), indent=1)
        self.protocol = json.load(open(p))
        assert self.protocol['rule'] == RULE, 'run folder was created with a different rule'
        self.coins = self.protocol['coins']
        self.start = pd.Timestamp(self.protocol['start'])
        self.book = CarryBook(START_INR / CS.USDINR, **RULE)
        self.last_ts = None
        sp = os.path.join(root, 'state.json')
        if os.path.exists(sp):
            s = json.load(open(sp)); self.book.load(s['book'])
            self.last_ts = None if s['last_ts'] in (None, 'None') else pd.Timestamp(s['last_ts'])

    def event(self, kind, **kw):
        rec = dict(event=kind, at=pd.Timestamp.now(tz='UTC').isoformat(), **kw)
        with open(os.path.join(self.root, 'events.jsonl'), 'a') as fh:
            fh.write(json.dumps(rec, default=str) + '\n')
        print(json.dumps(rec, default=str), flush=True)

    def cycle(self):
        cols = {}
        for c in self.coins:
            rows = _get(f'{FAPI}/fapi/v1/fundingRate?symbol={c}&limit=50') or []
            if rows:
                cols[c] = pd.Series([float(x['fundingRate']) for x in rows],
                                    index=pd.to_datetime([x['fundingTime'] // 60000 * 60000 for x in rows], unit='ms', utc=True))
        rates = pd.DataFrame(cols).sort_index()
        sig = CS.signals(rates, RULE['n'])
        floor = self.last_ts if self.last_ts is not None else self.start
        for ts in rates.index[rates.index > floor]:
            for a, c, h in self.book.on_settlement(rates.loc[ts].dropna().to_dict(), sig.loc[ts].dropna().to_dict(), quote):
                if a == 'EXIT':
                    q = quote(c)
                    basis = (h['notional'] * ((q['spot_bid'] / h['spot_ask'] - 1) - (q['perp_ask'] / h['perp_bid'] - 1))
                             if 'spot_ask' in h else None)
                    self.event('EXIT', coin=c, settlement=ts, funding_earned_usd=round(h.get('funding', 0), 4),
                               hedge_basis_pnl_usd=None if basis is None else round(basis, 4), equity_usd=round(self.book.eq, 4))
                else:
                    self.event('ENTRY', coin=c, settlement=ts, notional_usd=round(h['notional'], 2),
                               trailing_funding=h['signal'], quotes={k: v for k, v in h.items() if k not in ('notional', 'signal')})
            self.last_ts = ts
        self._status(sig)

    def _status(self, sig):
        mtm = {}
        for c, h in self.book.held.items():
            if 'spot_ask' in h:
                q = quote(c)
                mtm[c] = h['notional'] * ((q['spot_bid'] / h['spot_ask'] - 1) - (q['perp_ask'] / h['perp_bid'] - 1))
        base = START_INR / CS.USDINR
        now = pd.Timestamp.now(tz='UTC')
        st = dict(updated=now.isoformat(), started=self.start.isoformat(),
                  days=round((now - self.start).total_seconds() / 86400, 2), coins_watched=len(self.coins),
                  equity_inr=round(self.book.eq * CS.USDINR, 2), return_pct=round((self.book.eq / base - 1) * 100, 4),
                  funding_earned_inr=round(self.book.income * CS.USDINR, 2), fees_paid_inr=round(self.book.costs * CS.USDINR, 2),
                  entries=self.book.entries,
                  holding={c: dict(notional_usd=round(h['notional'], 2), funding_so_far_usd=round(h.get('funding', 0), 4),
                                   hedge_basis_mtm_usd=round(mtm.get(c, 0), 4)) for c, h in self.book.held.items()},
                  top_signals_per_payment=sig.iloc[-1].dropna().sort_values(ascending=False).head(5).round(6).to_dict(),
                  entry_threshold=RULE['enter'], last_settlement=str(self.last_ts))
        json.dump(st, open(os.path.join(self.root, 'status.json'), 'w'), indent=1, default=str)
        json.dump(dict(book=self.book.state(), last_ts=str(self.last_ts) if self.last_ts is not None else None),
                  open(os.path.join(self.root, 'state.json'), 'w'), indent=1, default=str)

    def loop(self):
        stop = os.path.join(self.root, 'STOP')
        while not os.path.exists(stop):
            try:
                self.cycle()
                print(f'{pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} cycle ok, equity Rs{self.book.eq * CS.USDINR:,.2f}, '
                      f'holding {list(self.book.held)}', flush=True)
            except Exception as e:
                print(f'cycle error: {e!r}', flush=True)
            nxt = (math.floor(time.time() / 300) + 1) * 300 + 40
            while time.time() < nxt and not os.path.exists(stop):
                time.sleep(5)
        print('stopping', flush=True)


def single_instance(root):
    """Hold an OS lock on <root>/run.lock for the life of the process; exit if another runner holds it."""
    import sys
    fh = open(os.path.join(root, 'run.lock'), 'a+')
    try:
        if os.name == 'nt':
            import msvcrt; msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl; fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f'Another runner is already using {root}; not starting a second copy.')
    return fh


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('start'); s.add_argument('--run', required=True); s.add_argument('--once', action='store_true')
    r = sub.add_parser('report'); r.add_argument('--run', required=True)
    sub.add_parser('verify')
    a = ap.parse_args()
    if a.cmd == 'verify':
        verify()
    elif a.cmd == 'report':
        print(open(os.path.join(a.run, 'status.json')).read())
    else:
        os.makedirs(a.run, exist_ok=True)
        _lock = single_instance(a.run)
        lv = Live(a.run)
        print(f"{len(lv.coins)} coins with spot+perp, start {lv.start}", flush=True)
        lv.cycle() if a.once else lv.loop()


if __name__ == '__main__':
    main()
