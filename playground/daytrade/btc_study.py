"""BTC-only day-trading study on Coinbase BTC-USD 5m, mid-2015 .. Sep 2026 (frozen protocol, single run).

Every strategy is capped at a 12h hold. Walk-forward: pick the best setting per strategy on 2015-07..2020,
check on 2021..2023, final test on 2024..2026-09. Costs: Binance-style BTC perp taker 0.05% + 1.5 bp slippage
per side (maker 0.02% where limit orders are used); no funding (spot data, perps did not exist before 2019).

  cd playground && ..\\.venv\\Scripts\\python.exe -m daytrade.btc_study
"""
from concurrent.futures import ProcessPoolExecutor
import json
import os
import time

import numpy as np
import pandas as pd

from . import btc_history, lab, research as R, strategies as st
from .backtest import costs_for, simulate

OUT = os.path.join(R.OUT, 'btc')
MAX_BARS = 144                     # 12h day-trading cap
MIN_TRADES = 100
PER = {'b_dev': ('2015-07-01', '2021-01-01'), 'b_val': ('2021-01-01', '2024-01-01'),
       'b_test': ('2024-01-01', '2026-10-01'), 'b_all': ('2015-07-01', '2026-10-01')}
R.PERIODS.update(PER)
DAY_FAMILIES = ['orb', 'sweep', 'vwap_fade', 'pullback', 'squeeze', 'cascade', 'daymom', 'nr_breakout',
                'rsi2_dip', 'hour_season']
_C = {}


def _ctx():
    if 'c' not in _C:
        c = st.Ctx('BTCUSDT', pd.read_pickle(btc_history.PATH))
        c.funding = pd.Series(dtype=float); c.metrics = None
        _C['c'] = c
    return _C['c']


def trades(fam, params, period, stress=1.0):
    ctx = _ctx()
    o = st.FAMILIES[fam](ctx, **params)
    if o is None or o.empty:
        return pd.DataFrame()
    o = o.assign(max_bars=np.minimum(o['max_bars'].to_numpy(), MAX_BARS))
    a, b = (pd.Timestamp(x, tz='UTC') for x in PER[period])
    t = ctx.idx[np.minimum(o['t'].to_numpy(), len(ctx.idx) - 1)]
    o = o[(t >= a) & (t < b)]
    c = costs_for('BTCUSDT')
    if stress != 1.0:
        c = c.stressed(stress)
    return simulate(ctx.df, ctx.funding, o, c, 'BTC', fam, R.MAX_COST_R)


def evaluate(job):
    fam, params, period, with_stress = job
    tr = trades(fam, params, period)
    m = R.metrics(tr, period)
    if m['trades'] and with_stress:
        m['stress_ret'] = R.metrics(trades(fam, params, period, 1.5), period).get('ret', 0.0)
    return fam, params, period, m


def _pool(jobs):
    with ProcessPoolExecutor(max(1, min(6, (os.cpu_count() or 2) - 1))) as pool:
        yield from pool.map(evaluate, jobs, chunksize=2)


def configs():
    for p in lab.grid():
        yield 'lab', p
    for f in DAY_FAMILIES:
        for p in st.grid(f):
            yield f, p


def _label(fam, p):
    if fam == 'lab':
        return f"{lab.VARIANTS[p['ind']][int(p['var'])]}, {p['tf']}, {p['exit']}, filter {p['filt']}"
    return json.dumps(p, default=str)


def _ok(m, n):
    return m.get('trades', 0) >= n and m.get('ret', -1) > 0 and (m.get('pf') or 0) >= 1.10 and m.get('stress_ret', -1) > 0


def main():
    os.makedirs(OUT, exist_ok=True)
    cfg = list(configs())
    json.dump(dict(periods=PER, max_hold_bars=MAX_BARS, configs=len(cfg), min_trades=MIN_TRADES,
                   selection='per strategy family / indicator: highest dev t-stat with >= 100 trades',
                   verdict='WORKS = dev (ret>0, PF>=1.10, t>=2, 1.5x-cost ret>0) and validation and test each '
                           '(>=30 trades, ret>0, PF>=1.10, 1.5x-cost ret>0); PROMISING = dev ret>0 and val or test ret>0',
                   costs='BTC taker 0.05% + 1.5bp slippage/side, maker 0.02%; no funding', written_at=time.time()),
              open(os.path.join(OUT, 'protocol.json'), 'w'), indent=1)
    print(f'stage 1: {len(cfg)} configs on 2015-07..2020', flush=True)
    rows = []
    for i, (f, p, _, m) in enumerate(_pool([(f, p, 'b_dev', False) for f, p in cfg]), 1):
        key = p['ind'] if f == 'lab' else f
        rows.append(dict(group=key, family=f, params=json.dumps(p, default=str),
                         **{k: v for k, v in m.items() if not isinstance(v, dict)}))
        if i % 50 == 0:
            print(f'  {i}/{len(cfg)}', flush=True)
    s1 = pd.DataFrame(rows)
    s1.to_csv(os.path.join(OUT, 'stage1.csv'), index=False)

    picks = {}
    for g, d in s1.groupby('group'):
        d = d[d['trades'] >= MIN_TRADES].sort_values('t', ascending=False)
        if len(d):
            p = json.loads(d.iloc[0]['params'])
            p = {k: (float('nan') if v == 'NaN' else v) for k, v in p.items()}
            picks[g] = (d.iloc[0]['family'], p)
    json.dump(picks, open(os.path.join(OUT, 'selection.json'), 'w'), indent=1, default=str)
    print(f'stage 2: {len(picks)} picks', flush=True)
    jobs = [(f, p, per, True) for f, p in picks.values() for per in ('b_dev', 'b_val', 'b_test', 'b_all')]
    res = {}
    for f, p, per, m in _pool(jobs):
        g = p['ind'] if f == 'lab' else f
        res.setdefault(g, dict(family=f, params=p, label=_label(f, p)))[per] = m
    for r in res.values():
        d, v, t = r['b_dev'], r['b_val'], r['b_test']
        dev_ok = d.get('ret', -1) > 0 and (d.get('pf') or 0) >= 1.10 and d.get('t', 0) >= 2 and d.get('stress_ret', -1) > 0
        r['verdict'] = ('WORKS' if dev_ok and _ok(v, 30) and _ok(t, 30) else
                        'PROMISING' if d.get('ret', -1) > 0 and (v.get('ret', -1) > 0 or t.get('ret', -1) > 0) else 'FAILS')
    json.dump(res, open(os.path.join(OUT, 'stage2.json'), 'w'), indent=1, default=str)
    report(s1, res)


def _cell(m):
    if not m.get('trades'):
        return '0 trades'
    return f"{m['trades']} · {m['ret'] * 100:+.1f}% · PF {m['pf']} · 1.5x {m.get('stress_ret', 0) * 100:+.1f}%"


def report(s1, res):
    rank = {'WORKS': 0, 'PROMISING': 1, 'FAILS': 2}
    order = sorted(res, key=lambda g: (rank[res[g]['verdict']],
                                       -(res[g]['b_val'].get('ret', -9) + res[g]['b_test'].get('ret', -9))))
    L = ['# BTC day trading 2015–2026 (Coinbase BTC-USD 5m)', '',
         'Max hold 12h. Cells: trades · return · profit factor · return at 1.5x costs (0.5% risk/trade on 1,000 USDT).', '',
         '| Strategy | Best setting (picked on 2015–2020) | Win | 2015–2020 (pick) | 2021–2023 (check) | 2024–2026 (test) | Verdict |',
         '|---|---|---:|---|---|---|---|']
    for g in order:
        r = res[g]
        L.append(f"| {g} | {r['label']} | {r['b_dev'].get('win', 0) * 100:.0f}% | {_cell(r['b_dev'])} | "
                 f"{_cell(r['b_val'])} | {_cell(r['b_test'])} | **{r['verdict']}** |")
    years = sorted({int(y) for r in res.values() for y in r['b_all'].get('by_year', {})})
    L += ['', '## Year by year (sum of R, picked settings)', '',
          '| Strategy | ' + ' | '.join(str(y) for y in years) + ' |', '|---|' + '---:|' * len(years)]
    for g in order[:12]:
        by = {int(k): v for k, v in res[g]['b_all'].get('by_year', {}).items()}
        L.append(f'| {g} | ' + ' | '.join(f"{by.get(y, 0):+.0f}" for y in years) + ' |')
    L += ['', f"## Stage 1: {len(s1)} configs on 2015–2020, {(s1['ret'] > 0).sum()} profitable", '',
          '| Strategy | Profitable configs | Best return | Median return |', '|---|---:|---:|---:|']
    for g, d in s1.groupby('group'):
        L.append(f"| {g} | {(d['ret'] > 0).sum()}/{len(d)} | {d['ret'].max() * 100:+.1f}% | {d['ret'].median() * 100:+.1f}% |")
    open(os.path.join(OUT, 'REPORT.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L), flush=True)


if __name__ == '__main__':
    main()
