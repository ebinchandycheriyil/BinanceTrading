"""Intraday strategy families. Each returns an orders DataFrame for backtest.simulate.

Every signal is decided at the close of a completed bar; the order is active from the next 5m bar.
GRIDS holds the small, predeclared parameter grid for each family.
"""
from itertools import product

import numpy as np
import pandas as pd

from . import indicators as ind

M5 = pd.Timedelta(minutes=5)
NAN = float('nan')


class Ctx:
    """Per-symbol data with memoised features shared across parameter combinations."""

    def __init__(self, symbol, df):
        self.symbol, self.df = symbol, df
        self.idx = df.index
        self._memo = {}

    def get(self, key, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def bars(self, rule):
        return self.get(('bars', rule), lambda: ind.resample(self.df, rule))

    def pos(self, times):
        """Positions of the 5m bars opening at the given timestamps (-1 if absent)."""
        p = self.idx.searchsorted(times)
        p = np.minimum(p, len(self.idx) - 1)
        ok = self.idx[p] == times
        return np.where(ok, p, -1)

    def daily_atr_frac(self):
        def f():
            d = self.bars('1D')
            return (ind.atr(d, 14) / d['close']).shift()   # prior day's value
        return self.get('datr', f)


def _orders(t, side, kind, price, expire, stop, target, max_bars, be_r=NAN, trail=NAN):
    n = len(t)

    def col(v, dtype=float):
        return np.full(n, v, dtype=dtype) if np.isscalar(v) or v is None else np.asarray(v, dtype=dtype)
    o = pd.DataFrame({'t': col(t, 'int64'), 'side': col(side, 'int64'),
                      'kind': np.full(n, kind, dtype=object),
                      'price': col(price), 'expire': col(expire, 'int64'), 'stop': col(stop),
                      'target': col(target), 'max_bars': col(max_bars, 'int64'), 'be_r': col(be_r),
                      'trail': col(trail)})
    return o[(o['t'] >= 0) & np.isfinite(o['stop'])]


def _session_starts(idx, session):
    days = pd.date_range(idx[0].floor('D'), idx[-1].floor('D'), freq='D', tz='UTC')
    if session == 'asia':
        return days
    tz, hh, mm = {'london': ('Europe/London', 8, 0), 'us': ('America/New_York', 9, 30)}[session]
    local = [pd.Timestamp(d.date()).tz_localize(tz) + pd.Timedelta(hours=hh, minutes=mm)
             for d in days if d.dayofweek < 5]
    return pd.DatetimeIndex(local).tz_convert('UTC')


# --------------------------------------------------------------------------- 1. ORB
def orb(ctx, session='us', range_min=30, window_h=4, target_r=2.0, stop_mode='range',
        max_range_atr=0.5, trend=False):
    df = ctx.df; h = df['high'].to_numpy(); lo = df['low'].to_numpy()
    starts = _session_starts(ctx.idx, session)
    datr = ctx.daily_atr_frac().reindex(starts.floor('D')).to_numpy()
    if trend:
        d = ctx.bars('1D')['close']
        up = (d > ind.ema(d, 20)).shift().reindex(starts.floor('D')).to_numpy()
    nr = range_min // 5
    rows = []
    for i, a in enumerate(ctx.pos(starts)):
        if a < 0 or a + nr >= len(h) or not np.isfinite(datr[i]):
            continue
        b = a + nr
        if ctx.idx[b - 1] - ctx.idx[a] != M5 * (nr - 1):
            continue
        hi, lw = h[a:b].max(), lo[a:b].min()
        mid = (hi + lw) / 2
        if (hi - lw) / mid > max_range_atr * datr[i]:
            continue
        e = min(len(h), b + window_h * 12)
        up_hit = np.flatnonzero(h[b:e] >= hi); dn_hit = np.flatnonzero(lo[b:e] <= lw)
        fu = up_hit[0] if len(up_hit) else 10**9; fd = dn_hit[0] if len(dn_hit) else 10**9
        if fu == fd == 10**9:
            continue
        side = 1 if fu <= fd else -1          # same-bar double touch -> long, stop-first applies
        if trend and up[i] == (side < 0):
            continue
        t = b + min(fu, fd); px = hi if side > 0 else lw
        stop = (lw if side > 0 else hi) if stop_mode == 'range' else mid
        tgt = px + side * target_r * abs(px - stop) if target_r else NAN
        rows.append((t, side, px, stop, tgt, e - 1 - t))
    if not rows:
        return None
    t, side, px, stop, tgt, mb = map(np.array, zip(*rows))
    return _orders(t, side, 'stop', px, 1, stop, tgt, np.maximum(mb, 1))


# --------------------------------------------------------------------------- 2. PDH/PDL sweep
def sweep(ctx, pen=0.1, buf=0.25, target_r=2.0, hold_h=8, entry='market'):
    m = ctx.bars('15min')
    a15 = ctx.get('atr15', lambda: ind.atr(m, 14))
    lv = ctx.get('pd15', lambda: ind.prior_day_levels(m))
    pdh, pdl = lv['high'], lv['low']
    short = (m['high'] > pdh + pen * a15) & (m['close'] < pdh) & (m['open'] < pdh)
    long = (m['low'] < pdl - pen * a15) & (m['close'] > pdl) & (m['open'] > pdl)
    day = m.index.floor('D')
    short &= short.groupby(day).cumsum() == 1
    long &= long.groupby(day).cumsum() == 1
    out = []
    for side, cond in ((1, long), (-1, short)):
        s = m[cond]
        if s.empty:
            continue
        aa = a15[cond]
        stop = (s['low'] - buf * aa) if side > 0 else (s['high'] + buf * aa)
        ref = s['close'] if entry == 'market' else (pdl[cond] if side > 0 else pdh[cond])
        tgt = ref + side * target_r * (ref - stop).abs()
        t = ctx.pos(s.index + pd.Timedelta(minutes=15))
        kind = 'market' if entry == 'market' else 'limit'
        out.append(_orders(t, side, kind, ref.to_numpy(), 6, stop.to_numpy(), tgt.to_numpy(), hold_h * 12))
    return pd.concat(out) if out else None


# --------------------------------------------------------------------------- 3. VWAP fade
def vwap_fade(ctx, k=2.5, rsi_band=25, adx_max=25, entry_atr=0.0, stop_atr=2.0,
              tgt_frac=1.0, hold_h=4):
    df = ctx.df; c = df['close']
    vw = ctx.get('vwap', lambda: ind.session_vwap(df))
    z = ctx.get('vz', lambda: (c - vw) / (c - vw).rolling(288).std())
    a5 = ctx.get('atr5', lambda: ind.atr(df, 14))
    r = ctx.get('rsi5', lambda: ind.rsi(c, 14))
    ax = ctx.get('adx1h', lambda: ind.to_base(ind.adx(ctx.bars('1h')), '1h', ctx.idx))
    hrs = ctx.idx.hour + ctx.idx.minute / 60
    base = (ax < adx_max) & (hrs >= 2)
    out = []
    for side, cond in ((1, base & (z < -k) & (r < rsi_band)), (-1, base & (z > k) & (r > 100 - rsi_band))):
        cond = cond & ~cond.shift(fill_value=False)
        p = np.flatnonzero(cond.to_numpy())
        if not len(p):
            continue
        cc, aa, vv = c.iloc[p].to_numpy(), a5.iloc[p].to_numpy(), vw.iloc[p].to_numpy()
        px = cc - side * entry_atr * aa
        stop = px - side * stop_atr * aa
        tgt = cc + tgt_frac * (vv - cc)
        out.append(_orders(p + 1, side, 'limit', px, 6, stop, tgt, hold_h * 12))
    return pd.concat(out) if out else None


# --------------------------------------------------------------------------- 4. trend-day pullback
def pullback(ctx, target_r=2.0, be_r=NAN, lookback=4, hold_h=12, start_h=7, end_h=20):
    m = ctx.bars('15min'); c = m['close']
    e9 = ctx.get('e9', lambda: ind.ema(c, 9)); e21 = ctx.get('e21', lambda: ind.ema(c, 21))
    a15 = ctx.get('atr15', lambda: ind.atr(m, 14))

    def trend():
        h4 = ctx.bars('4h')['close']; f, s = ind.ema(h4, 20), ind.ema(h4, 50)
        tr = pd.Series(np.where((f > s) & (f > f.shift()), 1, np.where((f < s) & (f < f.shift()), -1, 0)), h4.index)
        return ind.to_base(tr, '4h', m.index, '15min')
    tr = ctx.get('trend4_15', trend)
    vw = ctx.get('vwap15', lambda: pd.Series(
        ind.session_vwap(ctx.df).reindex(m.index + pd.Timedelta(minutes=10)).to_numpy(), m.index))
    hr = m.index.hour
    window = (hr >= start_h) & (hr < end_h)
    touch_l = (m['low'] <= e21 * 1.001).rolling(lookback).max().astype(bool)
    touch_s = (m['high'] >= e21 * 0.999).rolling(lookback).max().astype(bool)
    lowest = m['low'].rolling(6).min(); highest = m['high'].rolling(6).max()
    out = []
    for side, cond, ref in ((1, (tr == 1) & touch_l & (c > e9) & (c.shift() <= e9.shift()) & (c > vw), lowest),
                            (-1, (tr == -1) & touch_s & (c < e9) & (c.shift() >= e9.shift()) & (c < vw), highest)):
        cond = cond & window
        s = m[cond]
        if s.empty:
            continue
        aa = a15[cond]; cc = s['close']
        stop = ref[cond] - side * 0.2 * aa
        risk = (cc - stop).abs()
        ok = ((risk > 0.3 * aa) & (risk < 3 * aa)).to_numpy()
        tgt = cc + side * target_r * risk
        t = ctx.pos(s.index + pd.Timedelta(minutes=15))
        eod = ((s.index.floor('D') + pd.Timedelta(days=1) - (s.index + pd.Timedelta(minutes=15))) / M5).to_numpy() - 1
        mb = np.minimum(eod, hold_h * 12)
        out.append(_orders(t[ok], side, 'market', NAN, 1, stop.to_numpy()[ok], tgt.to_numpy()[ok], mb[ok], be_r))
    return pd.concat(out) if out else None


# --------------------------------------------------------------------------- 5. squeeze release
def squeeze(ctx, kc=1.5, min_sq=6, vol_mult=1.5, stop_atr=1.5, target_r=2.0, hold_h=24):
    h1 = ctx.bars('1h'); c = h1['close']

    def feats():
        mid, sd = c.rolling(20).mean(), c.rolling(20).std()
        return mid, sd, ind.ema(c, 20), ind.atr(h1, 20), ind.ema(c, 50)
    mid, sd, e20, a, e50 = ctx.get('sqf', feats)
    sq = (mid + 2 * sd < e20 + kc * a) & (mid - 2 * sd > e20 - kc * a)
    was = sq.shift().rolling(min_sq).sum() == min_sq
    vol_ok = h1['volume'] > vol_mult * h1['volume'].rolling(20).mean().shift()
    hi20 = h1['high'].rolling(20).max().shift(); lo20 = h1['low'].rolling(20).min().shift()
    out = []
    for side, cond in ((1, was & vol_ok & (c > hi20) & (c > e50)), (-1, was & vol_ok & (c < lo20) & (c < e50))):
        s = h1[cond]
        if s.empty:
            continue
        stop = s['close'] - side * stop_atr * a[cond]
        tgt = s['close'] + side * target_r * stop_atr * a[cond]
        t = ctx.pos(s.index + pd.Timedelta(hours=1))
        out.append(_orders(t, side, 'market', NAN, 1, stop.to_numpy(), tgt.to_numpy(), hold_h * 12))
    return pd.concat(out) if out else None


# --------------------------------------------------------------------------- 6. weekend (CME gap) fade
def weekend(ctx, gmin=0.01, fill_frac=1.0, stop_mult=1.0, hold_h=48):
    o = ctx.df['open'].to_numpy()
    fridays = pd.date_range(ctx.idx[0].floor('D'), ctx.idx[-1].floor('D'), freq='W-FRI', tz='UTC')
    rows = []
    for f in fridays:
        fc = (pd.Timestamp(f.date()).tz_localize('America/Chicago') + pd.Timedelta(hours=16)).tz_convert('UTC')
        so = (pd.Timestamp((f + pd.Timedelta(days=2)).date()).tz_localize('America/Chicago')
              + pd.Timedelta(hours=17)).tz_convert('UTC')
        a, b = ctx.pos(pd.DatetimeIndex([fc, so]))
        if a < 0 or b < 0:
            continue
        F, P = o[a], o[b]
        g = P / F - 1
        if abs(g) < gmin:
            continue
        side = -1 if g > 0 else 1
        gap = abs(P - F)
        rows.append((b, side, P - side * stop_mult * gap, P + side * fill_frac * gap))
    if not rows:
        return None
    t, side, stop, tgt = map(np.array, zip(*rows))
    return _orders(t, side, 'market', NAN, 1, stop, tgt, hold_h * 12)


# --------------------------------------------------------------------------- 7. liquidation cascade fade
def cascade(ctx, k_range=4.0, k_vol=4.0, deep=0.0, stop_k=1.0, tgt=0.5, hold=24):
    df = ctx.df

    def base():
        tr = ind.true_range(df)
        return tr.rolling(288).mean().shift(), df['volume'].rolling(288).mean().shift()
    btr, bvol = ctx.get('casc', base)
    rng = df['high'] - df['low']
    big = (rng > k_range * btr) & (df['volume'] > k_vol * bvol)
    loc = (df['close'] - df['low']) / rng.replace(0, np.nan)
    out = []
    for side, cond in ((1, big & (df['close'] < df['open']) & (loc < 0.5)),
                       (-1, big & (df['close'] > df['open']) & (loc > 0.5))):
        p = np.flatnonzero(cond.to_numpy())
        if not len(p):
            continue
        c = df['close'].to_numpy()[p]; h = df['high'].to_numpy()[p]; lw = df['low'].to_numpy()[p]
        r = h - lw; b = btr.to_numpy()[p]
        px = c - side * deep * r
        ext = np.minimum(lw, px) if side > 0 else np.maximum(h, px)
        stop = ext - side * stop_k * b
        target = c + tgt * ((h - c) if side > 0 else (lw - c))
        kind = 'market' if deep == 0 else 'limit'
        out.append(_orders(p + 1, side, kind, px, 12, stop, target, hold))
    return pd.concat(out) if out else None


# --------------------------------------------------------------------------- 8. intraday time-series momentum
def daymom(ctx, hour=16, k=1.0, stop_atr=2.0, mode='mom'):
    o = ctx.df['open'].to_numpy()
    vol = ctx.get('dvol', lambda: np.log(ctx.bars('1D')['close']).diff().rolling(30).std().shift())
    a1 = ctx.get('atr1h_b', lambda: ind.to_base(ind.atr(ctx.bars('1h'), 14), '1h', ctx.idx))
    days = vol.dropna().index
    t0 = ctx.pos(days); t1 = ctx.pos(days + pd.Timedelta(hours=hour))
    ok = (t0 >= 0) & (t1 > 0)
    days, t0, t1 = days[ok], t0[ok], t1[ok]
    ret = o[t1] / o[t0] - 1
    sig = vol.reindex(days).to_numpy() * np.sqrt(hour / 24)
    m = np.abs(ret) > k * sig
    side = np.sign(ret[m]).astype(int) * (1 if mode == 'mom' else -1)
    t = t1[m]
    stop = o[t] - side * stop_atr * a1.to_numpy()[t - 1]
    return _orders(t, side, 'market', NAN, 1, stop, NAN, (24 - hour) * 12 - 1)


FAMILIES = {'orb': orb, 'sweep': sweep, 'vwap_fade': vwap_fade, 'pullback': pullback,
            'squeeze': squeeze, 'weekend': weekend, 'cascade': cascade, 'daymom': daymom}

GRIDS = {
    'orb': dict(session=['asia', 'london', 'us'], range_min=[15, 30, 60], window_h=[4],
                target_r=[1.5, 3.0], stop_mode=['range', 'mid'], max_range_atr=[0.35, 1.0], trend=[False]),
    'sweep': dict(pen=[0.0, 0.25], buf=[0.25, 0.5], target_r=[1.5, 3.0], hold_h=[8], entry=['market', 'retest']),
    'vwap_fade': dict(k=[2.0, 3.0], rsi_band=[25], adx_max=[20, 30], entry_atr=[0.0, 0.5],
                      stop_atr=[1.5, 3.0], tgt_frac=[0.5, 1.0], hold_h=[4]),
    'pullback': dict(target_r=[1.5, 2.5], be_r=[NAN, 1.0], lookback=[3, 6], hold_h=[12], start_h=[7], end_h=[20]),
    'squeeze': dict(kc=[1.5], min_sq=[4, 8], vol_mult=[1.0, 1.5], stop_atr=[1.5, 2.5], target_r=[1.5, 3.0], hold_h=[24]),
    'weekend': dict(gmin=[0.005, 0.01, 0.02], fill_frac=[0.5, 1.0], stop_mult=[1.0, 2.0], hold_h=[24, 72]),
    'cascade': dict(k_range=[3.0, 5.0], k_vol=[3.0, 6.0], deep=[0.0, 0.25], stop_k=[1.0, 2.0], tgt=[0.5], hold=[24]),
    'daymom': dict(hour=[8, 14, 20], k=[0.5, 1.0], stop_atr=[2.0, 4.0], mode=['mom', 'rev']),
}


# =========================================================================== round 2
def trend_trail(ctx, lookback=48, trend=True, init_atr=2.0, trail_atr=3.0, hold_h=72):
    """1h Donchian close breakout, optional daily EMA50 filter, chandelier trail, no fixed target."""
    h1 = ctx.bars('1h'); c = h1['close']
    a = ctx.get('atr1h', lambda: ind.atr(h1, 14))
    hi = h1['high'].rolling(lookback).max().shift(); lo = h1['low'].rolling(lookback).min().shift()
    up = dn = pd.Series(True, index=h1.index)
    if trend:
        d = ctx.bars('1D')['close']
        f = ctx.get('d_ema50_1h', lambda: ind.to_base(d > ind.ema(d, 50), '1D', h1.index, '1h'))
        up = f == True; dn = f == False
    out = []
    for side, cond in ((1, (c > hi) & (c.shift() <= hi.shift()) & up),
                       (-1, (c < lo) & (c.shift() >= lo.shift()) & dn)):
        s = h1[cond]
        if s.empty:
            continue
        aa = a[cond].to_numpy()
        t = ctx.pos(s.index + pd.Timedelta(hours=1))
        out.append(_orders(t, side, 'market', NAN, 1, s['close'].to_numpy() - side * init_atr * aa, NAN,
                           hold_h * 12, NAN, trail_atr * aa))
    return pd.concat(out) if out else None


def nr_breakout(ctx, nr=7, k=0.3, stop_mode='open', use_nr=True):
    """Crabel range expansion: after an NR-n day, stop orders at today's open +/- k * daily ATR."""
    d = ctx.bars('1D')
    rng = d['high'] - d['low']
    flag = (rng == rng.rolling(nr).min()).shift() if use_nr else pd.Series(True, index=d.index)
    datr = ind.atr(d, 14).shift()
    h = ctx.df['high'].to_numpy(); lo = ctx.df['low'].to_numpy(); o = ctx.df['open'].to_numpy()
    rows = []
    days = d.index[(flag == True).to_numpy() & datr.notna().to_numpy()]
    for day, a0 in zip(days, ctx.pos(days)):
        if a0 < 0:
            continue
        e = min(len(h), a0 + 288)
        op = o[a0]; dist = k * datr[day]
        up_px, dn_px = op + dist, op - dist
        uh = np.flatnonzero(h[a0:e] >= up_px); dh = np.flatnonzero(lo[a0:e] <= dn_px)
        fu = uh[0] if len(uh) else 10**9; fd = dh[0] if len(dh) else 10**9
        if fu == fd == 10**9:
            continue
        side = 1 if fu <= fd else -1
        t = a0 + min(fu, fd)
        px = up_px if side > 0 else dn_px
        stop = op if stop_mode == 'open' else (dn_px if side > 0 else up_px)
        rows.append((t, side, px, stop, e - 1 - t))
    if not rows:
        return None
    t, side, px, stop, mb = map(np.array, zip(*rows))
    return _orders(t, side, 'stop', px, 1, stop, NAN, np.maximum(mb, 1))


def rsi2_dip(ctx, th=10, tgt_atr=1.0, stop_atr=2.5, hold_h=24, shorts=True):
    """Buy short-term oversold (RSI2) only in an established 1h/4h uptrend; mirror for shorts."""
    h1 = ctx.bars('1h'); c = h1['close']
    a = ctx.get('atr1h', lambda: ind.atr(h1, 14))
    r2 = ctx.get('rsi2_1h', lambda: ind.rsi(c, 2))
    e200 = ctx.get('e200_1h', lambda: ind.ema(c, 200))
    def t4():
        h4 = ctx.bars('4h')['close']; f, s = ind.ema(h4, 20), ind.ema(h4, 50)
        return ind.to_base(pd.Series(np.sign(f - s), h4.index), '4h', h1.index, '1h')
    tr4 = ctx.get('tr4_1h', t4)
    out = []
    sides = ((1, (r2 < th) & (c > e200) & (tr4 > 0)),) + (((-1, (r2 > 100 - th) & (c < e200) & (tr4 < 0)),) if shorts else ())
    for side, cond in sides:
        s = h1[cond]
        if s.empty:
            continue
        aa = a[cond].to_numpy(); cc = s['close'].to_numpy()
        t = ctx.pos(s.index + pd.Timedelta(hours=1))
        out.append(_orders(t, side, 'market', NAN, 1, cc - side * stop_atr * aa, cc + side * tgt_atr * aa, hold_h * 12))
    return pd.concat(out) if out else None


def funding_squeeze(ctx, thr=0.0003, brk=6, init_atr=2.0, trail_atr=3.0, hold_h=48, sides='both', long_div=3.0):
    """Crowded funding + 1h break AGAINST the crowd: positive funding & breakdown -> short."""
    h1 = ctx.bars('1h'); c = h1['close']
    a = ctx.get('atr1h', lambda: ind.atr(h1, 14))
    f = getattr(ctx, 'funding', None)
    if f is None or not len(f):
        return None
    fr = f.reindex(h1.index.union(f.index)).ffill().reindex(h1.index)   # last settled rate
    hi = h1['high'].rolling(brk).max().shift(); lo = h1['low'].rolling(brk).min().shift()
    out = []
    legs = ((-1, (fr > thr) & (c < lo)), (1, (fr < -thr / long_div) & (c > hi)))
    for side, cond in legs:
        if (sides == 'long' and side < 0) or (sides == 'short' and side > 0):
            continue
        cond = cond & ~cond.shift(fill_value=False)
        s = h1[cond]
        if s.empty:
            continue
        aa = a[cond].to_numpy()
        t = ctx.pos(s.index + pd.Timedelta(hours=1))
        out.append(_orders(t, side, 'market', NAN, 1, s['close'].to_numpy() - side * init_atr * aa, NAN,
                           hold_h * 12, NAN, trail_atr * aa))
    return pd.concat(out) if out else None


def hour_season(ctx, start=20, length=4, side=1, trend=True, stop_atr=3.0):
    """Hold a fixed UTC intraday window; optional daily trend filter in the trade direction."""
    days = ctx.bars('1D').index
    t = ctx.pos(days + pd.Timedelta(hours=start))
    a1 = ctx.get('atr1h_b', lambda: ind.to_base(ind.atr(ctx.bars('1h'), 14), '1h', ctx.idx))
    ok = t > 0
    if trend:
        d = ctx.bars('1D')['close']
        up = (d > ind.ema(d, 20)).shift().reindex(days).to_numpy()
        ok &= (up == True) if side > 0 else (up == False)
    t = t[ok]
    px = ctx.df['open'].to_numpy()[t]
    return _orders(t, side, 'market', NAN, 1, px - side * stop_atr * a1.to_numpy()[t - 1], NAN, length * 12 - 1)


ROUND2 = {'trend_trail': trend_trail, 'nr_breakout': nr_breakout, 'rsi2_dip': rsi2_dip,
          'funding_squeeze': funding_squeeze, 'hour_season': hour_season}
FAMILIES.update(ROUND2)
GRIDS.update({
    'trend_trail': dict(lookback=[24, 48, 96], trend=[True, False], init_atr=[2.0, 3.0], trail_atr=[3.0, 5.0], hold_h=[72]),
    'nr_breakout': dict(nr=[4, 7], k=[0.2, 0.4], stop_mode=['open', 'opposite'], use_nr=[True, False]),
    'rsi2_dip': dict(th=[5, 10], tgt_atr=[0.75, 1.5], stop_atr=[2.0, 3.5], hold_h=[24], shorts=[True, False]),
    'funding_squeeze': dict(thr=[0.0002, 0.0004], brk=[6, 24], init_atr=[2.0, 3.0], trail_atr=[3.0, 5.0], hold_h=[48]),
    'hour_season': dict(start=[0, 8, 13, 20], length=[4, 8], side=[1, -1], trend=[True], stop_atr=[3.0]),
})


# =========================================================================== round 3
def _hourly_metrics(ctx, h1_index):
    """Open interest / ratio metrics sampled at each 1h bar's final 5m stamp (known at bar close)."""
    m = getattr(ctx, 'metrics', None)
    if m is None or not len(m):
        return None
    stamp = h1_index + pd.Timedelta(minutes=55)
    return m.reindex(m.index.union(stamp)).ffill().reindex(stamp).set_axis(h1_index)


def squeeze_oi(ctx, fthr=0.0, oi_win=24, oi_min=0.03, ls_q=0.3, brk=6, init_atr=2.0,
               trail_atr=5.0, hold_h=48, sides='both', entry='market', eod=False):
    """Crowding = funding against the crowd + open-interest build-up + retail account skew.

    Long (short squeeze): funding < -fthr, OI up > oi_min over oi_win hours, retail long/short
    account ratio in the bottom ls_q of its trailing 30 days, and the 1h close breaks the
    prior `brk`-hour high. Short (long squeeze) mirrors it with funding > fthr + 1bp.
    entry='retest': maker limit back at the broken level, live for 6 bars (30 min).
    eod=True: forced exit by the last 5m bar of the UTC day (strict day trading).
    """
    h1 = ctx.bars('1h'); c = h1['close']
    a = ctx.get('atr1h', lambda: ind.atr(h1, 14))
    m = ctx.get('m1h', lambda: _hourly_metrics(ctx, h1.index))
    f = getattr(ctx, 'funding', None)
    if m is None or f is None:
        return None
    fr = ctx.get('fr1h', lambda: f.reindex(h1.index.union(f.index)).ffill().reindex(h1.index))
    oi = m['oi'].replace(0, np.nan)
    oi_chg = oi / oi.shift(oi_win) - 1
    ls = ctx.get('ls_rank', lambda: m['ls_acct'].replace(0, np.nan).rolling(720, min_periods=240).rank(pct=True))
    hi = h1['high'].rolling(brk).max().shift(); lo = h1['low'].rolling(brk).min().shift()
    crowd_short = (fr < -fthr) & (oi_chg > oi_min) & (ls < ls_q)
    crowd_long = (fr > fthr + 1e-4) & (oi_chg > oi_min) & (ls > 1 - ls_q)
    out = []
    for side, cond in ((1, crowd_short & (c > hi)), (-1, crowd_long & (c < lo))):
        if (sides == 'long' and side < 0) or (sides == 'short' and side > 0):
            continue
        cond = (cond & ~cond.shift(fill_value=False)).fillna(False).astype(bool)
        s = h1[cond]
        if s.empty:
            continue
        aa = a[cond].to_numpy()
        fill_t = s.index + pd.Timedelta(hours=1)
        t = ctx.pos(fill_t)
        mb = np.full(len(s), hold_h * 12)
        if eod:
            left = ((fill_t.floor('D') + pd.Timedelta(days=1) - fill_t) / M5).to_numpy().astype(int) - 1
            mb = np.minimum(mb, left)
        if entry == 'retest':
            lvl = (hi if side > 0 else lo)[cond].to_numpy()
            o = _orders(t, side, 'limit', lvl, 6, lvl - side * init_atr * aa, NAN, mb, NAN, trail_atr * aa)
        else:
            o = _orders(t, side, 'market', NAN, 1, s['close'].to_numpy() - side * init_atr * aa, NAN,
                        mb, NAN, trail_atr * aa)
        out.append(o[o['max_bars'] >= 6])          # skip entries with < 30 min left in the day
    return pd.concat(out) if out else None


def flow_follow(ctx, k_range=4.0, k_vol=4.0, flow=0.6, init_k=1.0, trail_k=3.0, hold=48):
    """Trade WITH an outsized 5m bar whose taker flow agrees (forced flow continuation)."""
    df = ctx.df
    btr, bvol = ctx.get('casc', lambda: (ind.true_range(df).rolling(288).mean().shift(),
                                          df['volume'].rolling(288).mean().shift()))
    rng = df['high'] - df['low']
    buy = df['taker_buy_volume'] / df['volume'].replace(0, np.nan)
    big = (rng > k_range * btr) & (df['volume'] > k_vol * bvol)
    out = []
    for side, cond in ((1, big & (df['close'] > df['open']) & (buy > flow)),
                       (-1, big & (df['close'] < df['open']) & (buy < 1 - flow))):
        p = np.flatnonzero(cond.to_numpy())
        if not len(p):
            continue
        c = df['close'].to_numpy()[p]; b = btr.to_numpy()[p]
        ext = df['low'].to_numpy()[p] if side > 0 else df['high'].to_numpy()[p]
        stop = ext - side * init_k * b
        out.append(_orders(p + 1, side, 'market', NAN, 1, stop, NAN, hold, NAN, trail_k * b))
    return pd.concat(out) if out else None


ROUND3 = {'squeeze_oi': squeeze_oi, 'flow_follow': flow_follow, 'funding_squeeze_r3': funding_squeeze,
          'squeeze_day': squeeze_oi}
FAMILIES.update(ROUND3)
GRIDS.update({
    'funding_squeeze_r3': dict(thr=[0.0003, 0.0004, 0.0005], brk=[4, 6, 9], init_atr=[2.0], trail_atr=[4.0, 6.0],
                               hold_h=[48], sides=['both', 'long']),
    'squeeze_oi': dict(fthr=[0.0, 0.0001], oi_win=[24], oi_min=[0.0, 0.05], ls_q=[0.2, 0.4], brk=[6],
                       init_atr=[2.0], trail_atr=[5.0], hold_h=[48], sides=['both', 'long']),
    # round 4: intraday version of squeeze_oi (holds <= 12h); evaluated out-of-sample on unseen coins
    'squeeze_day': dict(fthr=[0.0], oi_win=[24], oi_min=[0.0], ls_q=[0.4, 0.5], brk=[3, 6], init_atr=[1.5, 2.0],
                        trail_atr=[2.5, 4.0], hold_h=[4, 8, 12], sides=['long', 'both'], entry=['market', 'retest'],
                        eod=[False]),
    'flow_follow': dict(k_range=[3.0, 5.0], k_vol=[3.0, 6.0], flow=[0.55, 0.65], init_k=[1.0], trail_k=[2.0, 4.0], hold=[48]),
})


def grid(family):
    g = GRIDS[family]
    keys = list(g)
    for vals in product(*(g[k] for k in keys)):
        yield dict(zip(keys, vals))
