"""Improvement study: longer holds, swing trend-following, ML veto filter, combined account at higher risk.

BTC (Coinbase 2015-) + ETH (spot 2017-), 5m bars. Frozen protocol, single run:
  stage 1  grid of 'trend' configs (volume breakout 1h/4h with holds 12h..30d; Donchian 4h/1D) on 2015-07..2020
  pick     per signal type: highest dev t-stat with enough trades and positive return at 1.5x costs
  stage 2  picks + the live baseline (volume breakout, 12h) on 2021-23 (check), 2024-26 (test) and on 8 alts 2022-26
  ML       walk-forward LightGBM veto of the weakest 30% of volume-breakout signals (features from playgroundML)
  account  combined account at 0.5/1/1.5/2% risk per trade, with and without drawdown control
Funding: real Binance funding from Nov 2021, 0.01% per 8h before that (longs pay). Regular fees + slippage.

  cd playground && ..\\.venv\\Scripts\\python.exe -m daytrade.swing_study
"""
from concurrent.futures import ProcessPoolExecutor
from itertools import product
import json
import os
import time

import numpy as np
import pandas as pd

from . import btc_history, data, research as R, strategies as st, trend  # noqa: F401 (registers 'trend')
from .backtest import costs_for, simulate

OUT = os.path.join(R.OUT, 'swing')
ML_DATA = os.path.normpath(os.path.join(R.OUT, '..', '..', '..', 'playgroundML', 'data', 'ml_dataset.pkl'))
PER = {'s_dev': ('2015-07-01', '2021-01-01'), 's_val': ('2021-01-01', '2024-01-01'),
       's_test': ('2024-01-01', '2026-10-01'), 's_all': ('2015-07-01', '2026-10-01'),
       's_alts': ('2022-01-01', '2026-10-01')}
R.PERIODS.update(PER)
COINS = {'BTCUSDT': btc_history.PATH, 'ETHUSDT': btc_history.ETH_PATH}
ALTS = ('SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'LTCUSDT')
BASELINE = dict(sig='vb', tf='1h', n=20, vol_k=3.0, stop_atr=2.0, trail_atr=3.0, hold_h=12, filt='ema200', sides='both')
MIN_TRADES = {'vb': 100, 'donchian': 30}
RISKS = (0.005, 0.01, 0.015, 0.02)
_C = {}


def grid():
    for tf, vk, tr, hold, filt in product(('1h', '4h'), (2.0, 3.0), (3.0, 5.0), (12, 24, 72, 168, 720),
                                          ('ema200', 'none')):
        yield dict(sig='vb', tf=tf, n=20, vol_k=vk, stop_atr=2.0, trail_atr=tr, hold_h=hold, filt=filt, sides='both')
    for tf, n, tr, hold, filt, sides in product(('4h', '1D'), (20, 55), (3.0, 5.0), (168, 720), ('ema200', 'none'),
                                                ('both', 'long')):
        yield dict(sig='donchian', tf=tf, n=n, vol_k=0.0, stop_atr=2.0, trail_atr=tr, hold_h=hold, filt=filt,
                   sides=sides)


def _funding(sym, idx):
    try:
        real = data.load(sym)[1]
    except Exception:
        real = pd.Series(dtype=float)
    first = real.index[0] if len(real) else idx[-1]
    synth = pd.Series(1e-4, index=pd.date_range(idx[0].ceil('8h'), first - pd.Timedelta(hours=1), freq='8h'))
    return pd.concat([synth, real]).sort_index()


def _ctx(sym):
    if sym not in _C:
        df = pd.read_pickle(COINS[sym]) if sym in COINS else data.load(sym)[0]
        c = st.Ctx(sym, df)
        c.funding = _funding(sym, df.index); c.metrics = None
        _C[sym] = c
    return _C[sym]


def trades(params, period, stress=1.0, symbols=tuple(COINS)):
    out = []
    a, b = (pd.Timestamp(x, tz='UTC') for x in PER[period])
    for sym in symbols:
        ctx = _ctx(sym)
        o = st.FAMILIES['trend'](ctx, **params)
        if o is None or o.empty:
            continue
        t = ctx.idx[np.minimum(o['t'].to_numpy(), len(ctx.idx) - 1)]
        o = o[(t >= a) & (t < b)]
        c = costs_for(sym)
        if stress != 1.0:
            c = c.stressed(stress)
        tr = simulate(ctx.df, ctx.funding, o, c, sym, 'trend', R.MAX_COST_R)
        if len(tr):
            out.append(tr)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def evaluate(job):
    params, period, symbols, with_stress = job
    tr = trades(params, period, symbols=symbols)
    m = R.metrics(tr, period)
    if m['trades'] and with_stress:
        m['stress_ret'] = R.metrics(trades(params, period, 1.5, symbols), period).get('ret', 0.0)
    return params, period, symbols, m


def _pool(jobs, workers=6):
    with ProcessPoolExecutor(workers) as pool:
        yield from pool.map(evaluate, jobs, chunksize=1)


def key(p):
    return json.dumps(p, sort_keys=True)


def label(p):
    if p['sig'] == 'vb':
        s = f"vol breakout {p['tf']}, vol>{p['vol_k']:g}x"
    else:
        s = f"Donchian {p['n']} {p['tf']}" + (' long-only' if p['sides'] == 'long' else '')
    hold = f"{p['hold_h']}h" if p['hold_h'] < 48 else f"{p['hold_h'] // 24}d"
    return f"{s}, trail {p['trail_atr']:g} ATR, max hold {hold}, filter {p['filt']}"


# --------------------------------------------------------------------------- ML veto
def ml_filter(tr, veto=0.3, first_year=2019):
    """Walk-forward: for each year, train on trades closed before it; veto the lowest `veto` share of scores
    (threshold from out-of-fold scores inside the training years). Returns tr with a 'keep' column."""
    import lightgbm as lgb
    ds = pd.read_pickle(ML_DATA)
    feats = [c for c in ds.columns if not c.startswith(('label_', 'net_', 'exit_', 'hold_h_', 'stop_', 'target_',
                                                        'entry_', 'hind_')) and c != 'coin']
    ds = ds.assign(sym=ds['coin'] + 'USDT', ets=ds['entry_time'].astype('int64') // 10**6)
    tr = tr.merge(ds[['sym', 'ets'] + feats], left_on=['symbol', 'entry_ts'], right_on=['sym', 'ets'], how='left')
    print(f'  ML: {tr[feats[0]].notna().mean():.0%} of trades matched to feature rows', flush=True)
    X = tr[feats].astype(float).assign(side=tr['side'].astype(float), is_eth=(tr['symbol'] == 'ETHUSDT').astype(float))
    y = (tr['net_r'] > 0).astype(int).to_numpy()
    yr = pd.to_datetime(tr['entry_ts'], unit='ms', utc=True).dt.year.to_numpy()
    ex = tr['exit_ts'].to_numpy()
    prm = dict(objective='binary', learning_rate=0.03, num_leaves=7, min_data_in_leaf=40, feature_fraction=0.6,
               bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, verbose=-1, seed=3, num_threads=4)
    keep = np.ones(len(tr), dtype=bool); score = np.full(len(tr), np.nan)
    for Y in range(first_year, int(yr.max()) + 1):
        cut = pd.Timestamp(f'{Y}-01-01', tz='UTC').value // 10**6
        trn = np.flatnonzero(ex < cut); tst = np.flatnonzero(yr == Y)
        if len(trn) < 200 or not len(tst):
            continue
        folds = np.array_split(trn, 4); oof = []
        for k in range(1, 4):                                  # expanding inner folds -> honest OOF scores
            a_ = np.concatenate(folds[:k]); m = lgb.train(prm, lgb.Dataset(X.iloc[a_], y[a_]), 150)
            oof.append(m.predict(X.iloc[folds[k]]))
        thr = np.quantile(np.concatenate(oof), veto)
        m = lgb.train(prm, lgb.Dataset(X.iloc[trn], y[trn]), 150)
        score[tst] = m.predict(X.iloc[tst]); keep[tst] = score[tst] >= thr
    out = tr.drop(columns=['sym', 'ets'] + feats)
    out['keep'] = keep; out['ml_score'] = score
    return out


# --------------------------------------------------------------------------- account
def account(tr, period, risk, ddc=False, start=1000.0, lev=3.0, gross=5.0, max_pos=8):
    a, b = (pd.Timestamp(x, tz='UTC') for x in PER[period])
    et = pd.to_datetime(tr['entry_ts'], unit='ms', utc=True)
    tr = tr[(et >= a) & (et < b)].sort_values('entry_ts').reset_index(drop=True)
    eq, peak, open_, rows = start, start, [], []
    for r in tr.itertuples():
        for x in [x for x in open_ if x[0] <= r.entry_ts]:
            eq += x[1]; open_.remove(x); peak = max(peak, eq)
        if len(open_) >= max_pos:
            continue
        dd = 1 - eq / peak
        mult = 1.0 if not ddc or dd < 0.15 else 0.5 if dd < 0.25 else 0.25
        n = min(risk * mult * eq / r.risk_frac, lev * eq)
        if sum(x[2] for x in open_) + n > gross * eq:
            continue
        open_.append((r.exit_ts, n * r.net, n)); rows.append((r.exit_ts, n * r.net))
    if not rows:
        return dict(trades=0)
    days = pd.date_range(a, b - pd.Timedelta(days=1), freq='D').as_unit('ns')
    p = pd.DataFrame(rows, columns=['x', 'pnl'])
    day = pd.to_datetime(p['x'], unit='ms', utc=True).dt.floor('D').astype('datetime64[ns, UTC]')
    daily = p.groupby(day)['pnl'].sum().reindex(days, fill_value=0.0)
    e = start + daily.cumsum()
    yrs = (b - a).days / 365.25
    total = float(e.iloc[-1] / start - 1)
    ye = pd.concat([pd.Series([start], [a - pd.Timedelta(days=1)]), e.resample('YE').last()])
    by_year = (ye.pct_change().dropna() * 100).round(1)
    me = pd.concat([pd.Series([start], [a - pd.Timedelta(days=1)]), e.resample('ME').last()]).pct_change().dropna()
    return dict(trades=len(rows), ret=total, cagr=(1 + total) ** (1 / yrs) - 1 if total > -1 else -1.0,
                maxdd=float(((e.cummax() - e) / e.cummax()).max()), worst_year=float(by_year.min()),
                pos_months=float((me > 0).mean()), by_year={int(k.year): float(v) for k, v in by_year.items()})


# --------------------------------------------------------------------------- main
def main():
    os.makedirs(OUT, exist_ok=True)
    cfg = list(grid())
    json.dump(dict(periods=PER, configs=len(cfg), baseline=BASELINE, min_trades=MIN_TRADES, risks=RISKS,
                   selection='per signal type: highest 2015-2020 t-stat with min trades and 1.5x-cost return > 0',
                   funding='real Binance funding from 2021-11; 0.01%/8h before (longs pay)',
                   ml='LightGBM veto of lowest 30% scores, walk-forward by year from 2019',
                   written_at=time.time()), open(os.path.join(OUT, 'protocol.json'), 'w'), indent=1)
    t0 = time.time()
    print(f'stage 1: {len(cfg)} configs on 2015-07..2020 (BTC+ETH)', flush=True)
    rows = []
    for i, (p, _, _, m) in enumerate(_pool([(p, 's_dev', tuple(COINS), True) for p in cfg]), 1):
        rows.append(dict(key=key(p), sig=p['sig'], label=label(p), **{k: v for k, v in m.items() if not isinstance(v, dict)}))
        if i % 20 == 0:
            print(f'  {i}/{len(cfg)}  {time.time() - t0:.0f}s', flush=True)
    s1 = pd.DataFrame(rows)
    s1.to_csv(os.path.join(OUT, 'stage1.csv'), index=False)
    picks = {}
    for sig, d in s1.groupby('sig'):
        d = d[(d['trades'] >= MIN_TRADES[sig]) & (d['stress_ret'] > 0)].sort_values('t', ascending=False)
        if len(d):
            picks[sig] = json.loads(d.iloc[0]['key'])
    json.dump(picks, open(os.path.join(OUT, 'selection.json'), 'w'), indent=1)
    print('picks:', {k: label(v) for k, v in picks.items()}, flush=True)

    # stage 2: every config on check/test too (to show whether the pick is a plateau or a spike)
    print('stage 2: all configs on 2021-23 and 2024-26; picks + baseline on alts', flush=True)
    named = dict(picks, baseline=BASELINE)
    jobs = [(p, per, tuple(COINS), False) for p in cfg for per in ('s_val', 's_test')]
    jobs += [(p, per, tuple(COINS), True) for p in named.values() for per in ('s_dev', 's_val', 's_test', 's_all')]
    jobs += [(p, 's_alts', ALTS, True) for p in named.values()]
    res = {}
    for p, per, syms, m in _pool(jobs):
        tag = 'alts' if syms == ALTS else 'majors'
        cur = res.setdefault(key(p), {}).get(f'{tag}|{per}', {})
        if 'stress_ret' not in cur:            # keep the stressed evaluation when a config appears twice
            res[key(p)][f'{tag}|{per}'] = m
    json.dump(res, open(os.path.join(OUT, 'stage2.json'), 'w'), indent=1, default=str)

    print('ML veto filter', flush=True)
    ml = {}
    for name in ('vb', 'baseline'):
        if name not in named:
            continue
        f = ml_filter(trades(named[name], 's_all'))
        f.to_csv(os.path.join(OUT, f'ml_{name}_trades.csv'), index=False)
        yr = pd.to_datetime(f['entry_ts'], unit='ms', utc=True).dt.year.rename('year')
        ml[name] = pd.DataFrame({'n': f.groupby(yr).size(), 'n_kept': f[f.keep].groupby(yr[f.keep]).size(),
                                 'all_R': f.groupby(yr)['net_r'].sum(),
                                 'kept_R': f[f.keep].groupby(yr[f.keep])['net_r'].sum(),
                                 'vetoed_R': f[~f.keep].groupby(yr[~f.keep])['net_r'].sum()}).fillna(0).round(1)
        print(name, '\n', ml[name].to_string(), flush=True)

    print('account simulations', flush=True)
    books = {'baseline (live VB 12h)': trades(BASELINE, 's_all')}
    if 'vb' in picks:
        books['VB pick'] = trades(picks['vb'], 's_all')
    if 'donchian' in picks:
        books['Donchian pick'] = trades(picks['donchian'], 's_all')
    if 'vb' in picks and 'donchian' in picks:
        books['VB pick + Donchian pick'] = pd.concat([books['VB pick'], books['Donchian pick']], ignore_index=True)
        if 'vb' in ml:
            fv = pd.read_csv(os.path.join(OUT, 'ml_vb_trades.csv'))
            books['VB pick (ML-filtered) + Donchian pick'] = pd.concat(
                [fv[fv.keep].drop(columns=['keep', 'ml_score']), books['Donchian pick']], ignore_index=True)
    acc = {}
    for bname, tr in books.items():
        for risk in RISKS:
            for ddc in (False, True):
                for per in ('s_dev', 's_val', 's_test', 's_all'):
                    acc[f'{bname}|{risk}|{ddc}|{per}'] = account(tr, per, risk, ddc)
    json.dump(acc, open(os.path.join(OUT, 'accounts.json'), 'w'), indent=1, default=float)
    report(s1, picks, res, ml, acc, list(books))
    print(f'done in {time.time() - t0:.0f}s', flush=True)


def _cell(m):
    if not m or not m.get('trades'):
        return '–'
    s = f"{m['trades']} · {m['ret'] * 100:+.1f}% · PF {m['pf']} · DD {m['maxdd'] * 100:.0f}%"
    return s + (f" · 1.5x {m['stress_ret'] * 100:+.1f}%" if 'stress_ret' in m else '')


def _md(df):
    cols = list(df.columns)
    return '\n'.join(['| ' + ' | '.join(map(str, cols)) + ' |', '|' + '---:|' * len(cols)] +
                     ['| ' + ' | '.join(str(v) for v in row) + ' |' for row in df.itertuples(index=False)])


def report(s1, picks, res, ml, acc, books):
    named = dict(picks, baseline=BASELINE)
    L = ['# Improvement study: longer holds, swing trend, ML veto, combined account', '',
         'BTC + ETH 5m (2015–2026). Settings picked on 2015–2020 only; 2021–2023 = check, 2024–2026 = test. '
         'Alts (SOL XRP BNB DOGE ADA AVAX LINK LTC, 2022–2026) never used for picking. Regular Binance fees, '
         'slippage and funding. Cells: trades · return · profit factor · max DD · return at 1.5x costs '
         '(0.5% risk per trade).', '',
         '## Picked settings vs the live baseline', '',
         '| | Setting | 2015–2020 (pick) | 2021–2023 (check) | 2024–2026 (test) | Alts 2022–2026 |', '|---|---|---|---|---|---|']
    for name, p in named.items():
        r = res.get(key(p), {})
        L.append(f"| {name} | {label(p)} | {_cell(r.get('majors|s_dev'))} | {_cell(r.get('majors|s_val'))} | "
                 f"{_cell(r.get('majors|s_test'))} | {_cell(r.get('alts|s_alts'))} |")
    L += ['', '## Is the pick a plateau? All configs by group', '',
          '| Group | Configs | 2015–2020 profitable | 2021–2023 profitable | 2024–2026 profitable | Median 2021–23 | Median 2024–26 |',
          '|---|---:|---:|---:|---:|---:|---:|']
    s1 = s1.assign(val=[res.get(k, {}).get('majors|s_val', {}).get('ret', np.nan) for k in s1['key']],
                   test=[res.get(k, {}).get('majors|s_test', {}).get('ret', np.nan) for k in s1['key']],
                   hold=[json.loads(k)['hold_h'] for k in s1['key']], tf=[json.loads(k)['tf'] for k in s1['key']])
    for (sig, tf, hold), d in s1.groupby(['sig', 'tf', 'hold']):
        L.append(f"| {sig} {tf} hold {hold}h | {len(d)} | {(d['ret'] > 0).mean():.0%} | {(d['val'] > 0).mean():.0%} | "
                 f"{(d['test'] > 0).mean():.0%} | {d['val'].median() * 100:+.1f}% | {d['test'].median() * 100:+.1f}% |")
    for name, t in ml.items():
        L += ['', f'## ML veto on {name} (sum of R per year; vetoed = trades the model removed)', '',
              _md(t.reset_index())]
    L += ['', '## Combined account (1,000 USDT start, compounding; DD control = halve risk below -15%, quarter below -25%)', '',
          '| Book | Risk/trade | DD control | 2015–2020 CAGR (DD) | 2021–2023 CAGR (DD) | 2024–2026 CAGR (DD) | Worst year 2021–26 |',
          '|---|---:|---|---:|---:|---:|---:|']
    cell = lambda m: f"{m['cagr'] * 100:+.1f}% ({m['maxdd'] * 100:.0f}%)" if m.get('trades') else '–'
    for bname in books:
        for risk in RISKS:
            for ddc in (False, True):
                c = [acc[f'{bname}|{risk}|{ddc}|{per}'] for per in ('s_dev', 's_val', 's_test')]
                al = acc[f'{bname}|{risk}|{ddc}|s_all']
                worst = min([v for y, v in al.get('by_year', {}).items() if y >= 2021] or [float('nan')])
                L.append(f"| {bname} | {risk * 100:.1f}% | {'on' if ddc else 'off'} | " + ' | '.join(cell(m) for m in c)
                         + f' | {worst:+.1f}% |')
    open(os.path.join(OUT, 'REPORT.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L), flush=True)


if __name__ == '__main__':
    main()
