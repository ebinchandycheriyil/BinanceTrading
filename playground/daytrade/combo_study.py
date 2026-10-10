"""Tests 1 + 4: fee scenarios and a portfolio of weak day-trading edges on BTC + ETH, at real account sizes.

Strategies are frozen from the BTC 2015-2026 study (no re-tuning). ETH was not used for selection.
Account simulation uses Binance USD-M minimums (BTC: 0.001 step / $50 min notional, ETH: 0.001 / $20),
0.5% risk per trade; a position below the minimum is raised to it only if risk stays <= 2%; one position
per coin at a time; notional <= 3x equity. Each period starts a fresh account.

  cd playground && ..\\.venv\\Scripts\\python.exe -m daytrade.combo_study
"""
from concurrent.futures import ProcessPoolExecutor
import json
import math
import os

import numpy as np
import pandas as pd

from . import btc_history, research as R, strategies as st
from .backtest import Costs, simulate

OUT = os.path.join(R.OUT, 'combo')
CAP = 144
USDINR = 96.38                       # open.er-api.com, 2026-10-02
ACCOUNTS = {'Rs10k': 10_000, 'Rs1L': 100_000, 'Rs5L': 500_000}
COINS = {'BTC': (btc_history.PATH, 0.001, 50.0), 'ETH': (btc_history.ETH_PATH, 0.001, 20.0)}
STRATS = {
    'volume_breakout': ('lab', dict(ind='volume_breakout', var=1, tf='1h', exit='trail', filt='ema200')),
    'bb_breakout': ('lab', dict(ind='bb_breakout', var=1, tf='1h', exit='trail', filt='ema200')),
    'squeeze': ('squeeze', dict(kc=1.5, min_sq=4, vol_mult=1.5, stop_atr=2.5, target_r=3.0, hold_h=24)),
}
FEES = {'regular': Costs(taker=5e-4, maker=2e-4, slip=1.5e-4, funding=False),
        'bnb': Costs(taker=4.5e-4, maker=1.8e-4, slip=1.5e-4, funding=False)}
SCEN = {'regular fees, market entry': ('regular', False), 'BNB fees, market entry': ('bnb', False),
        'BNB fees, maker (limit) entry': ('bnb', True)}
PERIODS = {'2015-2020': ('2015-07-01', '2021-01-01'), '2021-2023': ('2021-01-01', '2024-01-01'),
           '2024-2026': ('2024-01-01', '2026-10-01')}
MAX_RISK, LEV, RISK = 0.02, 3.0, 0.005
_C = {}


def _ctx(coin):
    if coin not in _C:
        c = st.Ctx(coin + 'USDT', pd.read_pickle(COINS[coin][0]))
        c.funding = pd.Series(dtype=float); c.metrics = None
        _C[coin] = c
    return _C[coin]


def trades(job):
    strat, coin, fee, maker_entry = job
    fam, p = STRATS[strat]
    ctx = _ctx(coin)
    o = st.FAMILIES[fam](ctx, **p)
    o = o.assign(max_bars=np.minimum(o['max_bars'].to_numpy(), CAP))
    if maker_entry:     # resting limit at the signal bar's close for two 5m bars
        close = ctx.df['close'].to_numpy()
        o = o.assign(kind='limit', price=close[np.maximum(o['t'].to_numpy() - 1, 0)], expire=2)
    tr = simulate(ctx.df, ctx.funding, o, FEES[fee], coin, strat, R.MAX_COST_R)
    tr['strat'] = strat
    return job, tr


def account(tr, start_inr, risk=RISK):
    """Sequential account with exchange minimums; returns taken trades with USD pnl."""
    eq = start_inr / USDINR
    tr = tr.sort_values('entry_ts').reset_index(drop=True)
    busy, open_, rows = {}, [], []
    for r in tr.itertuples():
        for x in [x for x in open_ if x[0] <= r.entry_ts]:
            eq += x[1]; open_.remove(x)
        if busy.get(r.symbol, 0) > r.entry_ts:
            continue
        step, mn = COINS[r.symbol][1], COINS[r.symbol][2]
        notional = min(risk * eq / r.risk_frac, LEV * eq)
        qty = math.floor(notional / r.entry / step) * step
        smallest = max(mn, step * r.entry)
        if qty * r.entry < smallest:
            qty = math.ceil(smallest / r.entry / step - 1e-9) * step
            if qty * r.entry * r.risk_frac > MAX_RISK * eq or qty * r.entry > LEV * eq:
                continue
        pnl = qty * r.entry * r.net
        busy[r.symbol] = r.exit_ts
        open_.append((r.exit_ts, pnl))
        rows.append((r.entry_ts, r.exit_ts, r.symbol, r.strat, qty * r.entry, pnl, qty * r.entry * r.risk_frac / eq))
    return pd.DataFrame(rows, columns=['entry_ts', 'exit_ts', 'coin', 'strat', 'notional', 'pnl_usd', 'risk_used'])


def stats(trades_df, start_inr, period):
    a, b = (pd.Timestamp(x, tz='UTC') for x in PERIODS[period])
    et = pd.to_datetime(trades_df['entry_ts'], unit='ms', utc=True)
    t = account(trades_df[(et >= a) & (et < b)], start_inr)
    if t.empty:
        return dict(trades=0)
    start = start_inr / USDINR
    day = pd.to_datetime(t['exit_ts'], unit='ms', utc=True).dt.floor('D').astype('datetime64[ns, UTC]')
    daily = t.groupby(day)['pnl_usd'].sum().reindex(
        pd.date_range(a, b - pd.Timedelta(days=1), freq='D').as_unit('ns'), fill_value=0.0)
    eq = start + daily.cumsum()
    ret = daily / eq.shift(fill_value=start)
    months = pd.concat([pd.Series([start]), eq.resample('ME').last().reset_index(drop=True)]).pct_change().dropna()
    yrs = (b - a).days / 365.25
    total = float(eq.iloc[-1] / start - 1)
    return dict(trades=len(t), ret=total, cagr=(1 + total) ** (1 / yrs) - 1 if total > -1 else -1.0,
                maxdd=float(((eq.cummax() - eq) / eq.cummax()).max()),
                sharpe=float(ret.mean() / ret.std() * math.sqrt(365)) if ret.std() > 0 else 0.0,
                pos_months=float((months > 0).mean()), end_inr=start_inr * (1 + total),
                avg_risk=float(t['risk_used'].mean()), by_strat=t.groupby('strat')['pnl_usd'].sum().round(2).to_dict())


def _cell(s, kind):
    if not s.get('trades'):
        return '–'
    if kind == 'ret':
        return f"{s['ret'] * 100:+.1f}% (DD {s['maxdd'] * 100:.0f}%)"
    return f"{s['cagr'] * 100:+.1f}%/yr · Sharpe {s['sharpe']:.2f} · {s['pos_months'] * 100:.0f}% months up"


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [(s, c, f, m) for s in STRATS for c in COINS for f, m in SCEN.values()]
    with ProcessPoolExecutor(6) as pool:
        tr = dict(pool.map(trades, jobs))
    names = list(STRATS) + ['PORTFOLIO (all 3)']

    def pick(name, fee, mk):
        return pd.concat([tr[(s, c, fee, mk)] for s in (STRATS if name.startswith('PORT') else [name]) for c in COINS],
                         ignore_index=True)
    res = {}
    L = ['# Fees, portfolio and account-size test (BTC + ETH, day trades <= 12h)', '',
         f'Frozen strategies from the BTC study; ETH not used for selection. USD/INR {USDINR}. ETH data starts '
         'Dec 2017, so 2015–2020 is mostly BTC. Each period starts a fresh account; returns are for the whole period.', '',
         '## Test 1 – fee scenarios (Rs1L account, BTC + ETH)', '',
         '| Strategy | Fees / entry | ' + ' | '.join(PERIODS) + ' |', '|---|---|' + '---:|' * len(PERIODS)]
    for name in names:
        for label, (fee, mk) in SCEN.items():
            df = pick(name, fee, mk)
            o = {p: stats(df, 100_000, p) for p in PERIODS}
            res[f'{name}|{label}'] = o
            L.append(f'| {name} | {label} | ' + ' | '.join(_cell(o[p], 'ret') for p in PERIODS) + ' |')
    best = max(SCEN, key=lambda l: sum(res[f'PORTFOLIO (all 3)|{l}'][p].get('ret', -1) for p in ('2021-2023', '2024-2026')))
    L += ['', f'## Test 4 – single strategy vs portfolio ({best}, Rs1L)', '',
          '| Strategy | ' + ' | '.join(PERIODS) + ' |', '|---|' + '---:|' * len(PERIODS)]
    for name in names:
        o = res[f'{name}|{best}']
        L.append(f'| {name} | ' + ' | '.join(_cell(o[p], 'sharpe') for p in PERIODS) + ' |')
    port = pick('PORTFOLIO', *SCEN[best])
    L += ['', f'## Account size – portfolio, {best}', '',
          '| Start | Period | Trades taken | Return | Max DD | Ends at | Avg risk per trade |', '|---|---|---:|---:|---:|---:|---:|']
    for acc, inr in ACCOUNTS.items():
        for p in PERIODS:
            s = stats(port, inr, p)
            res[f'account|{acc}|{p}'] = s
            if s.get('trades'):
                L.append(f"| {acc} | {p} | {s['trades']} | {s['ret'] * 100:+.1f}% | {s['maxdd'] * 100:.1f}% | "
                         f"Rs{s['end_inr']:,.0f} | {s['avg_risk'] * 100:.2f}% |")
    json.dump(res, open(os.path.join(OUT, 'results.json'), 'w'), indent=1, default=float)
    open(os.path.join(OUT, 'REPORT.md'), 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('\n'.join(L))


if __name__ == '__main__':
    main()
