"""Forward PAPER run of a frozen strategy on live public Binance data, many symbols in parallel.

No API keys, no orders. Every 5 minutes (after each 5m bar closes) it:
  1. pulls new closed 5m klines, settled funding and hourly OI / long-short ratios per symbol;
  2. regenerates signals with the SAME strategy code used in the backtest;
  3. freezes each NEW signal the first time it is seen; it may only fill at the open of the
     first 5m bar that starts after that moment (no retroactive fills from late data);
  4. replays frozen orders through backtest.simulate (same costs, stops, trail, max hold);
  5. journals SIGNAL / ENTRY / EXIT / LATE_SIGNAL events and writes status + report files.

  python -m daytrade.live start --run artifacts/daytrade/live_squeeze_oi --top 40
  python -m daytrade.live report --run artifacts/daytrade/live_squeeze_oi
  (stop: create a file named STOP in the run directory, or Ctrl+C; restart resumes)
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

from . import data, research, strategies as st  # research also registers the 'lab' family
from . import ml_live  # noqa: F401  (registers the 'ml_rf' family)
from . import ml_live_lgbm  # noqa: F401  (registers the 'ml_lgbm' family)
from .backtest import costs_for, simulate

API = 'https://fapi.binance.com'
STRATEGY = 'squeeze_oi'
PARAMS = {"brk": 6, "fthr": 0.0, "hold_h": 48, "init_atr": 2.0, "ls_q": 0.4, "oi_min": 0.0,
          "oi_win": 24, "sides": "long", "trail_atr": 5.0}          # frozen _r3b selection
BOOT_DAYS = 45                 # ML features need >= 200 hourly bars plus 7 days of daily returns
MAX_SIGNAL_AGE = pd.Timedelta(minutes=15)    # older first-seen signals are logged, not traded
EXCLUDE_BASES = {'USDC', 'FDUSD', 'TUSD', 'BUSD', 'USDP', 'DAI', 'EUR', 'XAU', 'XAG', 'PAXG'}
_lock = threading.Lock()
_weight = {'used': 0}


def _get(path, **params):
    q = '&'.join(f'{k}={v}' for k, v in params.items())
    url = f'{API}{path}?{q}' if q else f'{API}{path}'
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                w = r.headers.get('X-MBX-USED-WEIGHT-1M')
                if w:
                    with _lock:
                        _weight['used'] = int(w)
                if _weight['used'] > 1800:
                    time.sleep(15)
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                time.sleep(30 * (attempt + 1)); continue
            if e.code == 400:
                return None
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'request failed: {url}')


# --------------------------------------------------------------------------- universe
def universe(top):
    info = _get('/fapi/v1/exchangeInfo')
    now = time.time() * 1000
    ok = {s['symbol'] for s in info['symbols']
          if s['contractType'] == 'PERPETUAL' and s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT'
          and s['symbol'].isascii() and s['baseAsset'] not in EXCLUDE_BASES and now - s.get('onboardDate', now) > 45 * 86400e3}
    tick = sorted((t for t in _get('/fapi/v1/ticker/24hr') if t['symbol'] in ok),
                  key=lambda t: -float(t['quoteVolume']))
    picked = [t['symbol'] for t in tick[:top]]
    return list(dict.fromkeys(data.UNIVERSE + picked))      # core 10 first, then extras


# --------------------------------------------------------------------------- per-symbol store
class Store:
    """Closed 5m klines, settled funding and hourly metrics, persisted per symbol."""

    def __init__(self, root, symbol):
        self.dir = os.path.join(root, 'data'); os.makedirs(self.dir, exist_ok=True)
        self.symbol = symbol
        self.k = self._load('k'); self.f = self._load('f'); self.m = self._load('m')

    def _path(self, kind):
        return os.path.join(self.dir, f'{self.symbol}_{kind}.pkl')

    def _load(self, kind):
        p = self._path(kind)
        return pd.read_pickle(p) if os.path.exists(p) else None

    def save(self):
        for kind in ('k', 'f', 'm'):
            v = getattr(self, kind)
            if v is not None:
                v.to_pickle(self._path(kind))

    @staticmethod
    def _merge(old, new):
        if old is None or not len(old):
            return new
        out = pd.concat([old, new])
        return out[~out.index.duplicated(keep='last')].sort_index()

    def update(self, now_ms):
        # klines: only bars whose close_time has passed
        start = int(self.k.index[-1].value // 10**6) if self.k is not None and len(self.k) else int(now_ms - BOOT_DAYS * 86400e3)
        rows = []
        while True:
            batch = _get('/fapi/v1/klines', symbol=self.symbol, interval='5m', startTime=start, limit=1500)
            if not batch:
                break
            rows += [b for b in batch if b[6] < now_ms]
            if len(batch) < 1500:
                break
            start = batch[-1][0] + 1
        if rows:
            k = pd.DataFrame([(r[0], *map(float, (r[1], r[2], r[3], r[4], r[5], r[7], r[9]))) for r in rows],
                             columns=['ts', 'open', 'high', 'low', 'close', 'volume', 'quote_volume', 'taker_buy_volume'])
            k.index = pd.DatetimeIndex(pd.to_datetime(k.pop('ts'), unit='ms', utc=True)).as_unit('ns')
            self.k = self._merge(self.k, k)
        # funding: settled rates
        fr = _get('/fapi/v1/fundingRate', symbol=self.symbol, limit=1000 if self.f is None else 10) or []
        if fr:
            f = pd.Series([float(x['fundingRate']) for x in fr],
                          index=pd.to_datetime([x['fundingTime'] // 60000 * 60000 for x in fr], unit='ms', utc=True).as_unit('ns'),
                          name='funding')
            self.f = self._merge(self.f, f)
        # metrics: hourly snapshot at T relabelled T-5m, matching the archive's xx:55 sample
        n = 500 if self.m is None else 6
        oi = self._hist('/futures/data/openInterestHist', n)
        ls = self._hist('/futures/data/globalLongShortAccountRatio', n)
        if oi and ls:
            o = pd.Series({x['timestamp']: float(x['sumOpenInterest']) for x in oi})
            l = pd.Series({x['timestamp']: float(x['longShortRatio']) for x in ls})
            m = pd.DataFrame({'oi': o, 'ls_acct': l}).dropna()
            m.index = (pd.to_datetime(m.index, unit='ms', utc=True) - pd.Timedelta(minutes=5)).as_unit('ns')
            self.m = self._merge(self.m, m)

    def _hist(self, path, n):
        out = _get(path, symbol=self.symbol, period='1h', limit=min(n, 500)) or []
        if n >= 500 and out:      # second page back to the 30-day retention limit
            older = _get(path, symbol=self.symbol, period='1h', limit=500, endTime=out[0]['timestamp'] - 1) or []
            out = older + out
        return out

    def ctx(self, now=None):
        return live_ctx(self.symbol, self.k, self.f, self.m, now)


def live_ctx(symbol, k, f, m, now=None):
    """Strategy context from closed bars. With `now`, append an inert placeholder for the 5m bar in
    progress so an order decided at the latest close can be positioned on it (strategies drop orders
    whose fill bar does not exist). One 5m bar never completes a higher-timeframe bar."""
    if now is not None and len(k) and k.index[-1] + pd.Timedelta(minutes=5) <= now.floor('5min'):
        nxt = k.index[-1] + pd.Timedelta(minutes=5)
        c = k['close'].iloc[-1]
        ph = pd.DataFrame({'open': c, 'high': c, 'low': c, 'close': c, 'volume': 0.0, 'quote_volume': 0.0,
                           'taker_buy_volume': 0.0}, index=pd.DatetimeIndex([nxt]).as_unit(k.index.unit))
        k = pd.concat([k, ph])
    c = st.Ctx(symbol, k)
    c.funding = f if f is not None else pd.Series(dtype=float)
    c.metrics = m
    return c


# --------------------------------------------------------------------------- run state
class Run:
    def __init__(self, root, top=40, strategy=STRATEGY, params=None, symbols=None):
        params = PARAMS if params is None else params
        self.root = root; os.makedirs(root, exist_ok=True)
        p = os.path.join(root, 'protocol.json')
        if os.path.exists(p):
            self.protocol = json.load(open(p))
            if self.protocol['params'] != params or self.protocol['strategy'] != strategy:
                raise SystemExit('Run directory was created with different frozen settings.')
        else:
            self.protocol = dict(kind='FORWARD_PAPER_REPLAY', strategy=strategy, params=params,
                                 start=pd.Timestamp.now(tz='UTC').isoformat(), symbols=symbols or universe(top),
                                 core=data.UNIVERSE, max_cost_r=research.MAX_COST_R,
                                 portfolio=dict(risk=research.RISK, max_pos=research.MAX_POS,
                                                lev_cap=research.LEV_CAP, gross_cap=research.GROSS_CAP),
                                 fees='Binance USD-M regular: maker 0.02%, taker 0.05%; slippage per backtest',
                                 note='Paper only. Signals freeze when first seen; fill at the open of the 5m bar in progress when seen; live ask recorded.')
            json.dump(self.protocol, open(p, 'w'), indent=1)
        self.start = pd.Timestamp(self.protocol['start'])
        self.strategy, self.params = self.protocol['strategy'], self.protocol['params']
        self.symbols = [s for s in self.protocol['symbols'] if s.isascii()]   # URL-safe symbols only
        self.orders_path = os.path.join(root, 'orders.json')
        self.orders = json.load(open(self.orders_path)) if os.path.exists(self.orders_path) else {}
        self.events_path = os.path.join(root, 'events.jsonl')
        self.known = self._known_events()
        self.stores = {s: Store(root, s) for s in self.symbols}

    def _known_events(self):
        k = set()
        if os.path.exists(self.events_path):
            for line in open(self.events_path):
                e = json.loads(line); k.add((e['event'], e['key']))
        return k

    def event(self, kind, key, **kw):
        if (kind, key) in self.known:
            return
        self.known.add((kind, key))
        rec = dict(event=kind, key=key, at=pd.Timestamp.now(tz='UTC').isoformat(), **kw)
        with open(self.events_path, 'a') as fh:
            fh.write(json.dumps(rec, default=str) + '\n')
        print(json.dumps(rec, default=str), flush=True)

    # ------------------------------------------------------------------ one cycle
    def cycle(self):
        now = pd.Timestamp.now(tz='UTC')
        now_ms = now.value // 10**6
        errors = {}

        def upd(s):
            try:
                self.stores[s].update(now_ms); return s, None
            except Exception as e:      # one bad symbol must not stop the run
                return s, repr(e)
        with ThreadPoolExecutor(6) as pool:
            for s, err in pool.map(upd, self.symbols):
                if err:
                    errors[s] = err
        trades = []
        for s in self.symbols:
            store = self.stores[s]
            if store.k is None or len(store.k) < 3000 or (self.strategy.startswith('squeeze') and store.m is None):
                continue
            c = store.ctx(now)
            c.peers = {sym: self.stores[sym].k for sym in self.stores}
            self._freeze_new(c, now)
            tr = self._replay(store.ctx())
            if tr is not None and len(tr):
                trades.append(tr)
            store.save()
        tr = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
        for r in tr.itertuples():
            key = f'{r.symbol}|{r.entry_ts}'
            self.event('ENTRY', key, symbol=r.symbol, side=r.side, entry=r.entry, stop=r.stop,
                       entry_time=pd.Timestamp(r.entry_ts, unit='ms', tz='UTC'), risk_pct=round(r.risk_frac * 100, 3))
            if not r.open:
                self.event('EXIT', key, symbol=r.symbol, exit=r.exit, reason=r.reason, net_r=round(r.net_r, 3),
                           net_pct=round(r.net * 100, 3), exit_time=pd.Timestamp(r.exit_ts, unit='ms', tz='UTC'))
        json.dump(self.orders, open(self.orders_path, 'w'), indent=1)
        self._write_status(now, tr, errors)
        return tr

    def _freeze_new(self, ctx, now, quote=True):
        orders = st.FAMILIES[self.strategy](ctx, **self.params)
        if orders is None or orders.empty:
            return
        idx = ctx.idx
        for o in orders.itertuples():
            if o.t < 1 or o.t > len(idx):
                continue
            # signal decided at close of bar t-1
            decided = idx[o.t - 1] + pd.Timedelta(minutes=5)
            if decided < self.start:
                continue
            key = f'{ctx.symbol}|{decided.isoformat()}|{o.side}'
            if key in self.orders:
                continue
            if now - decided > MAX_SIGNAL_AGE:
                self.event('LATE_SIGNAL', key, symbol=ctx.symbol, decided=decided, first_seen=now,
                           note='signal appeared late (data delay/revision); not traded')
                self.orders[key] = dict(skipped=True)
                continue
            # fill at the open of the 5m bar in progress when first seen (= backtest when seen
            # within the bar it was decided; ~25 s optimistic). The live ask is recorded to measure drift.
            fill_from = now.floor('5min')
            ask = None
            try:
                if not quote:
                    raise RuntimeError('quotes disabled')
                ask = float(_get('/fapi/v1/ticker/bookTicker', symbol=ctx.symbol)['askPrice'])
            except Exception:
                pass
            self.orders[key] = dict(symbol=ctx.symbol, side=int(o.side), stop=float(o.stop), trail=float(o.trail),
                                    target=float(o.target),
                                    max_bars=int(o.max_bars), decided=decided.isoformat(),
                                    first_seen=now.isoformat(), fill_from=fill_from.isoformat(), live_ask=ask)
            self.event('SIGNAL', key, symbol=ctx.symbol, side=int(o.side), decided=decided, first_seen=now,
                       fill_from=fill_from, stop=float(o.stop), live_ask=ask)

    def _replay(self, ctx):
        mine = [v for v in self.orders.values() if v.get('symbol') == ctx.symbol and not v.get('skipped')]
        if not mine:
            return None
        idx = ctx.idx
        t = idx.searchsorted([pd.Timestamp(v['fill_from']) for v in mine])
        o = st._orders(t, np.array([v['side'] for v in mine]), 'market', np.nan, 1,
                       np.array([v['stop'] for v in mine]), np.array([v.get('target', np.nan) for v in mine]),
                       np.array([v['max_bars'] for v in mine]),
                       np.nan, np.array([v['trail'] for v in mine]))
        tr = simulate(ctx.df, ctx.funding, o, costs_for(ctx.symbol), ctx.symbol, self.strategy, research.MAX_COST_R)
        if tr.empty:
            return tr
        last_ts = idx[-1].value // 10**6
        mb = {idx[p].value // 10**6: v['max_bars'] for p, v in zip(t, mine) if p < len(idx)}
        tr['open'] = [bool(r.reason == 'time' and r.exit_ts == last_ts and r.bars - 1 < mb.get(r.entry_ts, 10**9))
                      for r in tr.itertuples()]
        tr['core'] = ctx.symbol in data.UNIVERSE
        return tr

    def _write_status(self, now, tr, errors):
        closed = tr[~tr['open']] if len(tr) else tr
        opened = tr[tr['open']] if len(tr) else tr
        st_ = dict(updated=now.isoformat(), started=self.start.isoformat(),
                   hours_running=round((now - self.start).total_seconds() / 3600, 2),
                   symbols=len(self.symbols), symbols_with_data=sum(1 for s in self.stores.values() if s.m is not None),
                   errors=errors, signals=sum(1 for v in self.orders.values() if not v.get('skipped')),
                   late_signals=sum(1 for v in self.orders.values() if v.get('skipped')),
                   open_positions=[dict(symbol=r.symbol, entry=r.entry, entry_time=str(pd.Timestamp(r.entry_ts, unit='ms', tz='UTC')),
                                        unrealized_r=round(r.net_r, 3)) for r in opened.itertuples()] if len(opened) else [],
                   closed=summarize(closed))
        if len(closed):
            st_['closed_core10'] = summarize(closed[closed['core']])
            st_['closed_extended'] = summarize(closed[~closed['core']])
            closed.to_csv(os.path.join(self.root, 'closed_trades.csv'), index=False)
        json.dump(st_, open(os.path.join(self.root, 'status.json'), 'w'), indent=1, default=str)

    def loop(self, hours=None):
        stop_file = os.path.join(self.root, 'STOP')
        end = time.time() + hours * 3600 if hours else None
        while True:
            t0 = time.time()
            try:
                tr = self.cycle()
                print(f'{pd.Timestamp.now(tz="UTC"):%Y-%m-%d %H:%M} cycle ok, trades={len(tr)} '
                      f'weight={_weight["used"]} {time.time() - t0:.0f}s', flush=True)
            except Exception as e:
                print(f'cycle error: {e!r}', flush=True)
            if os.path.exists(stop_file) or (end and time.time() > end):
                print('stopping', flush=True); return
            nxt = (math.floor(time.time() / 300) + 1) * 300 + 25     # 25 s after each 5m close
            while time.time() < nxt:
                if os.path.exists(stop_file):
                    print('stopping', flush=True); return
                time.sleep(min(5, nxt - time.time() + 0.01))


def summarize(tr):
    if tr is None or not len(tr):
        return dict(trades=0)
    r = tr['net_r']
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    taken, _ = research.portfolio(tr)
    return dict(trades=int(len(r)), win=round(float((r > 0).mean()), 3), sum_r=round(float(r.sum()), 2),
                avg_r=round(float(r.mean()), 3), pf=round(float(wins / losses), 3) if losses else None,
                portfolio_pnl_usdt=round(float(taken['pnl'].sum()), 2), portfolio_trades=int(len(taken)),
                by_symbol=r.groupby(tr['symbol']).sum().round(2).to_dict())


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
    s = sub.add_parser('start'); s.add_argument('--run', required=True); s.add_argument('--top', type=int, default=40)
    s.add_argument('--strategy', default=STRATEGY); s.add_argument('--params', help='JSON; default = frozen squeeze_oi')
    s.add_argument('--symbols', nargs='*')
    s.add_argument('--hours', type=float); s.add_argument('--once', action='store_true')
    r = sub.add_parser('report'); r.add_argument('--run', required=True)
    a = ap.parse_args()
    if a.cmd == 'report':
        print(open(os.path.join(a.run, 'status.json')).read()); return
    params = None
    if a.params:
        params = json.load(open(a.params[1:])) if a.params.startswith('@') else json.loads(a.params)
    os.makedirs(a.run, exist_ok=True)
    _lock = single_instance(a.run)
    run = Run(a.run, a.top, a.strategy, params, a.symbols)
    print(f'{len(run.symbols)} symbols, started {run.start}', flush=True)
    if a.once:
        run.cycle()
    else:
        run.loop(a.hours)


if __name__ == '__main__':
    main()
