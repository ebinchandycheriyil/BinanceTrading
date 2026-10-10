"""Indicator lab pipeline (frozen protocol, single run). Run from the playground folder.

Stage 1: every indicator x setting x timeframe x exit x filter on development (2022-01..2024-07),
         original 10 coins, base costs.
Stage 2: the best setting per indicator (highest t with >= 150 trades) plus the two squeeze strategies,
         re-tested with 1.5x costs on development, on validation (2024-07..2025-07, original coins) and on
         29 coins never used for any selection (2022-01..2026-10). Unseen-coin access is logged.

  cd playground && ..\\.venv\\Scripts\\python.exe -m daytrade.lab_run
"""
import json
import os
import time

import pandas as pd

from . import data, lab, research as R

OUT = os.path.join(R.OUT, 'lab')
MIN_TRADES = 150


def _ok(m):
    return (m.get('trades', 0) >= 40 and m.get('ret', -1) > 0 and (m.get('pf') or 0) >= 1.10
            and m.get('stress_ret', -1) > 0)


def _verdict(dev, val, oos):
    if R._qualifies(dev) and _ok(val) and _ok(oos):
        return 'WORKS'
    if dev.get('ret', -1) > 0 and (val.get('ret', -1) > 0 or oos.get('ret', -1) > 0):
        return 'PROMISING'
    return 'FAILS'


def main():
    os.makedirs(OUT, exist_ok=True)
    ext = json.load(open(R.EXTENDED))
    configs = list(lab.grid())
    json.dump(dict(stage1=f'{len(configs)} configs: {len(lab.INDICATORS)} indicators x 2 settings x tf(15min,1h) '
                          f'x exit{list(lab.EXITS)} x filter(none, ema200); 12h max hold',
                   selection=f'per indicator: highest t-stat with >= {MIN_TRADES} dev trades',
                   verdict='WORKS = dev gates (research.SELECT) + validation and unseen coins each: >=40 trades, '
                           'return > 0, PF >= 1.10, 1.5x-cost return > 0. PROMISING = dev profitable and '
                           'profitable in validation or unseen coins at base cost.',
                   exits=lab.EXITS, unseen_coins=ext, written_at=time.time()),
              open(os.path.join(OUT, 'protocol.json'), 'w'), indent=1, default=str)

    print(f'stage 1: {len(configs)} configs', flush=True)
    rows = []
    for i, (f, p, m) in enumerate(R._pool_run([('lab', p, 'dev', data.UNIVERSE, False) for p in configs]), 1):
        rows.append(dict(p, **{k: v for k, v in m.items() if not isinstance(v, dict)},
                         by_year=json.dumps(m.get('by_year', {}))))
        if i % 20 == 0:
            print(f'  {i}/{len(configs)}', flush=True)
    s1 = pd.DataFrame(rows)
    s1.to_csv(os.path.join(OUT, 'stage1.csv'), index=False)

    picks = {}
    for name, g in s1.groupby('ind'):
        g = g[g['trades'] >= MIN_TRADES].sort_values('t', ascending=False)
        if len(g):
            r = g.iloc[0]
            picks[name] = ('lab', {k: (int(r[k]) if k == 'var' else r[k]) for k in ('ind', 'var', 'tf', 'exit', 'filt')})
    picks['squeeze_oi (48h)'] = ('squeeze_oi', R._load('selection_before_validation_r3b.json')['squeeze_oi']['params'])
    picks['squeeze_day (12h)'] = ('squeeze_day', R._load('selection_before_validation_r4.json')['squeeze_day']['params'])
    json.dump(picks, open(os.path.join(OUT, 'selection.json'), 'w'), indent=1)

    with open(os.path.join(R.OUT, 'holdout_access.log'), 'a') as fh:
        fh.write(f'{pd.Timestamp.now(tz="UTC").isoformat()} OOS-COINS lab stage2 picks={list(picks)} coins={len(ext)}\n')
    print(f'stage 2: {len(picks)} candidates', flush=True)
    jobs, keys = [], []
    for label, (fam, p) in picks.items():
        coins = [c for c in ext if fam == 'lab' or os.path.exists(os.path.join(data.ROOT, f'{c}_metrics.pkl'))]
        for period, syms in (('dev', data.UNIVERSE), ('val', data.UNIVERSE), ('all', coins)):
            jobs.append((fam, p, period, syms, True)); keys.append((label, period))
    res = {}
    for (label, period), (f, p, m) in zip(keys, R._pool_run(jobs)):
        res.setdefault(label, dict(family=f, params=p))[period] = m
    for r in res.values():
        r['verdict'] = _verdict(r['dev'], r['val'], r['all'])
    json.dump(res, open(os.path.join(OUT, 'stage2.json'), 'w'), indent=1, default=str)
    write_report(s1, res)


def _fmt(m):
    if not m.get('trades'):
        return '0 | – | – | –'
    return f"{m['trades']} · {m['ret'] * 100:+.1f}% · PF {m['pf']} · stress {m.get('stress_ret', 0) * 100:+.1f}%"


def write_report(s1, res):
    L = ['# Indicator lab results', '',
         'All trades: market entry at the next 5m bar, 12h max hold, Binance regular fees + slippage + funding. '
         'Returns use 0.5% equity risk per trade on 1,000 USDT. Cells: trades · return · profit factor · return at 1.5x costs.', '',
         '| Indicator | Best setting (picked on dev) | Win rate (dev) | Development 2022–24, 10 coins | Validation 2024–25, 10 coins | Unseen 29 coins 2022–26 | Verdict |',
         '|---|---|---:|---|---|---|---|']
    rank = {'WORKS': 0, 'PROMISING': 1, 'FAILS': 2}
    for k in sorted(res, key=lambda k: (rank[res[k]['verdict']], -(res[k]['all'].get('ret') or -9))):
        r = res[k]; p = r['params']
        setting = (f"{lab.VARIANTS[p['ind']][p['var']]}, {p['tf']}, {p['exit']}, filter {p['filt']}"
                   if r['family'] == 'lab' else 'frozen earlier')
        L.append(f"| {k} | {setting} | {r['dev'].get('win', 0) * 100:.0f}% | {_fmt(r['dev'])} | {_fmt(r['val'])} | "
                 f"{_fmt(r['all'])} | **{r['verdict']}** |")
    L += ['', f'## Which settings help (stage 1: all {len(s1)} configs, development period)', '',
          '| Setting | Value | Configs | Share profitable | Median avg R per trade | Median trades |',
          '|---|---|---:|---:|---:|---:|']
    for col in ('tf', 'exit', 'filt', 'var'):
        for val, g in s1.groupby(col):
            L.append(f"| {col} | {val} | {len(g)} | {(g['ret'] > 0).mean() * 100:.0f}% | "
                     f"{g['avg_r'].median():+.3f} | {int(g['trades'].median())} |")
    L += ['', '## Per indicator, all 16 configs (development)', '',
          '| Indicator | Profitable configs | Best return | Median return |', '|---|---:|---:|---:|']
    for name, g in s1.groupby('ind'):
        L.append(f"| {name} | {(g['ret'] > 0).sum()}/{len(g)} | {g['ret'].max() * 100:+.1f}% | {g['ret'].median() * 100:+.1f}% |")
    L += ['', '## High win rate is not profit', '', '| Config | Win rate | Avg R | Return |', '|---|---:|---:|---:|']
    for _, r in s1.sort_values('win', ascending=False).head(5).iterrows():
        L.append(f"| {r['ind']} {lab.VARIANTS[r['ind']][int(r['var'])]} {r['tf']} {r['exit']} {r['filt']} | "
                 f"{r['win'] * 100:.0f}% | {r['avg_r']:+.3f} | {r['ret'] * 100:+.1f}% |")
    L += ['', f"Stage 1 profitable configs: {(s1['ret'] > 0).sum()} / {len(s1)}.", '',
          'Full data: `stage1.csv`, `stage2.json`, `selection.json`, `protocol.json`.']
    open(os.path.join(OUT, 'REPORT.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
