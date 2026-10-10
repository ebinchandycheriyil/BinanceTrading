"""Do the ML systems and volume breakout agree, and is agreement worth more than either alone?

Historical signals are rebuilt exactly as each system was evaluated (no look-ahead):
  validation 2021-2022: models trained before 2021      test 2023 - Sep 2026: models trained before 2023
Volume breakout (BTC+ETH, BNB fees) is a fixed rule, so its trades are the same as in combo_study.

  Windows:  python -m daytrade.ml_agree signals lgbm      (LightGBM run22_#1, run22_#3)
  WSL:      python -m daytrade.ml_agree signals rf        (scikit-learn RandomForest, WSL run22_#1)
  Windows:  python -m daytrade.ml_agree report
Agreement = an ML signal on the same coin and side whose entry is within WINDOW_H hours before the VB entry
(VB enters on a 1h close; the ML systems decide on every 1h close).
"""
import json
import os
import sys

import numpy as np
import pandas as pd

from . import combo_study as CB, ml_evolve as E

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'ml')
OUT = os.path.join(ART, 'agree')
WINDOW_H = 3
PERIODS = {'2021-22 (validation)': (E.TRAIN_END, E.VAL_END), '2023-Sep 2026 (test)': (E.VAL_END, E.TEST_END)}
SYSTEMS = {'lgbm': {'LGBM#1': ('evolve', 'run22_#1'), 'LGBM#3': ('evolve', 'run22_#3')},
           'rf': {'RF': ('evolve_wsl', 'run22_#1')}}


def build_signals(group, out_dir=OUT):
    if group == 'rf':
        from . import ml_evolve_sk  # noqa: F401  (registers sk_rf)
    os.makedirs(out_dir, exist_ok=True)
    for name, (folder, sid) in SYSTEMS[group].items():
        g = json.load(open(os.path.join(ART, folder, 'finalists.json')))[sid]['g']
        parts = [E.run_system(g, a, a, b).assign(period=p) for p, (a, b) in PERIODS.items()]
        tr = pd.concat(parts, ignore_index=True)
        tr.to_pickle(os.path.join(out_dir, f'signals_{name}.pkl'))
        print(name, len(tr), 'signals', file=sys.stderr, flush=True)


def vb_trades():
    parts = [CB.trades(('volume_breakout', c, 'bnb', False))[1] for c in CB.COINS]
    return pd.concat(parts, ignore_index=True)


def _period(tr):
    et = pd.to_datetime(tr['entry_ts'], unit='ms', utc=True)
    out = pd.Series(None, index=tr.index, dtype=object)
    for p, (a, b) in PERIODS.items():
        out[(et >= a) & (et < b)] = p
    return out


def agree_mask(base, other, window_h=WINDOW_H):
    """True where `other` has a same-coin, same-side signal entering in [base entry - window, base entry]."""
    w = window_h * 3600_000
    m = np.zeros(len(base), bool)
    for (sym, side), idx in base.groupby(['symbol', 'side']).groups.items():
        o = np.sort(other.loc[(other['symbol'] == sym) & (other['side'] == side), 'entry_ts'].to_numpy())
        if not len(o):
            continue
        t = base.loc[idx, 'entry_ts'].to_numpy()
        j = np.searchsorted(o, t, side='right') - 1
        ok = j >= 0
        m[base.index.get_indexer(idx)[ok]] = t[ok] - o[j[ok]] <= w
    return m


def per_trade(tr):
    if not len(tr):
        return dict(n=0)
    r = tr['net']
    return dict(n=int(len(tr)), win=round(float((r > 0).mean()) * 100, 1), avg_net=round(float(r.mean()) * 100, 3),
                avg_r=round(float(tr['net_r'].mean()), 3))


def account(tr, period):
    if not len(tr):
        return dict(trades=0, ret=0.0, maxdd=0.0)
    a, b = PERIODS[period]
    CB.PERIODS['_p'] = (a.isoformat(), b.isoformat())
    s = CB.stats(tr, 100_000, '_p')
    return dict(trades=s.get('trades', 0), ret=round(s.get('ret', 0.0) * 100, 1), maxdd=round(s.get('maxdd', 0.0) * 100, 1))


def report():
    sig = {n: pd.read_pickle(os.path.join(OUT, f'signals_{n}.pkl')) for n in ('LGBM#1', 'LGBM#3', 'RF')
           if os.path.exists(os.path.join(OUT, f'signals_{n}.pkl'))}
    vb = vb_trades(); vb['period'] = _period(vb); vb = vb[vb['period'].notna()].reset_index(drop=True)
    vb['strat'] = 'vb'
    for n in sig:
        vb[n] = agree_mask(vb, sig[n])
    names = list(sig)
    vb['n_ml'] = vb[names].sum(1)
    res = dict(window_h=WINDOW_H, vb_sides=vb['side'].value_counts().to_dict(), periods={})
    for p in PERIODS:
        v = vb[vb['period'] == p]
        row = dict(vb_all=dict(per_trade(v), **account(v, p)))
        for n in names:
            row[f'vb_and_{n}'] = dict(per_trade(v[v[n]]), **account(v[v[n]], p))
            row[f'vb_not_{n}'] = per_trade(v[~v[n]])
            s = sig[n][sig[n]['period'] == p]
            row[f'{n}_alone'] = dict(per_trade(s), **account(s.assign(strat=n), p))
        row['vb_and_any_ml'] = dict(per_trade(v[v['n_ml'] >= 1]), **account(v[v['n_ml'] >= 1], p))
        row['vb_and_2plus_ml'] = dict(per_trade(v[v['n_ml'] >= 2]), **account(v[v['n_ml'] >= 2], p))
        row['vb_no_ml'] = per_trade(v[v['n_ml'] == 0])
        # ML-vs-ML agreement: LGBM#1 signals confirmed by the other ML systems
        if 'LGBM#1' in sig:
            s1 = sig['LGBM#1'][sig['LGBM#1']['period'] == p].reset_index(drop=True)
            others = [n for n in names if n != 'LGBM#1']
            k = sum(agree_mask(s1, sig[n], 1).astype(int) for n in others) if others else np.zeros(len(s1), int)
            for lvl in range(len(others) + 1):
                row[f'LGBM#1_confirmed_by_{lvl}_others'] = per_trade(s1[k == lvl])
            row['LGBM#1_confirmed_all'] = dict(per_trade(s1[k == len(others)]),
                                               **account(s1[k == len(others)].assign(strat='ml'), p))
        res['periods'][p] = row
    json.dump(res, open(os.path.join(OUT, 'agree_results.json'), 'w'), indent=1, default=str)
    with open(os.path.join(ART, 'holdout_access.log'), 'a') as fh:
        fh.write(f'{pd.Timestamp.now(tz="UTC").isoformat()} ml_agree report: VB x ML agreement on 2021-22 and 2023-2026\n')
    print(json.dumps(res, indent=1, default=str))


if __name__ == '__main__':
    if sys.argv[1] == 'signals':
        build_signals(sys.argv[2], *(sys.argv[3:4]))
    elif sys.argv[1] == 'report':
        report()
