"""Summary table of every paper bot: live return at 0.5 / 0.75 / 1% risk vs what the backtest expected so far.

  ~/trade/venv/bin/python ~/trade/deploy/vps/results.py            (on the VM)
  .\\results.ps1                                                    (from the laptop, via ssh)

Returns are on a 1,000 USDT paper account, compounding, same sizing rules as the backtests
(research.portfolio: risk per trade on realised equity, max 6 positions, 2x leverage cap).
"""
import json
import os
import sys

import pandas as pd

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'playground'))
from daytrade import research  # noqa: E402

RISKS = (0.005, 0.0075, 0.01)
# name, run folder, backtest yearly return at 0.5% risk on later (unseen or test) data, where that number comes from
BOTS = [
    ('Donchian 4h (BTC+ETH)', 'playground/artifacts/daytrade/live_donchian4h', 9.7, '2022-26'),
    ('Vol breakout 4h (BTC+ETH)', 'playground/artifacts/daytrade/live_vb4h', 12.0, '2022-26'),
    ('Vol breakout 1h (BTC+ETH)', 'playground/artifacts/daytrade/live_volume_breakout', 12.0, '2023-26'),
    ('Vol breakout 1h (8 alts)', 'playground/artifacts/daytrade/live_volume_breakout_alts', 0.0, 'unseen coins'),
    ('ML RandomForest', 'playgroundML/artifacts/ml/live_rf', 11.1, '2023-26'),
    ('ML LightGBM #1', 'playgroundML/artifacts/ml/live_lgbm1', 14.0, '2023-26'),
    ('ML LightGBM #3', 'playgroundML/artifacts/ml/live_lgbm3', 11.0, '2023-26'),
    ('squeeze_oi (39 coins)', 'artifacts/daytrade/live_squeeze_oi', 2.9, 'holdout'),
    ('Funding carry (Rs10k)', 'playground/artifacts/daytrade/live_carry', 6.0, '2022-26'),
]


def returns(run):
    """Live % return at each risk level from the closed trades; (returns, trades, win rate)."""
    p = os.path.join(run, 'closed_trades.csv')
    if not os.path.exists(p):
        return None, 0, None
    tr = pd.read_csv(p)
    if 'open' in tr:
        tr = tr[~tr['open'].astype(bool)]
    if tr.empty:
        return None, 0, None
    out = []
    for r in RISKS:
        research.RISK = r
        taken, _ = research.portfolio(tr)
        out.append(float(taken['pnl'].sum()) / 1000 * 100)
    return out, len(tr), float((tr['net_r'] > 0).mean())


def pct(v):
    return '–' if v is None else f'{v:+.2f}%'


def main():
    rows = []
    for name, rel, bt_year, src in BOTS:
        run = os.path.join(ROOT, rel)
        if not os.path.exists(os.path.join(run, 'status.json')):
            continue
        st = json.load(open(os.path.join(run, 'status.json')))
        days = st.get('days') or st.get('hours_running', 0) / 24
        if 'return_pct' in st:                       # carry bot keeps its own INR equity
            live, n, win = [float(st['return_pct'])] * 3, '', ''
        else:
            live, n, win = returns(run)
            win = '' if win is None else f'{win:.0%}'
        live = live or [None] * 3
        opn = len(st.get('open_positions') or st.get('holding') or [])
        rows.append([name, f'{days:.1f}', str(n), win, str(opn), pct(live[0]), pct(live[1]), pct(live[2]),
                     pct(bt_year * days / 365), f'{bt_year:+.1f}%', src])
        upd = st.get('updated', '')[:16].replace('T', ' ')
    head = ['Bot', 'Days', 'Trades', 'Win', 'Open', 'Live 0.5%', 'Live 0.75%', 'Live 1%',
            'Backtest so far', 'Backtest/yr', 'Source']
    w = [max(len(str(r[i])) for r in rows + [head]) for i in range(len(head))]
    line = lambda r: '  '.join(str(v).ljust(w[i]) if i == 0 else str(v).rjust(w[i]) for i, v in enumerate(r))
    print(f'Paper results on a 1,000 USDT account (updated {upd} UTC)\n')
    print(line(head)); print('-' * len(line(head)))
    for r in rows:
        print(line(r))
    print('\nBacktest so far = backtest yearly return at 0.5% risk x days running / 365 (what the backtest expects by now).'
          '\nUntil ~3 months / 30+ trades per bot the live numbers are mostly noise. Carry shows its own return in every column.')


if __name__ == '__main__':
    main()
