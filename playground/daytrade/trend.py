"""Trend family without the 12h day-trading cap: volume breakout and Donchian breakouts on 1h / 4h / 1D bars.

Same execution as the indicator lab (market entry at the next 5m open, ATR stop, chandelier trail fixed at
entry), but the stop, trail and maximum hold are parameters so winners can run for days.

Registered as strategy family 'trend' with params: sig, tf, n, vol_k, stop_atr, trail_atr, hold_h, filt, sides.
"""
import numpy as np
import pandas as pd

from . import indicators as ind
from . import strategies as st

NAN = float('nan')


def _signals(b, sig, n, vol_k):
    c, h, l, v = b['close'], b['high'], b['low'], b['volume']
    hh, ll = h.rolling(n).max().shift(), l.rolling(n).min().shift()
    if sig == 'vb':                      # n-bar high/low close on volume > vol_k x 20-bar average
        vol_ok = v > vol_k * v.rolling(20).mean().shift()
        return (c > hh) & vol_ok, (c < ll) & vol_ok
    if sig == 'donchian':                # first close beyond the n-bar channel
        return (c > hh) & (c.shift() <= hh.shift()), (c < ll) & (c.shift() >= ll.shift())
    raise ValueError(sig)


def trend(ctx, sig='vb', tf='1h', n=20, vol_k=3.0, stop_atr=2.0, trail_atr=3.0, hold_h=12, filt='ema200',
          sides='both'):
    b = ctx.bars(tf)
    long, short = ctx.get(('trendsig', sig, tf, n, vol_k), lambda: _signals(b, sig, n, vol_k))
    a = ctx.get(('trendatr', tf), lambda: ind.atr(b, 14))
    if filt in ('ema200', 'ema50'):
        span = int(filt[3:])
        e = ctx.get(('trendema', tf, span), lambda: b['close'].ewm(span=span, adjust=False, min_periods=span).mean())
        long, short = long & (b['close'] > e), short & (b['close'] < e)
    step = b.index[1] - b.index[0]
    out = []
    for side, cond in ((1, long), (-1, short)):
        if (sides == 'long' and side < 0) or (sides == 'short' and side > 0):
            continue
        cond = cond.fillna(False).astype(bool)
        s = b[cond]
        if s.empty:
            continue
        aa = a[cond].to_numpy(); cc = s['close'].to_numpy()
        t = ctx.pos(s.index + step)
        out.append(st._orders(t, side, 'market', NAN, 1, cc - side * stop_atr * aa, NAN, int(hold_h * 12), NAN,
                              trail_atr * aa))
    return pd.concat(out) if out else None


st.FAMILIES['trend'] = trend
