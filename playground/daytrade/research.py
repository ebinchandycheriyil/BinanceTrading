"""Walk-forward research runner for the intraday families.

  python -m daytrade.research dev                 # full grid on development, freeze selection
  python -m daytrade.research validate            # selected configs on validation
  python -m daytrade.research holdout --confirm   # final untouched period (access is logged)
  python -m daytrade.research run FAMILY [k=v ...] [--period dev|val] [--stress 1.5]

Periods (UTC): dev 2022-01-01..2024-07-01, val 2024-07-01..2025-07-01, holdout 2025-07-01..end.
"""
from concurrent.futures import ProcessPoolExecutor
import argparse
import json
import math
import os
import time

import numpy as np
import pandas as pd

from . import data, strategies as st
from . import lab as _lab  # noqa: F401  (registers the 'lab' indicator family)
from . import trend as _trend  # noqa: F401  (registers the 'trend' family)
from .backtest import Costs, costs_for, simulate

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'daytrade')
PERIODS = {'dev': ('2022-01-01', '2024-07-01'), 'val': ('2024-07-01', '2025-07-01'),
           'holdout': ('2025-07-01', '2026-10-01'), 'all': ('2022-01-01', '2026-10-01')}
EXTENDED = os.path.join(OUT, 'extended_symbols.json')   # coins never used for selection
RISK, MAX_POS, LEV_CAP, GROSS_CAP = 0.005, 6, 2.0, 5.0
MAX_COST_R = 0.3     # skip setups whose round-trip cost exceeds 30% of the stop distance

SELECT = dict(min_trades=120, min_pf=1.15, min_t=2.0, min_years_positive=2, stress=1.5)
PASS = dict(min_trades=40, min_pf=1.10, stress=1.5)

_CTX = {}


def _ctx(sym):
    if sym not in _CTX:
        k, f = data.load(sym)
        ctx = st.Ctx(sym, k)
        ctx.funding = f
        ctx.metrics = data.load_metrics(sym)
        _CTX[sym] = (ctx, f)
    return _CTX[sym]


def _period_mask(ctx, orders, period):
    a, b = (pd.Timestamp(x, tz='UTC') for x in PERIODS[period])
    t = ctx.idx[np.minimum(orders['t'].to_numpy(), len(ctx.idx) - 1)]
    return orders[(t >= a) & (t < b)]


def trades_for(family, params, period, stress=1.0, symbols=data.UNIVERSE):
    out = []
    for sym in symbols:
        ctx, fund = _ctx(sym)
        orders = st.FAMILIES[family](ctx, **params)
        if orders is None or orders.empty:
            continue
        orders = _period_mask(ctx, orders, period)
        c = costs_for(sym)
        if stress != 1.0:
            c = c.stressed(stress)
        tr = simulate(ctx.df, fund, orders, c, sym, family, MAX_COST_R)
        if len(tr):
            out.append(tr)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# --------------------------------------------------------------------------- metrics
def portfolio(trades, start=1000.0):
    """Risk-based sizing on realised equity; concurrency and leverage caps. Returns (taken, daily pnl)."""
    if trades.empty:
        return trades, pd.Series(dtype=float)
    tr = trades.sort_values('entry_ts').reset_index(drop=True)
    entry, exit_, rf, net = (tr[c].to_numpy() for c in ('entry_ts', 'exit_ts', 'risk_frac', 'net'))
    eq = start; open_ = []          # (exit_ts, pnl, notional)
    taken, pnl, notional = [], [], []
    for i in range(len(tr)):
        still = []
        for x, p, nn in open_:
            if x <= entry[i]:
                eq += p
            else:
                still.append((x, p, nn))
        open_ = still
        if len(open_) >= MAX_POS:
            continue
        n = min(RISK * eq / rf[i], LEV_CAP * eq)
        if sum(nn for _, _, nn in open_) + n > GROSS_CAP * eq:
            continue
        taken.append(i); notional.append(n); pnl.append(n * net[i])
        open_.append((exit_[i], n * net[i], n))
    t = tr.loc[taken].copy()
    t['pnl'] = pnl; t['notional'] = notional
    day = pd.to_datetime(t['exit_ts'], unit='ms', utc=True).dt.floor('D').astype('datetime64[ns, UTC]')
    return t, t.groupby(day)['pnl'].sum()


def metrics(trades, period, start=1000.0):
    if trades.empty:
        return dict(trades=0)
    t, daily = portfolio(trades, start)
    a, b = (pd.Timestamp(x, tz='UTC') for x in PERIODS[period])
    days = pd.date_range(a, b - pd.Timedelta(days=1), freq='D').as_unit('ns')
    daily = daily.reindex(days, fill_value=0.0)
    equity = start + daily.cumsum()
    dd = float(((equity.cummax() - equity) / equity.cummax()).max())
    ret = daily / (start + daily.cumsum().shift(fill_value=0))
    sharpe = float(ret.mean() / ret.std() * math.sqrt(365)) if ret.std() > 0 else 0.0
    r = trades['net_r']
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    years = pd.to_datetime(trades['entry_ts'], unit='ms', utc=True).dt.year
    month_end = pd.concat([pd.Series([start], [a - pd.Timedelta(days=1)]), equity.resample('ME').last()])
    months = month_end.pct_change().dropna()
    return dict(trades=int(len(r)), taken=int(len(t)), win=round(float((r > 0).mean()), 3),
                avg_r=round(float(r.mean()), 4), sum_r=round(float(r.sum()), 2),
                pf=round(float(wins / losses), 3) if losses > 0 else None,
                t=round(float(r.mean() / r.std() * math.sqrt(len(r))), 2) if len(r) > 1 and r.std() > 0 else 0.0,
                ret=round(float(equity.iloc[-1] / start - 1), 4), maxdd=round(dd, 4), sharpe=round(sharpe, 2),
                pos_months=round(float((months > 0).mean()), 2),
                avg_cost_r=round(float((trades['fees'] / trades['risk_frac']).mean()), 3),
                avg_hold_h=round(float(trades['bars'].mean() / 12), 2),
                by_year={int(k): v for k, v in r.groupby(years).sum().round(2).items()},
                by_symbol=r.groupby(trades['symbol']).sum().round(2).to_dict(),
                exits=trades['reason'].value_counts().to_dict())


def evaluate(job):
    family, params, period = job[:3]
    symbols = job[3] if len(job) > 3 else data.UNIVERSE
    with_stress = job[4] if len(job) > 4 else True
    base = trades_for(family, params, period, symbols=symbols)
    m = metrics(base, period)
    if m['trades'] and with_stress:
        s = trades_for(family, params, period, stress=SELECT['stress'], symbols=symbols)
        m['stress_sum_r'] = round(float(s['net_r'].sum()), 2) if len(s) else 0.0
        m['stress_ret'] = metrics(s, period).get('ret', 0.0)
    return family, params, m


def _pool_run(jobs, workers=None):
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(workers) as pool:
        yield from pool.map(evaluate, jobs, chunksize=1)


def _qualifies(m):
    if not m.get('trades') or m['trades'] < SELECT['min_trades']:
        return False
    return ((m['pf'] or 99) >= SELECT['min_pf'] and m['t'] >= SELECT['min_t']
            and sum(v > 0 for v in m['by_year'].values()) >= SELECT['min_years_positive']
            and m.get('stress_sum_r', -1) > 0 and m['ret'] > 0 and m.get('stress_ret', -1) > 0)


def _passes(m):
    return (m.get('trades', 0) >= PASS['min_trades'] and m['sum_r'] > 0 and (m['pf'] or 99) >= PASS['min_pf']
            and m.get('stress_sum_r', -1) > 0 and m['ret'] > 0 and m.get('stress_ret', -1) > 0)


def _key(p):
    return json.dumps(p, sort_keys=True, default=str)


# --------------------------------------------------------------------------- commands
def cmd_dev(families, tag=''):
    os.makedirs(OUT, exist_ok=True)
    protocol = dict(periods=PERIODS, select=SELECT, passes=PASS, universe=data.UNIVERSE,
                    grids={f: st.GRIDS[f] for f in families}, risk=RISK, max_pos=MAX_POS,
                    lev_cap=LEV_CAP, gross_cap=GROSS_CAP, max_cost_r=MAX_COST_R, costs=Costs().__dict__,
                    written_at=time.time(), note='Written before development results were computed.')
    _dump(f'protocol{tag}.json', protocol)
    jobs = [(f, p, 'dev') for f in families for p in st.grid(f)]
    print(f'{len(jobs)} development runs', flush=True)
    rows = []
    for i, (f, p, m) in enumerate(_pool_run(jobs), 1):
        rows.append(dict(family=f, params=_key(p), **{k: v for k, v in m.items() if not isinstance(v, dict)},
                         by_year=json.dumps(m.get('by_year', {})), qualifies=_qualifies(m)))
        print(f"[{i}/{len(jobs)}] {f} {_key(p)} n={m.get('trades')} R={m.get('sum_r')} "
              f"pf={m.get('pf')} t={m.get('t')} stress={m.get('stress_sum_r')}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, f'dev_grid{tag}.csv'), index=False)
    sel = {}
    for f, g in df.groupby('family'):
        q = g[g['qualifies']].sort_values('t', ascending=False)
        best = (q if len(q) else g.sort_values('t', ascending=False)).iloc[0]
        sel[f] = dict(params=json.loads(best['params']), qualified=bool(best['qualifies']),
                      dev_t=best['t'], dev_sum_r=best['sum_r'], dev_pf=best['pf'], dev_trades=int(best['trades']),
                      grid_positive=f"{int((g['sum_r'] > 0).sum())}/{len(g)}",
                      grid_median_r=float(g['sum_r'].median()))
    _dump(f'selection_before_validation{tag}.json', sel)
    print(json.dumps(sel, indent=1, default=str))


def cmd_period(period, confirm=False, tag=''):
    sel = _load(f'selection_before_validation{tag}.json')
    if period == 'holdout':
        if not confirm:
            raise SystemExit('Holdout is single-use evidence. Re-run with --confirm.')
        val = _load(f'validation{tag}.json')
        sel = {f: s for f, s in sel.items() if val.get(f, {}).get('passes')}
        with open(os.path.join(OUT, 'holdout_access.log'), 'a') as fh:
            fh.write(f'{pd.Timestamp.now(tz="UTC").isoformat()} tag={tag!r} families={list(sel)}\n')
        if not sel:
            raise SystemExit('No family passed validation; holdout not run.')
    else:
        sel = {f: s for f, s in sel.items() if s['qualified']}
    jobs = [(f, s['params'], period) for f, s in sel.items()]
    res = {}
    for f, p, m in _pool_run(jobs):
        res[f] = dict(params=p, passes=_passes(m), **m)
        print(f"{f}: n={m.get('trades')} R={m.get('sum_r')} pf={m.get('pf')} ret={m.get('ret')} "
              f"dd={m.get('maxdd')} stress={m.get('stress_sum_r')} passes={_passes(m)}", flush=True)
    _dump(f'{"validation" if period == "val" else "holdout"}{tag}.json', res)
    return res


def cmd_oos(tag, confirm=False):
    """Frozen selection on coins never used in selection, over the whole 2022-2026 span (single use)."""
    if not confirm:
        raise SystemExit('Out-of-sample coins are single-use evidence. Re-run with --confirm.')
    sel = {f: s for f, s in _load(f'selection_before_validation{tag}.json').items() if s['qualified']}
    with open(EXTENDED) as fh:
        ext = [x for x in json.load(fh) if os.path.exists(os.path.join(data.ROOT, f'{x}_metrics.pkl'))]
    with open(os.path.join(OUT, 'holdout_access.log'), 'a') as fh:
        fh.write(f'{pd.Timestamp.now(tz="UTC").isoformat()} OOS-COINS tag={tag!r} families={list(sel)} coins={len(ext)}\n')
    res = {}
    for f, p, m in _pool_run([(f, s['params'], 'all', ext) for f, s in sel.items()]):
        res[f] = dict(params=p, passes=_passes(m), coins=ext, **m)
        print(f"{f}: n={m.get('trades')} R={m.get('sum_r')} pf={m.get('pf')} ret={m.get('ret')} "
              f"dd={m.get('maxdd')} stress={m.get('stress_ret')} passes={_passes(m)}", flush=True)
    _dump(f'oos_coins{tag}.json', res)


def cmd_run(family, params, period, stress, save):
    if period == 'holdout':
        raise SystemExit('Ad-hoc runs on the holdout are not allowed; use the holdout command.')
    tr = trades_for(family, params, period, stress)
    m = metrics(tr, period)
    print(json.dumps(m, indent=1, default=str))
    if save and len(tr):
        os.makedirs(OUT, exist_ok=True)
        tr.to_csv(os.path.join(OUT, f'trades_{family}_{period}.csv'), index=False)
    return tr, m


def _dump(name, obj):
    with open(os.path.join(OUT, name), 'w') as fh:
        json.dump(obj, fh, indent=1, default=str)


def _load(name):
    with open(os.path.join(OUT, name)) as fh:
        return json.load(fh)


def _parse_kv(items):
    out = {}
    for it in items:
        k, v = it.split('=', 1)
        for cast in (int, float):
            try:
                v = cast(v); break
            except ValueError:
                pass
        else:
            v = {'true': True, 'false': False, 'nan': float('nan')}.get(v.lower(), v)
        out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', default='', help='suffix for artifact files, e.g. _r2 for a second round')
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('dev'); d.add_argument('--families', nargs='*', default=list(st.FAMILIES))
    sub.add_parser('validate')
    h = sub.add_parser('holdout'); h.add_argument('--confirm', action='store_true')
    oo = sub.add_parser('oos'); oo.add_argument('--confirm', action='store_true')
    r = sub.add_parser('run'); r.add_argument('family'); r.add_argument('params', nargs='*')
    r.add_argument('--period', default='dev', choices=['dev', 'val']); r.add_argument('--stress', type=float, default=1.0)
    r.add_argument('--save', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'dev':
        cmd_dev(a.families, a.tag)
    elif a.cmd == 'validate':
        cmd_period('val', tag=a.tag)
    elif a.cmd == 'holdout':
        cmd_period('holdout', a.confirm, a.tag)
    elif a.cmd == 'oos':
        cmd_oos(a.tag, a.confirm)
    else:
        cmd_run(a.family, _parse_kv(a.params), a.period, a.stress, a.save)


if __name__ == '__main__':
    main()
