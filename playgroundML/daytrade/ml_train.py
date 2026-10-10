"""Walk-forward training on the labelled entry dataset (frozen protocol).

Folds: for test year Y in 2019..2025 (2025 = Jan..Sep), train on every decision before Jan 1 of Y minus a
13h purge (labels look 12h ahead). Final holdout Oct 2025..Sep 2026 is only scored with `--holdout`.
Per side (long/short) and model (logistic regression, LightGBM with fixed parameters):
  * threshold = 90th percentile of out-of-sample probabilities on the LAST 12 MONTHS OF THE TRAINING window
    (from a model fit without those months) -> trade only the ~10% most confident hours;
  * selected hours become real orders (same stop/target/12h) through backtest.simulate, one position per coin,
    then the Rs1L account with Binance minimums (combo_study.stats).
Controls: plain volume_breakout, random hours with the same count (10 draws), shuffled-label model.
All row selection is positional (BTC and ETH share timestamps).

  cd playgroundML && ..\\.venv\\Scripts\\python.exe -m daytrade.ml_train            # walk-forward only
  cd playgroundML && ..\\.venv\\Scripts\\python.exe -m daytrade.ml_train --holdout  # single final look
"""
import argparse
import json
import os
import time
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import combo_study as CB, ml_dataset as M, strategies as st
from .backtest import Costs, simulate

warnings.filterwarnings('ignore')
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'ml')
YEARS = list(range(2019, 2026))
HOLDOUT = (pd.Timestamp('2025-10-01', tz='UTC'), pd.Timestamp('2026-10-01', tz='UTC'))
PURGE = pd.Timedelta(hours=13)
TOP = 0.90
LGB = dict(objective='binary', learning_rate=0.03, num_leaves=15, min_data_in_leaf=300, bagging_fraction=0.7,
           bagging_freq=1, feature_fraction=0.7, lambda_l2=5.0, verbose=-1, num_threads=6)
ROUNDS = 300
# scikit-learn's compiled modules are blocked by this machine's Windows application-control policy,
# so the baseline model, scaling, imputation and AUC are implemented with numpy.
COSTS = Costs(taker=M.TAKER, maker=M.MAKER, slip=M.SLIP, funding=False)
_RAW = {}


def _raw(coin):
    if coin not in _RAW:
        _RAW[coin] = pd.read_pickle(M.COINS[coin])
    return _RAW[coin]


class Logit:
    """Median imputation + standardisation + L2 logistic regression fitted by Newton's method."""

    def __init__(self, l2=20.0, iters=12):
        self.l2, self.iters = l2, iters

    def _prep(self, X):
        X = np.where(np.isnan(X), self.med, X)
        return np.column_stack([np.ones(len(X)), (X - self.mu) / self.sd])

    def fit(self, X, y):
        self.med = np.nanmedian(X, axis=0); self.med = np.where(np.isnan(self.med), 0.0, self.med)
        Xf = np.where(np.isnan(X), self.med, X)
        self.mu, self.sd = Xf.mean(0), Xf.std(0) + 1e-12
        Z = self._prep(X); w = np.zeros(Z.shape[1]); reg = np.full(len(w), self.l2); reg[0] = 0.0
        for _ in range(self.iters):
            p = 1 / (1 + np.exp(-np.clip(Z @ w, -30, 30)))
            g = Z.T @ (p - y) + reg * w
            Hm = (Z * (p * (1 - p))[:, None]).T @ Z + np.diag(reg)
            w -= np.linalg.solve(Hm, g)
        self.w = w
        return self

    def predict_proba(self, X):
        p = 1 / (1 + np.exp(-np.clip(self._prep(X) @ self.w, -30, 30)))
        return np.column_stack([1 - p, p])


class GBM:
    def __init__(self, seed=0):
        self.seed = seed

    def fit(self, X, y):
        self.booster = lgb.train(dict(LGB, seed=self.seed), lgb.Dataset(X, y), num_boost_round=ROUNDS)
        return self

    def predict_proba(self, X):
        p = self.booster.predict(X)
        return np.column_stack([1 - p, p])


def roc_auc_score(y, p):
    r = pd.Series(p).rank().to_numpy(); pos = y == 1; n1, n0 = pos.sum(), (~pos).sum()
    return (r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0) if n1 and n0 else np.nan


def _model(kind, seed=0):
    return Logit() if kind == 'logit' else GBM(seed)


def fold_bounds(year):
    a = pd.Timestamp(f'{year}-01-01', tz='UTC')
    return a, (HOLDOUT[0] if year == 2025 else pd.Timestamp(f'{year + 1}-01-01', tz='UTC'))


def predict_fold(X, y, t, a, b, kind, shuffle=False, seed=0):
    """Returns (positions of test rows, probabilities, threshold, fitted model)."""
    train = t < a - PURGE
    if shuffle:
        y = y.copy(); y[train] = np.random.default_rng(seed).permutation(y[train])
    calib = train & (t >= a - pd.Timedelta(days=365))
    pre = train & (t < a - pd.Timedelta(days=365) - PURGE)
    test = np.flatnonzero((t >= a) & (t < b))
    thr = np.quantile(_model(kind, seed).fit(X[pre], y[pre]).predict_proba(X[calib])[:, 1], TOP)
    m = _model(kind, seed).fit(X[train], y[train])
    return test, m.predict_proba(X[test])[:, 1], thr, m


def to_trades(ds, pos, side):
    """Real simulation of the entries at row positions `pos`, one position per coin."""
    out, s = [], (1 if side == 'long' else -1)
    sub = ds.iloc[pos]
    for coin, g in sub.groupby('coin'):
        o = st._orders(g['entry_pos'].to_numpy(), s, 'market', np.nan, 1, g[f'stop_{side}'].to_numpy(),
                       g[f'target_{side}'].to_numpy(), M.HOLD - 1)
        tr = simulate(_raw(coin), pd.Series(dtype=float), o, COSTS, coin, f'ml_{side}')
        if len(tr):
            tr['strat'] = f'ml_{side}'; out.append(tr)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def account(trades, a, b, start_inr=100_000):
    if trades is None or trades.empty:
        return dict(trades=0, ret=0.0, maxdd=0.0, win=np.nan)
    CB.PERIODS['_fold'] = (a.isoformat(), b.isoformat())
    s = CB.stats(trades, start_inr, '_fold')
    return dict(trades=s.get('trades', 0), ret=s.get('ret', 0.0), maxdd=s.get('maxdd', 0.0),
                win=float((trades['net'] > 0).mean()))


def vb_trades():
    if '_vb' not in _RAW:
        parts = []
        for coin in ('BTC', 'ETH'):
            ctx = st.Ctx(coin + 'USDT', _raw(coin)); ctx.funding = pd.Series(dtype=float)
            o = st.FAMILIES['lab'](ctx, **CB.STRATS['volume_breakout'][1])
            o = o.assign(max_bars=np.minimum(o['max_bars'].to_numpy(), 144))
            tr = simulate(ctx.df, ctx.funding, o, COSTS, coin, 'volume_breakout', 0.3); tr['strat'] = 'vb'
            parts.append(tr)
        _RAW['_vb'] = pd.concat(parts, ignore_index=True)
    return _RAW['_vb']


def _between(tr, a, b):
    t = pd.to_datetime(tr['entry_ts'], unit='ms', utc=True)
    return tr[(t >= a) & (t < b)]


def evaluate(ds, feats, folds, name):
    X = ds[feats].to_numpy(float); t = ds.index
    rows = []
    for a, b in folds:
        vb = account(_between(vb_trades(), a, b), a, b)
        for kind in ('logit', 'lgbm'):
            picks, info, sh_parts = {}, {}, []
            for side in ('long', 'short'):
                y = ds[f'label_{side}'].to_numpy()
                test, p, thr, _ = predict_fold(X, y, t, a, b, kind)
                sel = p >= thr
                picks[side] = (test, test[sel])
                yt = y[test]
                info[side] = dict(auc=roc_auc_score(yt, p), base=yt.mean(), prec=yt[sel].mean() if sel.any() else np.nan)
                tst, ps, ths, _ = predict_fold(X, y, t, a, b, kind, shuffle=True)
                sh_parts.append(to_trades(ds, tst[ps >= ths], side))
            res = account(pd.concat([to_trades(ds, picks[s][1], s) for s in picks], ignore_index=True), a, b)
            rnd = []
            for d in range(10):
                rng = np.random.default_rng(100 + d)
                parts = [to_trades(ds, rng.choice(test, size=min(len(chosen), len(test)), replace=False), s)
                         for s, (test, chosen) in picks.items()]
                rnd.append(account(pd.concat(parts, ignore_index=True), a, b)['ret'])
            sh = account(pd.concat(sh_parts, ignore_index=True), a, b)
            r = dict(period=name(a), model=kind, trades=res['trades'], ret=res['ret'], maxdd=res['maxdd'], win=res['win'],
                     random_median=float(np.median(rnd)), random_best=float(np.max(rnd)), shuffled=sh['ret'],
                     vb_ret=vb['ret'], vb_trades=vb['trades'], auc_long=info['long']['auc'], auc_short=info['short']['auc'],
                     hit_long=info['long']['prec'], base_long=info['long']['base'],
                     hit_short=info['short']['prec'], base_short=info['short']['base'])
            rows.append(r)
            print(f"{r['period']} {kind:5s}: model {r['ret'] * 100:+6.1f}% ({r['trades']} trades, DD {r['maxdd'] * 100:.0f}%) | "
                  f"random {r['random_median'] * 100:+6.1f}% | shuffled {r['shuffled'] * 100:+6.1f}% | VB {r['vb_ret'] * 100:+6.1f}% | "
                  f"AUC {r['auc_long']:.3f}/{r['auc_short']:.3f} | hit L {r['hit_long']:.2f} vs {r['base_long']:.2f}, "
                  f"S {r['hit_short']:.2f} vs {r['base_short']:.2f}", flush=True)
    return pd.DataFrame(rows)


def importance(ds, feats):
    X = ds[feats].to_numpy(float); out = {}
    for side in ('long', 'short'):
        _, _, _, m = predict_fold(X, ds[f'label_{side}'].to_numpy(), ds.index, HOLDOUT[0], HOLDOUT[1], 'lgbm')
        out[side] = pd.Series(m.booster.feature_importance('gain'), feats)
    return pd.DataFrame(out).sort_values('long', ascending=False)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--holdout', action='store_true'); args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    ds = pd.read_pickle(M.OUT)
    ds['coin_id'] = (ds['coin'] == 'ETH').astype(float)
    feats = M.feature_columns(ds)
    json.dump(dict(features=feats, years=YEARS, holdout=[str(x) for x in HOLDOUT], top=TOP, lgb=LGB, rounds=ROUNDS, purge_h=13,
                   label=dict(stop_atr=M.STOP_ATR, rr=M.RR, min_net=M.MIN_NET, hold_bars=M.HOLD,
                              fees=dict(taker=M.TAKER, maker=M.MAKER, slip=M.SLIP)), written_at=time.time()),
              open(os.path.join(OUT, 'protocol.json'), 'w'), indent=1)
    if args.holdout:
        with open(os.path.join(OUT, 'holdout_access.log'), 'a') as fh:
            fh.write(f'{pd.Timestamp.now(tz="UTC").isoformat()} ml_train holdout\n')
        evaluate(ds, feats, [HOLDOUT], lambda a: 'HOLDOUT').to_csv(os.path.join(OUT, 'holdout.csv'), index=False)
        return
    evaluate(ds, feats, [fold_bounds(y) for y in YEARS], lambda a: str(a.year)).to_csv(
        os.path.join(OUT, 'walkforward.csv'), index=False)
    imp = importance(ds, feats); imp.to_csv(os.path.join(OUT, 'feature_importance.csv'))
    print('\ntop features by gain (long / short):\n', imp.head(12).round(0).to_string())


if __name__ == '__main__':
    main()
