"""Evolutionary search over complete ML trading systems (frozen protocol).

A genome = model family + its settings + label rule (stop, reward/risk, hold, minimum net profit) +
confidence cut-off + sides + feature group. Splits:
  TRAIN 2015-07 .. 2020-12   fit models (threshold calibrated on the last 12 months of the training window)
  VALID 2021 .. 2022         fitness = min(return 2021, return 2022) - 0.5 x worst drawdown  (-1 if < 40 trades/yr)
  TEST  2023 .. 2026-09      scored ONCE for finalists, retrained on 2015-2022; access logged
Evolution: population 20, 5 generations, 5 elites, 15 children (crossover + mutation); 3 independent runs.
Models: scikit-learn LogisticRegression and MLPClassifier; LightGBM gbdt / random-forest / extra-trees
(scikit-learn's own tree models are blocked by Windows Smart App Control on this PC).

  cd playgroundML && ..\\.venv\\Scripts\\python.exe -m daytrade.ml_evolve
"""
from concurrent.futures import ProcessPoolExecutor
import json
import os
import random
import time
import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import combo_study as CB, ml_dataset as M, strategies as st
from .backtest import Costs, simulate

warnings.filterwarnings('ignore')
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'ml', 'evolve')
TRAIN_END, VAL_END, TEST_END = (pd.Timestamp(x, tz='UTC') for x in ('2021-01-01', '2023-01-01', '2026-10-01'))
POP, GENS, ELITE, RUNS = 20, 5, 5, (11, 22, 33)
COSTS = Costs(taker=M.TAKER, maker=M.MAKER, slip=M.SLIP, funding=False)

SPACE = {
    'model': ['logit', 'mlp', 'lgbm', 'lgbm_rf', 'lgbm_et'],
    'stop': [1.0, 1.5, 2.0], 'rr': [1.0, 1.5, 2.0], 'hold': [48, 96, 144], 'min_net': [0.002, 0.003, 0.005],
    'q': [0.80, 0.90, 0.95], 'sides': ['long', 'short', 'both'],
    'feats': ['all', 'no_calendar', 'price_vol', 'indicators'],
}
MODEL_SPACE = {
    'logit': dict(C=[0.001, 0.01, 0.1, 1.0], pen=['l1', 'l2']),
    'mlp': dict(hidden=['32', '64', '64-32', '128-64'], alpha=[1e-4, 1e-3, 1e-2]),
    'lgbm': dict(leaves=[7, 15, 31, 63], lr=[0.01, 0.03, 0.1], rounds=[100, 300, 600], min_leaf=[100, 300, 1000], ff=[0.5, 0.8]),
    'lgbm_rf': dict(leaves=[15, 63, 255], rounds=[100, 300], min_leaf=[100, 500], ff=[0.5, 0.8]),
    'lgbm_et': dict(leaves=[7, 15, 31, 63], lr=[0.01, 0.03, 0.1], rounds=[100, 300, 600], min_leaf=[100, 300, 1000], ff=[0.5, 0.8]),
}
CAL = ['hour_sin', 'hour_cos', 'weekday']
IND = ['rsi14', 'rsi2', 'macd_hist', 'bb_pctb', 'bb_width', 'adx', 'dmi_diff', 'stoch_k', 'cci', 'mfi', 'vol_z20',
       'vol_z168', 'taker_share', 'candle_body', 'upper_wick', 'lower_wick', 'sig_volume_breakout', 'sig_bb_breakout',
       'sig_donchian', 'atr_pct']

_G = {}


# --------------------------------------------------------------------------- data
def _init():
    if 'feat' not in _G:
        _G['feat'] = pd.read_pickle(os.path.join(M.ROOT, 'ml_features.pkl'))
        _G['raw'] = {c: pd.read_pickle(p) for c, p in M.COINS.items()}
        _G['labels'] = {}


def dataset(stop, rr, hold, min_net):
    _init()
    key = (stop, rr, hold, min_net)
    if key not in _G['labels']:
        parts = []
        for coin, (h, f) in _G['feat'].items():
            lb = M._labels(_G['raw'][coin], h, f, stop_atr=stop, rr=rr, min_net=min_net, hold=hold)
            d = f.loc[lb.index].join(lb); d['coin'] = coin; d['coin_id'] = float(coin == 'ETH')
            parts.append(d)
        _G['labels'] = {key: pd.concat(parts).sort_index()}          # one label set per worker (memory)
    return _G['labels'][key]


def feature_list(ds, group):
    allf = M.feature_columns(ds)
    if group == 'no_calendar':
        return [c for c in allf if c not in CAL]
    if group == 'indicators':
        return [c for c in allf if c in IND] + ['coin_id']
    if group == 'price_vol':
        return [c for c in allf if c not in IND and c not in CAL]
    return allf


# --------------------------------------------------------------------------- models
def make_model(g, seed):
    m, p = g['model'], g['mp']
    if m in ('logit', 'mlp'):
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        if m == 'logit':
            from sklearn.linear_model import LogisticRegression
            est = LogisticRegression(C=p['C'], penalty=p['pen'], solver='liblinear', max_iter=300)
        else:
            from sklearn.neural_network import MLPClassifier
            est = MLPClassifier(hidden_layer_sizes=tuple(int(x) for x in p['hidden'].split('-')), alpha=p['alpha'],
                                early_stopping=True, max_iter=60, batch_size=512, random_state=seed)
        return make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), est)
    return _LGB(m, p, seed)


class _LGB:
    def __init__(self, kind, p, seed):
        self.params = dict(objective='binary', num_leaves=p['leaves'], min_data_in_leaf=p['min_leaf'],
                           feature_fraction=p['ff'], bagging_fraction=0.7, bagging_freq=1, lambda_l2=5.0,
                           verbose=-1, num_threads=1, seed=seed, learning_rate=p.get('lr', 1.0))
        if kind == 'lgbm_rf':
            self.params.update(boosting='rf', bagging_fraction=0.6)
        if kind == 'lgbm_et':
            self.params.update(extra_trees=True)
        self.rounds = p['rounds']

    def fit(self, X, y):
        self.b = lgb.train(self.params, lgb.Dataset(X, y), num_boost_round=self.rounds)
        return self

    def predict_proba(self, X):
        p = self.b.predict(X)
        return np.column_stack([1 - p, p])


# --------------------------------------------------------------------------- one system
def run_system(g, train_end, pred_start, pred_end, seed=0, stress=1.0):
    """Fit on decisions before train_end (purged), calibrate threshold on the last 12 training months,
    trade the predicted window. Returns trades DataFrame."""
    ds = dataset(g['stop'], g['rr'], g['hold'], g['min_net'])
    feats = feature_list(ds, g['feats'])
    X = ds[feats].to_numpy(float); t = ds.index
    purge = pd.Timedelta(hours=g['hold'] / 12 + 1)
    train = t < train_end - purge
    calib = train & (t >= train_end - pd.Timedelta(days=365))
    pre = train & (t < train_end - pd.Timedelta(days=365) - purge)
    pred = np.flatnonzero((t >= pred_start) & (t < pred_end))
    sides = ['long', 'short'] if g['sides'] == 'both' else [g['sides']]
    costs = COSTS if stress == 1.0 else COSTS.stressed(stress)
    out = []
    for side in sides:
        y = ds[f'label_{side}'].to_numpy()
        thr = np.quantile(make_model(g, seed).fit(X[pre], y[pre]).predict_proba(X[calib])[:, 1], g['q'])
        p = make_model(g, seed).fit(X[train], y[train]).predict_proba(X[pred])[:, 1]
        pos = pred[p >= thr]
        s = 1 if side == 'long' else -1
        for coin, sub in ds.iloc[pos].groupby('coin'):
            o = st._orders(sub['entry_pos'].to_numpy(), s, 'market', np.nan, 1, sub[f'stop_{side}'].to_numpy(),
                           sub[f'target_{side}'].to_numpy(), g['hold'] - 1)
            tr = simulate(_G['raw'][coin], pd.Series(dtype=float), o, costs, coin, side)
            if len(tr):
                tr['strat'] = side; out.append(tr)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def year_stats(tr, a, b, start_inr=100_000):
    if tr is None or tr.empty:
        return dict(trades=0, ret=0.0, maxdd=0.0)
    CB.PERIODS['_y'] = (a.isoformat(), b.isoformat())
    s = CB.stats(tr, start_inr, '_y')
    return dict(trades=s.get('trades', 0), ret=s.get('ret', 0.0), maxdd=s.get('maxdd', 0.0))


def evaluate(g):
    t0 = time.time()
    try:
        tr = run_system(g, TRAIN_END, TRAIN_END, VAL_END)
        y21 = year_stats(tr, pd.Timestamp('2021-01-01', tz='UTC'), pd.Timestamp('2022-01-01', tz='UTC'))
        y22 = year_stats(tr, pd.Timestamp('2022-01-01', tz='UTC'), VAL_END)
        fit = min(y21['ret'], y22['ret']) - 0.5 * max(y21['maxdd'], y22['maxdd'])
        if min(y21['trades'], y22['trades']) < 40:
            fit -= 1.0
        return dict(g=g, fitness=fit, r2021=y21['ret'], r2022=y22['ret'], dd=max(y21['maxdd'], y22['maxdd']),
                    trades=y21['trades'] + y22['trades'], secs=round(time.time() - t0, 1))
    except Exception as e:      # a broken genome scores worst instead of stopping the run
        return dict(g=g, fitness=-9.0, error=repr(e)[:200], secs=round(time.time() - t0, 1))


# --------------------------------------------------------------------------- genetics
def random_genome(rng):
    g = {k: rng.choice(v) for k, v in SPACE.items()}
    g['mp'] = {k: rng.choice(v) for k, v in MODEL_SPACE[g['model']].items()}
    return g


def mutate(g, rng):
    g = json.loads(json.dumps(g))
    for _ in range(rng.choice([1, 2, 3])):
        k = rng.choice(list(SPACE) + ['mp'])
        if k == 'mp':
            mk = rng.choice(list(MODEL_SPACE[g['model']])); g['mp'][mk] = rng.choice(MODEL_SPACE[g['model']][mk])
        else:
            g[k] = rng.choice(SPACE[k])
            if k == 'model':
                g['mp'] = {mk: rng.choice(v) for mk, v in MODEL_SPACE[g['model']].items()}
    return g


def crossover(a, b, rng):
    c = {k: (a if rng.random() < 0.5 else b)[k] for k in SPACE}
    c['mp'] = dict((a if c['model'] == a['model'] else b)['mp'])
    return c


def key(g):
    return json.dumps(g, sort_keys=True)


def evolve(pool, seed, log):
    rng = random.Random(seed)
    seen, pop = {}, []
    while len(pop) < POP:
        g = random_genome(rng)
        if key(g) not in {key(x) for x in pop}:
            pop.append(g)
    for gen in range(1, GENS + 1):
        todo = [g for g in pop if key(g) not in seen]
        for r in pool.map(evaluate, todo):
            seen[key(r['g'])] = r
        ranked = sorted((seen[key(g)] for g in pop), key=lambda r: -r['fitness'])
        for r in ranked:
            log.append(dict(run=seed, gen=gen, **{k: v for k, v in r.items() if k != 'g'}, genome=key(r['g'])))
        b = ranked[0]; g = b['g']
        print(f"run {seed} gen {gen}: best fitness {b['fitness']:+.3f} (2021 {b.get('r2021', 0) * 100:+.1f}%, "
              f"2022 {b.get('r2022', 0) * 100:+.1f}%, DD {b.get('dd', 0) * 100:.0f}%) {g['model']} stop{g['stop']} "
              f"rr{g['rr']} hold{g['hold'] // 12}h q{g['q']} {g['sides']} {g['feats']} | "
              f"median {np.median([r['fitness'] for r in ranked]):+.3f}", flush=True)
        elites = [r['g'] for r in ranked[:ELITE]]
        nxt = [dict(e) for e in elites]
        while len(nxt) < POP:
            child = mutate(crossover(rng.choice(elites), rng.choice(elites), rng), rng)
            if key(child) not in {key(x) for x in nxt}:
                nxt.append(child)
        pop = nxt
    return sorted(seen.values(), key=lambda r: -r['fitness'])


# --------------------------------------------------------------------------- final test
def vb_trades():
    _init()
    parts = []
    for coin in ('BTC', 'ETH'):
        ctx = st.Ctx(coin + 'USDT', _G['raw'][coin]); ctx.funding = pd.Series(dtype=float)
        o = st.FAMILIES['lab'](ctx, **CB.STRATS['volume_breakout'][1])
        o = o.assign(max_bars=np.minimum(o['max_bars'].to_numpy(), 144))
        tr = simulate(ctx.df, ctx.funding, o, COSTS, coin, 'vb', 0.3); tr['strat'] = 'vb'; parts.append(tr)
    return pd.concat(parts, ignore_index=True)


TEST_YEARS = [('2023', '2023-01-01', '2024-01-01'), ('2024', '2024-01-01', '2025-01-01'),
              ('2025', '2025-01-01', '2026-01-01'), ('2026 Jan-Sep', '2026-01-01', '2026-10-01'),
              ('2023-2026 all', '2023-01-01', '2026-10-01')]


def test_one(job):
    label, g = job
    out = {}
    for stress in (1.0, 1.5):
        tr = run_system(g, VAL_END, VAL_END, TEST_END, stress=stress)
        for y, a, b in TEST_YEARS:
            out[f'{y}|x{stress}'] = year_stats(tr, pd.Timestamp(a, tz='UTC'), pd.Timestamp(b, tz='UTC'))
    return label, out


def main():
    os.makedirs(OUT, exist_ok=True)
    json.dump(dict(train=['2015-07', str(TRAIN_END)], valid=[str(TRAIN_END), str(VAL_END)], test=[str(VAL_END), str(TEST_END)],
                   pop=POP, gens=GENS, elite=ELITE, runs=RUNS, space=SPACE, model_space=MODEL_SPACE,
                   fitness='min(ret2021, ret2022) - 0.5*max DD; -1 if <40 trades in a year', start_inr=100_000,
                   fees=dict(taker=M.TAKER, maker=M.MAKER, slip=M.SLIP), written_at=time.time()),
              open(os.path.join(OUT, 'protocol.json'), 'w'), indent=1, default=str)
    log, finalists = [], {}
    with ProcessPoolExecutor(6, initializer=_init) as pool:
        for seed in RUNS:
            ranked = evolve(pool, seed, log)
            pd.DataFrame(log).to_csv(os.path.join(OUT, 'generations.csv'), index=False)
            for i, r in enumerate([r for r in ranked if r['fitness'] > -9][:3]):
                finalists[f'run{seed}_#{i + 1}'] = r
        json.dump(finalists, open(os.path.join(OUT, 'finalists.json'), 'w'), indent=1, default=float)
        with open(os.path.join(os.path.dirname(OUT), 'holdout_access.log'), 'a') as fh:
            fh.write(f'{pd.Timestamp.now(tz="UTC").isoformat()} ml_evolve TEST 2023-2026 finalists={list(finalists)}\n')
        print(f'\nfinal test on 2023-2026 for {len(finalists)} finalists (retrained on 2015-2022)', flush=True)
        tests = dict(pool.map(test_one, [(k, v['g']) for k, v in finalists.items()]))
    vb = vb_trades()
    vbs = {y: year_stats(vb, pd.Timestamp(a, tz='UTC'), pd.Timestamp(b, tz='UTC')) for y, a, b in TEST_YEARS}
    json.dump(dict(finalists=finalists, tests=tests, vb=vbs), open(os.path.join(OUT, 'test_results.json'), 'w'),
              indent=1, default=float)
    report(log, finalists, tests, vbs)


def report(log, finalists, tests, vbs):
    lg = pd.DataFrame(log)
    L = ['# Evolutionary ML search – results', '',
         f"{lg['genome'].nunique()} unique systems trained across {len(RUNS)} runs x {GENS} generations. "
         'Train 2015–2020, select on 2021–2022, final test 2023–Sep 2026 (once). Rs1L account, BNB fees, Binance minimums.', '',
         '## Best fitness by generation', '', '| Run | ' + ' | '.join(f'Gen {g}' for g in range(1, GENS + 1)) + ' |',
         '|---|' + '---:|' * GENS]
    for run, d in lg.groupby('run'):
        L.append(f'| {run} | ' + ' | '.join(f"{d[d.gen == g]['fitness'].max():+.3f}" for g in range(1, GENS + 1)) + ' |')
    L += ['', '## Finalists: validation (selected on) vs TEST (never seen)', '',
          '| System | Model / rule | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 Jan–Sep | Test 2023–26 total (DD) | Test at 1.5x fees |',
          '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for k, r in finalists.items():
        g, t = r['g'], tests[k]
        desc = (f"{g['model']} {json.dumps(g['mp'])}, stop {g['stop']}ATR, {g['rr']}R, {g['hold'] // 12}h, "
                f"min {g['min_net'] * 100:.1f}%, top {round((1 - g['q']) * 100)}%, {g['sides']}, {g['feats']}")
        cells = [f"{t[f'{y}|x1.0']['ret'] * 100:+.1f}%" for y in ('2023', '2024', '2025', '2026 Jan-Sep')]
        tot, stt = t['2023-2026 all|x1.0'], t['2023-2026 all|x1.5']
        L.append(f"| {k} | {desc} | {r['r2021'] * 100:+.1f}% | {r['r2022'] * 100:+.1f}% | " + ' | '.join(cells) +
                 f" | {tot['ret'] * 100:+.1f}% ({tot['maxdd'] * 100:.0f}%) | {stt['ret'] * 100:+.1f}% |")
    L.append('| Volume breakout (rule, reference) | – | – | – | ' + ' | '.join(
        f"{vbs[y]['ret'] * 100:+.1f}%" for y in ('2023', '2024', '2025', '2026 Jan-Sep')) +
        f" | {vbs['2023-2026 all']['ret'] * 100:+.1f}% ({vbs['2023-2026 all']['maxdd'] * 100:.0f}%) | – |")
    by_model = lg.drop_duplicates('genome').assign(model=lambda d: d['genome'].map(lambda s: json.loads(s)['model']))
    L += ['', '## Which model families did best on validation', '', '| Model | Systems tried | Median fitness | Best fitness |',
          '|---|---:|---:|---:|']
    for m, d in by_model.groupby('model'):
        L.append(f"| {m} | {len(d)} | {d['fitness'].median():+.3f} | {d['fitness'].max():+.3f} |")
    open(os.path.join(OUT, 'REPORT.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L), flush=True)


if __name__ == '__main__':
    main()
