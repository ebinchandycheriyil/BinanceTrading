"""Test 2: delta-neutral funding carry (long spot + short perpetual) at small account sizes.

At each funding settlement a coin is held if the trailing mean of its last `n` settled rates is above
`enter` (exit when below `exit_`). At most `k` coins, each gets equity/k: half buys spot, half is 1x perp
collateral, so hedged notional = equity / (2k). Income = settled funding x notional (short receives positive
funding, pays negative). Costs per round trip: spot taker 0.1% x2, perp taker 0.05% x2, 2 bp slippage x4
(BNB scenario: spot 0.075%, perp 0.045%). Basis drift, ADL and exchange risk are NOT modelled.

  cd playground && ..\\.venv\\Scripts\\python.exe -m daytrade.carry_study
"""
from itertools import product
import json
import os

import numpy as np
import pandas as pd

from . import data, research as R

OUT = os.path.join(R.OUT, 'carry')
USDINR = 96.38
MIN_NOTIONAL = {'BTCUSDT': 86.0, 'ETHUSDT': 20.0}          # others: $5 (spot and perp)
FEES = {'regular': 2 * 0.001 + 2 * 0.0005 + 4 * 0.0002, 'bnb': 2 * 0.00075 + 2 * 0.00045 + 4 * 0.0002}
PERIODS = {'2022-mid2024 (pick)': ('2022-01-01', '2024-07-01'), 'mid2024-mid2025 (check)': ('2024-07-01', '2025-07-01'),
           'mid2025-2026 (test)': ('2025-07-01', '2026-10-01')}
GRID = dict(n=[3, 9], enter=[0.0001, 0.0002, 0.0003], exit_=[0.0, 0.00005], k=[1, 3])


def load_rates():
    coins = json.load(open(R.EXTENDED)) + data.UNIVERSE
    cols = {}
    for c in coins:
        p = os.path.join(data.ROOT, f'{c}_funding.pkl')
        if os.path.exists(p):
            f = pd.read_pickle(p)
            cols[c] = f[~f.index.duplicated()]
    return pd.DataFrame(cols).sort_index()


def signals(rates, n):
    # each coin's own trailing mean, carried forward between its settlements
    return pd.DataFrame({c: rates[c].dropna().rolling(n).mean() for c in rates}).reindex(rates.index).ffill()


def run(rates, sig, start, end, n, enter, exit_, k, fee, start_inr):
    a, b = pd.Timestamp(start, tz='UTC'), pd.Timestamp(end, tz='UTC')
    idx = rates.index[(rates.index >= a) & (rates.index < b)]
    eq = start_inr / USDINR
    held = {}            # coin -> hedged notional
    curve, income, costs, entries = [], 0.0, 0.0, 0
    R_ = rates.loc[idx].to_numpy(); S_ = sig.loc[idx].to_numpy(); cols = list(rates.columns)
    for i, ts in enumerate(idx):
        for c, nt in held.items():          # settle funding on open hedges
            v = R_[i, cols.index(c)]
            if not np.isnan(v):
                eq += v * nt; income += v * nt
        s = dict(zip(cols, S_[i]))
        for c in [c for c in held if not (s[c] > exit_)]:            # exits
            cost = FEES[fee] / 2 * held.pop(c); eq -= cost; costs += cost
        if len(held) < k:
            cand = sorted((v, c) for c, v in s.items() if v > enter and c not in held)[::-1]
            for v, c in cand[:k - len(held)]:
                nt = eq / (2 * k)
                if nt < MIN_NOTIONAL.get(c, 5.0):
                    continue
                cost = FEES[fee] / 2 * nt; eq -= cost; costs += cost
                held[c] = nt; entries += 1
        curve.append(eq)
    for nt in held.values():
        cost = FEES[fee] / 2 * nt; eq -= cost; costs += cost
    if not curve:
        return dict(ret=0.0, apr=0.0, maxdd=0.0, entries=0, income_pct=0.0, cost_pct=0.0, end_inr=start_inr)
    cv = pd.Series(curve)
    yrs = (b - a).days / 365.25
    base = start_inr / USDINR
    total = eq / base - 1
    return dict(ret=total, apr=(1 + total) ** (1 / yrs) - 1, maxdd=float(((cv.cummax() - cv) / cv.cummax()).max()),
                entries=entries, income_pct=income / base, cost_pct=costs / base, end_inr=start_inr * (1 + total))


def main():
    os.makedirs(OUT, exist_ok=True)
    rates = load_rates()
    sigs = {n: signals(rates, n) for n in GRID['n']}
    keys = list(GRID)
    pick = list(PERIODS)[0]
    rows = []
    for vals in product(*GRID.values()):
        p = dict(zip(keys, vals))
        rows.append(dict(p, **run(rates, sigs[p['n']], *PERIODS[pick], fee='bnb', start_inr=100_000, **p)))
    g = pd.DataFrame(rows).sort_values('apr', ascending=False)
    g.to_csv(os.path.join(OUT, 'grid.csv'), index=False)
    best = {k: (int(v) if k in ('n', 'k') else float(v)) for k, v in g.iloc[0][keys].items()}
    L = ['# Funding carry (long spot + short perp), market-neutral', '',
         f"{len(rates.columns)} coins with funding history. Picked on {pick} (BNB fees, Rs1L): trailing "
         f"{best['n']} payments, enter > {best['enter'] * 100:.3f}%/payment, exit < {best['exit_'] * 100:.4f}%, "
         f"up to {best['k']} coin(s). Grid: {len(g)} configs, {(g['ret'] > 0).sum()} profitable in the pick period.", '',
         '| Account | Fees | Period | Return | Annualised | Max DD | Funding earned | Fees paid | Entries | Ends at |',
         '|---|---|---|---:|---:|---:|---:|---:|---:|---:|']
    res = {}
    for acc, inr in (('Rs10k', 10_000), ('Rs1L', 100_000), ('Rs5L', 500_000)):
        for fee in ('regular', 'bnb'):
            for per, (a, b) in PERIODS.items():
                m = run(rates, sigs[best['n']], a, b, fee=fee, start_inr=inr, **best)
                res[f'{acc}|{fee}|{per}'] = m
                L.append(f"| {acc} | {fee} | {per} | {m['ret'] * 100:+.1f}% | {m['apr'] * 100:+.1f}%/yr | "
                         f"{m['maxdd'] * 100:.1f}% | {m['income_pct'] * 100:+.1f}% | {m['cost_pct'] * 100:.1f}% | "
                         f"{m['entries']} | Rs{m['end_inr']:,.0f} |")
    L += ['', 'Not modelled: spot/perp basis moves while hedged, exchange/counterparty risk, auto-deleveraging, '
          'whether each coin has a Binance spot market, and Indian tax. Returns = funding income minus trading fees.']
    json.dump(dict(best=best, results=res), open(os.path.join(OUT, 'results.json'), 'w'), indent=1, default=float)
    open(os.path.join(OUT, 'REPORT.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
