"""Robustness checks on the walk-forward LightGBM picks (no holdout data used).

  1. same picks with 1.5x fees and slippage
  2. same picks entered one / two 5m bars LATER (latency; a leak usually collapses here)
  3. three model seeds
  4. breakdown by coin and by side

  cd playgroundML && ..\\.venv\\Scripts\\python.exe -m daytrade.ml_checks
"""
import os

import numpy as np
import pandas as pd

from . import ml_dataset as M, ml_train as T, strategies as st
from .backtest import simulate


def trades_variant(ds, pos, side, costs, delay=0):
    out, s = [], (1 if side == 'long' else -1)
    for coin, g in ds.iloc[pos].groupby('coin'):
        o = st._orders(g['entry_pos'].to_numpy() + delay, s, 'market', np.nan, 1, g[f'stop_{side}'].to_numpy(),
                       g[f'target_{side}'].to_numpy(), M.HOLD - 1)
        tr = simulate(T._raw(coin), pd.Series(dtype=float), o, costs, coin, f'ml_{side}')
        if len(tr):
            tr['strat'] = f'ml_{side}'; tr['side_name'] = side; out.append(tr)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def main():
    ds = pd.read_pickle(M.OUT)
    ds['coin_id'] = (ds['coin'] == 'ETH').astype(float)
    feats = M.feature_columns(ds)
    X = ds[feats].to_numpy(float); t = ds.index
    rows, parts_all = [], []
    for year in T.YEARS:
        a, b = T.fold_bounds(year)
        res = {}
        for seed in (0, 1, 2):
            picks = {}
            for side in ('long', 'short'):
                test, p, thr, _ = T.predict_fold(X, ds[f'label_{side}'].to_numpy(), t, a, b, 'lgbm', seed=seed)
                picks[side] = test[p >= thr]
            variants = ({'base': (T.COSTS, 0), 'fees_x1.5': (T.COSTS.stressed(1.5), 0),
                         'delay_5m': (T.COSTS, 1), 'delay_10m': (T.COSTS, 2)} if seed == 0 else {f'seed{seed}': (T.COSTS, 0)})
            for vname, (c, dly) in variants.items():
                tr = pd.concat([trades_variant(ds, picks[sd], sd, c, dly) for sd in picks], ignore_index=True)
                res[vname] = T.account(tr, a, b)['ret']
                if vname == 'base':
                    tr['year'] = year; parts_all.append(tr)
        rows.append(dict(year=year, **res))
        print(year, '  '.join(f'{k} {v * 100:+.1f}%' for k, v in res.items()), flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(T.OUT, 'checks.csv'), index=False)
    allt = pd.concat(parts_all, ignore_index=True)
    g = allt.groupby(['symbol', 'side_name'])['net']
    print('\nper coin x side: trades, avg net % per trade, win rate')
    print(pd.DataFrame({'trades': g.size(), 'avg_net_pct': g.mean() * 100, 'win': g.apply(lambda x: (x > 0).mean())}).round(3).to_string())
    print('\nsum of net % by year x side (no compounding):')
    print((allt.pivot_table(index='year', columns='side_name', values='net', aggfunc='sum') * 100).round(1).to_string())


if __name__ == '__main__':
    main()
