"""Live paper strategy 'ml_rf': the frozen scikit-learn RandomForest system from the WSL search (run22_#1).

Training needs scikit-learn's tree code (blocked on Windows by Smart App Control), so:
  * `train_export` runs inside WSL: fits the model, then exports every tree as plain arrays (npz);
  * `NumpyForest` re-implements RandomForest.predict_proba with numpy, so the live bot runs on Windows.
The export step checks the numpy forest reproduces scikit-learn's probabilities.

  WSL:      python -m daytrade.ml_live train   then   python -m daytrade.ml_live tar > (Windows) model.tar
  Windows:  the live runner uses strategy family 'ml_rf' (registered on import)
"""
import io
import json
import os
import sys
import tarfile
import time

import numpy as np
import pandas as pd

from . import ml_dataset as M, strategies as st

GENOME = {"model": "sk_rf", "stop": 2.0, "rr": 1.5, "hold": 144, "min_net": 0.003, "q": 0.9, "sides": "both",
          "feats": "no_calendar", "mp": {"trees": 200, "depth": 0, "leaf": 200, "maxf": "0.5"}}
TRAIN_END = pd.Timestamp('2026-10-01', tz='UTC')
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'ml', 'live_rf_model')
PEER = {'BTCUSDT': 'ETHUSDT', 'ETHUSDT': 'BTCUSDT'}


# --------------------------------------------------------------------------- numpy forest (Windows-safe)
class NumpyForest:
    def __init__(self, npz):
        d = np.load(npz)
        self.med = d['medians']
        self.trees = [(d[f't{i}_left'], d[f't{i}_right'], d[f't{i}_feat'], d[f't{i}_thr'], d[f't{i}_p1'])
                      for i in range(int(d['n_trees']))]

    def predict_proba(self, X):
        X = np.where(np.isnan(X), self.med, X)
        acc = np.zeros(len(X))
        for left, right, feat, thr, p1 in self.trees:
            node = np.zeros(len(X), dtype=np.int64)
            active = np.flatnonzero(left[node] >= 0)
            while len(active):
                nd = node[active]
                go_left = X[active, feat[nd]] <= thr[nd]
                node[active] = np.where(go_left, left[nd], right[nd])
                active = active[left[node[active]] >= 0]
            acc += p1[node]
        p = acc / len(self.trees)
        return np.column_stack([1 - p, p])


def _export_forest(pipe, path):
    imp, rf = pipe.steps[0][1], pipe.steps[-1][1]
    arrs = {'medians': imp.statistics_.astype(np.float64), 'n_trees': np.array(len(rf.estimators_))}
    for i, est in enumerate(rf.estimators_):
        t = est.tree_
        v = t.value[:, 0, :]
        arrs[f't{i}_left'] = t.children_left; arrs[f't{i}_right'] = t.children_right
        arrs[f't{i}_feat'] = t.feature; arrs[f't{i}_thr'] = t.threshold
        arrs[f't{i}_p1'] = v[:, 1] / v.sum(1)
    np.savez_compressed(path, **arrs)


# --------------------------------------------------------------------------- training (WSL)
def train_export(out_dir):
    from . import ml_evolve as E, ml_evolve_sk  # noqa: F401  (registers the sk_rf model family)
    os.makedirs(out_dir, exist_ok=True)
    g = GENOME
    ds = E.dataset(g['stop'], g['rr'], g['hold'], g['min_net'])
    feats = E.feature_list(ds, g['feats'])
    X = ds[feats].to_numpy(float); t = ds.index
    purge = pd.Timedelta(hours=g['hold'] / 12 + 1)
    train = t < TRAIN_END - purge
    calib = train & (t >= TRAIN_END - pd.Timedelta(days=365))
    pre = train & (t < TRAIN_END - pd.Timedelta(days=365) - purge)
    meta = dict(genome=g, features=feats, train_end=str(TRAIN_END), trained_at=time.time(), thresholds={},
                rows_train=int(train.sum()), label_rule=dict(stop_atr=g['stop'], rr=g['rr'], hold_bars=g['hold'],
                                                             min_net=g['min_net'], taker=M.TAKER, maker=M.MAKER, slip=M.SLIP))
    check = np.random.default_rng(0).choice(np.flatnonzero(train), 3000, replace=False)
    for side in ('long', 'short'):
        y = ds[f'label_{side}'].to_numpy()
        thr = float(np.quantile(E.make_model(g, 0).fit(X[pre], y[pre]).predict_proba(X[calib])[:, 1], g['q']))
        final = E.make_model(g, 0).fit(X[train], y[train])
        path = os.path.join(out_dir, f'forest_{side}.npz')
        _export_forest(final, path)
        diff = np.abs(NumpyForest(path).predict_proba(X[check])[:, 1] - final.predict_proba(X[check])[:, 1]).max()
        meta['thresholds'][side] = thr
        meta[f'numpy_vs_sklearn_max_diff_{side}'] = float(diff)
        print(f'{side}: threshold {thr:.4f}, numpy vs scikit-learn max |diff| {diff:.2e}', file=sys.stderr, flush=True)
    json.dump(meta, open(os.path.join(out_dir, 'meta.json'), 'w'), indent=1)


# --------------------------------------------------------------------------- live strategy (Windows)
_MODEL = {}


def _load():
    if 'meta' not in _MODEL:
        _MODEL['meta'] = json.load(open(os.path.join(MODEL_DIR, 'meta.json')))
        _MODEL['long'] = NumpyForest(os.path.join(MODEL_DIR, 'forest_long.npz'))
        _MODEL['short'] = NumpyForest(os.path.join(MODEL_DIR, 'forest_short.npz'))
    return _MODEL


def ml_rf(ctx, lookback_h=None):
    """Orders for every completed 1h bar whose probability clears the frozen threshold.
    Needs ctx.peers[<other symbol>] (5m klines of the other coin) for the cross-asset features."""
    return signals(ctx, _load(), lookback_h)


def signals(ctx, mdl, lookback_h=None):
    """Shared by every ML family: mdl = {'meta', 'long', 'short'} with predict_proba models."""
    meta = mdl['meta']; g = meta['genome']
    other = getattr(ctx, 'peers', {}).get(PEER.get(ctx.symbol, ''))
    h, f = M._features(ctx.df, other)
    f['coin_id'] = float(ctx.symbol.startswith('ETH'))
    if lookback_h:
        f = f.iloc[-lookback_h:]
    f = f.dropna(subset=['atr_pct'])
    if f.empty:
        return None
    X = f[meta['features']].to_numpy(float)
    ml = mdl['long'].predict_proba(X)[:, 1] - meta['thresholds']['long']
    ms = mdl['short'].predict_proba(X)[:, 1] - meta['thresholds']['short']
    side = np.where((ml >= 0) & (ml >= ms), 1, np.where(ms >= 0, -1, 0))
    keep = side != 0
    if not keep.any():
        return None
    t = ctx.pos(f.index[keep] + pd.Timedelta(hours=1))
    o = ctx.df['open'].to_numpy()
    close = h['close'].reindex(f.index[keep]).to_numpy()
    s = side[keep]
    entry = np.where(t >= 0, o[np.maximum(t, 0)], close) * (1 + s * M.SLIP)
    sd = g['stop'] * f['atr_pct'].to_numpy()[keep] * close
    td = np.maximum(g['rr'] * sd, entry * (M.TAKER + M.MAKER + M.SLIP + g['min_net']))
    return st._orders(t, s, 'market', np.nan, 1, entry - s * sd, entry + s * td, g['hold'] - 1)


st.FAMILIES['ml_rf'] = ml_rf


def _tar_stdout(out_dir):
    """Stream the model folder to stdout (WSL -> Windows without writing through /mnt/c)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w') as tf:
        tf.add(out_dir, arcname='live_rf_model')
    sys.stdout.buffer.write(buf.getvalue())


if __name__ == '__main__':
    if sys.argv[1:] == ['train']:
        train_export('/root/pml/live_rf_model')
    elif sys.argv[1:] == ['tar']:
        _tar_stdout('/root/pml/live_rf_model')
