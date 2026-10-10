"""Indicator lab: popular day-trader indicators under one standard entry/exit framework.

Every indicator yields long/short signals at the close of a completed bar on its chart timeframe.
All share the same execution: market entry at the next 5m open, ATR stop, either a fixed-R target
or an ATR trailing stop, a 12h maximum hold, and an optional EMA-200 trend filter. This isolates the
value of the SIGNAL from the exit design.

Registered as strategy family 'lab' with params: ind, var, tf, exit, filt.
"""
import numpy as np
import pandas as pd

from . import indicators as ind
from . import strategies as st

NAN = float('nan')


def _cross_up(a, b):
    return (a > b) & (a.shift() <= b.shift())


def _hh(s, n):
    return s.rolling(n).max()


def _ll(s, n):
    return s.rolling(n).min()


def _supertrend(b, n, mult):
    a = ind.atr(b, n).to_numpy(); h, l, c = (b[k].to_numpy() for k in ('high', 'low', 'close'))
    mid = (h + l) / 2
    up, dn = mid - mult * a, mid + mult * a
    trend = np.zeros(len(c), dtype=int); fu, fd = up.copy(), dn.copy()
    for i in range(1, len(c)):
        if np.isnan(a[i]) or np.isnan(a[i - 1]):
            continue
        fu[i] = max(up[i], fu[i - 1]) if c[i - 1] > fu[i - 1] else up[i]
        fd[i] = min(dn[i], fd[i - 1]) if c[i - 1] < fd[i - 1] else dn[i]
        trend[i] = 1 if c[i] > fd[i - 1] else -1 if c[i] < fu[i - 1] else trend[i - 1]
    return pd.Series(trend, b.index)


def _psar(b, step, mx):
    h, l = b['high'].to_numpy(), b['low'].to_numpy()
    n = len(h); trend = np.ones(n, dtype=int); sar = np.zeros(n)
    sar[0] = l[0]; ep = h[0]; af = step
    for i in range(1, n):
        s = sar[i - 1] + af * (ep - sar[i - 1])
        if trend[i - 1] > 0:
            s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < s:
                trend[i], s, ep, af = -1, ep, l[i], step
            else:
                trend[i] = 1
                if h[i] > ep:
                    ep, af = h[i], min(af + step, mx)
        else:
            s = max(s, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > s:
                trend[i], s, ep, af = 1, ep, h[i], step
            else:
                trend[i] = -1
                if l[i] < ep:
                    ep, af = l[i], min(af + step, mx)
        sar[i] = s
    return pd.Series(trend, b.index)


def _dmi(b, n):
    up = b['high'].diff(); dn = -b['low'].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), b.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), b.index)
    tr = ind.true_range(b).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return (100 * plus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr,
            100 * minus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr)


def _level(x, b):
    return pd.Series(float(x), b.index)


def signals(ctx, b, name, var):
    """Return (long, short) boolean Series on bars b. var is 0 (standard setting) or 1 (variant)."""
    c, h, l, o, v = b['close'], b['high'], b['low'], b['open'], b['volume']
    if name == 'ema_cross':
        f, s = ((9, 21), (20, 50))[var]
        ef, es = ind.ema(c, f), ind.ema(c, s)
        return _cross_up(ef, es), _cross_up(es, ef)
    if name == 'macd':
        f, s, g = ((12, 26, 9), (8, 21, 5))[var]
        m = ind.ema(c, f) - ind.ema(c, s); sg = ind.ema(m, g)
        return _cross_up(m, sg) & (m < 0), _cross_up(sg, m) & (m > 0)   # classic: cross below/above zero line
    if name == 'rsi_reversal':
        n, lo_, hi_ = ((14, 30, 70), (7, 20, 80))[var]
        r = ind.rsi(c, n)
        return _cross_up(r, _level(lo_, b)), _cross_up(_level(hi_, b), r)
    if name == 'rsi_momentum':
        n, lvl = ((14, 60), (14, 55))[var]
        r = ind.rsi(c, n)
        return _cross_up(r, _level(lvl, b)), _cross_up(_level(100 - lvl, b), r)
    if name in ('bb_reversal', 'bb_breakout'):
        k = (2.0, 2.5)[var]
        mid, sd = c.rolling(20).mean(), c.rolling(20).std()
        up, dn = mid + k * sd, mid - k * sd
        if name == 'bb_reversal':     # close back inside after closing outside
            return _cross_up(c, dn), _cross_up(up, c)
        return _cross_up(c, up), _cross_up(dn, c)
    if name == 'supertrend':
        n, m = ((10, 3.0), (10, 2.0))[var]
        t = _supertrend(b, n, m)
        return (t == 1) & (t.shift() == -1), (t == -1) & (t.shift() == 1)
    if name == 'vwap_cross':
        vw = ctx.get('vwap5', lambda: ind.session_vwap(ctx.df))
        tf = b.index[1] - b.index[0]
        vb = pd.Series(vw.reindex(b.index + tf - pd.Timedelta(minutes=5)).to_numpy(), b.index)
        if var == 1:      # require a second close beyond VWAP (confirmation)
            return (c > vb) & (c.shift() > vb.shift()) & (c.shift(2) <= vb.shift(2)), \
                   (c < vb) & (c.shift() < vb.shift()) & (c.shift(2) >= vb.shift(2))
        return _cross_up(c, vb), _cross_up(vb, c)
    if name == 'stochastic':
        n, lo_ = ((14, 20), (5, 20))[var]
        k = (100 * (c - _ll(l, n)) / (_hh(h, n) - _ll(l, n))).rolling(3).mean(); d = k.rolling(3).mean()
        return _cross_up(k, d) & (k < lo_), _cross_up(d, k) & (k > 100 - lo_)
    if name == 'adx_dmi':
        th = (20, 25)[var]
        p, m = _dmi(b, 14); ax = ind.adx(b, 14)
        return _cross_up(p, m) & (ax > th), _cross_up(m, p) & (ax > th)
    if name == 'ichimoku':
        a, k_, s = ((9, 26, 52), (7, 22, 44))[var]
        ten = (_hh(h, a) + _ll(l, a)) / 2; kij = (_hh(h, k_) + _ll(l, k_)) / 2
        sa = ((ten + kij) / 2).shift(k_); sb = ((_hh(h, s) + _ll(l, s)) / 2).shift(k_)
        top, bot = np.maximum(sa, sb), np.minimum(sa, sb)
        return _cross_up(ten, kij) & (c > top), _cross_up(kij, ten) & (c < bot)
    if name == 'psar':
        stp, mx = ((0.02, 0.2), (0.01, 0.1))[var]
        t = _psar(b, stp, mx)
        return (t == 1) & (t.shift() == -1), (t == -1) & (t.shift() == 1)
    if name == 'donchian':
        n = (20, 55)[var]
        return _cross_up(c, _hh(h, n).shift()), _cross_up(_ll(l, n).shift(), c)
    if name == 'keltner_breakout':
        m = (2.0, 1.5)[var]
        e, a = ind.ema(c, 20), ind.atr(b, 20)
        return _cross_up(c, e + m * a), _cross_up(e - m * a, c)
    if name == 'cci':
        lvl = (100, 200)[var]
        tp = (h + l + c) / 3; sma = tp.rolling(20).mean()
        mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
        cci = (tp - sma) / (0.015 * mad)
        return _cross_up(cci, _level(-lvl, b)), _cross_up(_level(lvl, b), cci)
    if name == 'heikin_ashi':
        need = (2, 3)[var]
        hc = (o + h + l + c) / 4
        ho = hc.shift().ewm(alpha=0.5, adjust=False).mean()
        green = hc > ho
        red_run = (~green).astype(int).rolling(need).sum().shift() == need
        green_run = green.astype(int).rolling(need).sum().shift() == need
        no_low_wick = np.minimum(ho, hc) <= l * 1.0005
        no_up_wick = np.maximum(ho, hc) >= h * 0.9995
        return green & red_run & no_low_wick, ~green & green_run & no_up_wick
    if name == 'pivots':
        d = ctx.bars('1D')
        P = ((d['high'] + d['low'] + d['close']) / 3).shift()
        R1 = 2 * P - d['low'].shift(); S1 = 2 * P - d['high'].shift()
        day = b.index.floor('D')
        P, R1, S1 = (x.reindex(day).set_axis(b.index) for x in (P, R1, S1))
        if var == 0:      # breakout through R1 / S1
            return _cross_up(c, R1), _cross_up(S1, c)
        return (l < S1) & (c > S1), (h > R1) & (c < R1)   # bounce / rejection
    if name == 'mfi':
        lo_ = (20, 10)[var]
        tp = (h + l + c) / 3; mf = tp * v
        pos = mf.where(tp > tp.shift(), 0.0).rolling(14).sum(); neg = mf.where(tp < tp.shift(), 0.0).rolling(14).sum()
        mfi = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
        return _cross_up(mfi, _level(lo_, b)), _cross_up(_level(100 - lo_, b), mfi)
    if name == 'volume_breakout':
        k = (2.0, 3.0)[var]
        vol_ok = v > k * v.rolling(20).mean().shift()
        return (c > _hh(h, 20).shift()) & vol_ok, (c < _ll(l, 20).shift()) & vol_ok
    raise ValueError(name)


INDICATORS = ['ema_cross', 'macd', 'rsi_reversal', 'rsi_momentum', 'bb_reversal', 'bb_breakout', 'supertrend',
              'vwap_cross', 'stochastic', 'adx_dmi', 'ichimoku', 'psar', 'donchian', 'keltner_breakout', 'cci',
              'heikin_ashi', 'pivots', 'mfi', 'volume_breakout']
VARIANTS = {'ema_cross': ['9/21', '20/50'], 'macd': ['12/26/9', '8/21/5'], 'rsi_reversal': ['14 30/70', '7 20/80'],
            'rsi_momentum': ['14 cross 60/40', '14 cross 55/45'], 'bb_reversal': ['20,2', '20,2.5'],
            'bb_breakout': ['20,2', '20,2.5'], 'supertrend': ['10,3', '10,2'], 'vwap_cross': ['cross', '2-bar confirm'],
            'stochastic': ['14,3,3 <20', '5,3,3 <20'], 'adx_dmi': ['ADX>20', 'ADX>25'], 'ichimoku': ['9/26/52', '7/22/44'],
            'psar': ['0.02/0.2', '0.01/0.1'], 'donchian': ['20', '55'], 'keltner_breakout': ['EMA20+2ATR', 'EMA20+1.5ATR'],
            'cci': ['+-100', '+-200'], 'heikin_ashi': ['flip after 2', 'flip after 3'],
            'pivots': ['R1/S1 breakout', 'S1/R1 bounce'], 'mfi': ['14 20/80', '14 10/90'],
            'volume_breakout': ['20-bar high, vol>2x', '20-bar high, vol>3x']}
EXITS = {'target2R': dict(stop_atr=1.5, target_r=2.0, trail_atr=NAN),
         'trail': dict(stop_atr=2.0, target_r=NAN, trail_atr=3.0)}


def lab(ctx, ind='ema_cross', var=0, tf='1h', exit='target2R', filt='none', hold_h=12):
    name = ind
    b = ctx.bars(tf)
    long, short = ctx.get(('labsig', name, var, tf), lambda: signals(ctx, b, name, var))
    a = ctx.get(('labatr', tf), lambda: _atr14(b))
    if filt == 'ema200':
        e = ctx.get(('labema200', tf), lambda: b['close'].ewm(span=200, adjust=False, min_periods=200).mean())
        long, short = long & (b['close'] > e), short & (b['close'] < e)
    x = EXITS[exit]
    step = b.index[1] - b.index[0]
    out = []
    for side, cond in ((1, long), (-1, short)):
        cond = cond.fillna(False).astype(bool)
        s = b[cond]
        if s.empty:
            continue
        aa = a[cond].to_numpy(); cc = s['close'].to_numpy()
        stop = cc - side * x['stop_atr'] * aa
        tgt = cc + side * x['target_r'] * x['stop_atr'] * aa if not np.isnan(x['target_r']) else np.full(len(cc), NAN)
        trail = x['trail_atr'] * aa if not np.isnan(x['trail_atr']) else np.full(len(cc), NAN)
        t = ctx.pos(s.index + step)
        out.append(st._orders(t, side, 'market', NAN, 1, stop, tgt, hold_h * 12, NAN, trail))
    return pd.concat(out) if out else None


def _atr14(b):
    return ind.atr(b, 14)


st.FAMILIES['lab'] = lab


def grid():
    for name in INDICATORS:
        for var in (0, 1):
            for tf in ('15min', '1h'):
                for ex in EXITS:
                    for filt in ('none', 'ema200'):
                        yield dict(ind=name, var=var, tf=tf, exit=ex, filt=filt)
