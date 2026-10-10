"""Live paper strategy 'ml_lgbm': frozen LightGBM systems from the Windows evolutionary search.

LightGBM runs natively on Windows and Linux, so the model is trained here and saved as LightGBM's own text
format; the live bot only needs `lightgbm` to load and predict (no scikit-learn).
Signal logic is shared with ml_rf (ml_live.signals): same features, thresholds, stops and targets.

  python -m daytrade.ml_live_lgbm train run22_#1      (writes artifacts/ml/live_lgbm_run22_1/)
  live runner: --strategy ml_lgbm --params '{"system": "run22_#1"}'
"""
import json
import os
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import ml_live as L, strategies as st

ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'ml')
FINALISTS = os.path.join(ART, 'evolve', 'finalists.json')
TRAIN_END = L.TRAIN_END


def model_dir(system):
    return os.path.join(ART, 'live_lgbm_' + system.replace('#', ''))


class Booster:
    def __init__(self, path):
        self.b = lgb.Booster(model_file=path)

    def predict_proba(self, X):
        p = self.b.predict(X, num_threads=1)
        return np.column_stack([1 - p, p])


def train_export(system):
    from . import ml_evolve as E
    g = json.load(open(FINALISTS))[system]['g']
    out = model_dir(system); os.makedirs(out, exist_ok=True)
    ds = E.dataset(g['stop'], g['rr'], g['hold'], g['min_net'])
    feats = E.feature_list(ds, g['feats'])
    X = ds[feats].to_numpy(float); t = ds.index
    purge = pd.Timedelta(hours=g['hold'] / 12 + 1)
    train = t < TRAIN_END - purge
    calib = train & (t >= TRAIN_END - pd.Timedelta(days=365))
    pre = train & (t < TRAIN_END - pd.Timedelta(days=365) - purge)
    meta = dict(system=system, genome=g, features=feats, train_end=str(TRAIN_END), trained_at=time.time(),
                thresholds={}, rows_train=int(train.sum()))
    check = np.random.default_rng(0).choice(np.flatnonzero(train), 3000, replace=False)
    for side in ('long', 'short'):
        y = ds[f'label_{side}'].to_numpy()
        thr = float(np.quantile(E.make_model(g, 0).fit(X[pre], y[pre]).predict_proba(X[calib])[:, 1], g['q']))
        final = E.make_model(g, 0).fit(X[train], y[train])
        path = os.path.join(out, f'lgbm_{side}.txt')
        final.b.save_model(path)
        diff = np.abs(Booster(path).predict_proba(X[check])[:, 1] - final.predict_proba(X[check])[:, 1]).max()
        meta['thresholds'][side] = thr
        meta[f'reload_max_diff_{side}'] = float(diff)
        print(f'{system} {side}: threshold {thr:.4f}, reload max |diff| {diff:.2e}', file=sys.stderr, flush=True)
    json.dump(meta, open(os.path.join(out, 'meta.json'), 'w'), indent=1)


_MODELS = {}


def ml_lgbm(ctx, system, lookback_h=None):
    if system not in _MODELS:
        d = model_dir(system)
        _MODELS[system] = dict(meta=json.load(open(os.path.join(d, 'meta.json'))),
                               long=Booster(os.path.join(d, 'lgbm_long.txt')), short=Booster(os.path.join(d, 'lgbm_short.txt')))
    return L.signals(ctx, _MODELS[system], lookback_h)


st.FAMILIES['ml_lgbm'] = ml_lgbm

if __name__ == '__main__':
    if sys.argv[1] == 'train':
        for s_ in sys.argv[2:]:
            train_export(s_)
