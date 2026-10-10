"""Causal indicators on 5m bars.

Convention: a value at 5m bar i uses information up to and including the CLOSE of bar i.
Strategies emit orders that become active on bar i+1.
"""
import numpy as np
import pandas as pd

BAR = pd.Timedelta(minutes=5)


def ema(s, n):
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def true_range(df):
    pc = df['close'].shift()
    return pd.concat([df['high'] - df['low'], (df['high'] - pc).abs(), (df['low'] - pc).abs()], axis=1).max(axis=1)


def atr(df, n=14):
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def adx(df, n=14):
    up = df['high'].diff()
    dn = -df['low'].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def resample(df, rule):
    """OHLCV bars labelled by their OPEN time. Incomplete bars are dropped."""
    agg = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last',
           'volume': 'sum', 'quote_volume': 'sum', 'taker_buy_volume': 'sum'}
    out = df.resample(rule, label='left', closed='left').agg(agg)
    counts = df['close'].resample(rule, label='left', closed='left').count()
    full = int(pd.Timedelta(rule) / BAR)
    return out[counts == full].dropna()


def to_base(htf_values, rule, base_index, base=BAR):
    """Map completed higher-timeframe values onto base bars without look-ahead.

    A bar opening at T with length L is complete at the close of the base bar opening at T+L-base,
    so its value becomes usable from that base bar onward.
    """
    shifted = htf_values.copy()
    shifted.index = shifted.index + pd.Timedelta(rule) - pd.Timedelta(base)
    return shifted.reindex(base_index, method='ffill')


def session_vwap(df):
    """VWAP anchored at each UTC midnight, including the current bar."""
    tp = (df['high'] + df['low'] + df['close']) / 3
    day = df.index.floor('D')
    pv = (tp * df['volume']).groupby(day).cumsum()
    v = df['volume'].groupby(day).cumsum()
    return pv / v.replace(0, np.nan)


def prior_day_levels(df):
    """Previous UTC day's high/low/close, available all day."""
    d = df.resample('1D').agg({'high': 'max', 'low': 'min', 'close': 'last'}).shift()
    day = df.index.floor('D')
    return d.reindex(day).set_axis(df.index)
