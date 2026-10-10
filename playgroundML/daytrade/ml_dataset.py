"""ML dataset: every 1h candle close is a possible BUY and SELL entry, labelled with what actually happened.

Label rule (fixed before training):
  entry   = open of the 5m bar right after the 1h close (+ slippage), BNB-discount taker fee
  stop    = 1.5 x ATR(14, 1h)
  target  = max(1.5 x stop distance, round-trip costs + MIN_NET) beyond entry  (resting maker order)
  exit    = first of target / stop (stop wins same-bar ties) / 12h time limit
  label   = 1 if net return after ALL fees and slippage >= MIN_NET (0.30%), else 0
Hindsight columns (prefix hind_) give the best possible exit inside the 12h window. They are for analysis
ONLY and are never model inputs. Features use completed bars only (value known at the 1h close).

  cd playgroundML && ..\\.venv\\Scripts\\python.exe -m daytrade.ml_dataset
"""
import os

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from . import btc_history, indicators as ind, lab, strategies as st
from .data import ROOT

TAKER, MAKER, SLIP = 4.5e-4, 1.8e-4, 1.5e-4          # BNB-discount fees, BTC/ETH slippage per taker fill
STOP_ATR, RR, MIN_NET, HOLD = 1.5, 1.5, 0.003, 144   # 144 x 5m = 12h
COINS = {'BTC': btc_history.PATH, 'ETH': btc_history.ETH_PATH}
OUT = os.path.join(ROOT, 'ml_dataset.pkl')
NON_FEATURE = ('label_', 'net_', 'exit_', 'hold_h_', 'stop_', 'target_', 'entry_', 'hind_')


def feature_columns(ds):
    return [c for c in ds.columns if not c.startswith(NON_FEATURE) and c not in ('coin',)]


def _features(df5, other5=None):
    h = ind.resample(df5, '1h')
    c, hi, lo, v = h['close'], h['high'], h['low'], h['volume']
    lr = np.log(c).diff()
    a = ind.atr(h, 14)
    f = pd.DataFrame(index=h.index)
    for n in (1, 4, 12, 24, 72, 168):
        f[f'ret_{n}h'] = np.log(c / c.shift(n))
    f['atr_pct'] = a / c
    f['vol_24h'] = lr.rolling(24).std(); f['vol_168h'] = lr.rolling(168).std()
    f['vol_ratio'] = f['vol_24h'] / f['vol_168h']
    for n in (24, 168):
        hh, ll = hi.rolling(n).max(), lo.rolling(n).min()
        f[f'range_pos_{n}h'] = (c - ll) / (hh - ll)
        f[f'dist_high_{n}h'] = (hh - c) / a
        f[f'dist_low_{n}h'] = (c - ll) / a
    for n in (20, 50, 200):
        f[f'ema{n}_dist'] = (c - ind.ema(c, n)) / a
    f['ema20_slope'] = ind.ema(c, 20).diff(4) / a
    f['rsi14'] = ind.rsi(c, 14); f['rsi2'] = ind.rsi(c, 2)
    m = ind.ema(c, 12) - ind.ema(c, 26); f['macd_hist'] = (m - ind.ema(m, 9)) / a
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    f['bb_pctb'] = (c - (mid - 2 * sd)) / (4 * sd); f['bb_width'] = 4 * sd / mid
    p, q = lab._dmi(h, 14); f['adx'] = ind.adx(h, 14); f['dmi_diff'] = p - q
    f['stoch_k'] = 100 * (c - lo.rolling(14).min()) / (hi.rolling(14).max() - lo.rolling(14).min())
    tp = (hi + lo + c) / 3
    f['cci'] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).std() * 0.8)
    mf = tp * v
    pos = mf.where(tp > tp.shift(), 0.0).rolling(14).sum(); neg = mf.where(tp < tp.shift(), 0.0).rolling(14).sum()
    f['mfi'] = 100 - 100 / (1 + pos / neg.replace(0, np.nan))
    f['vol_z20'] = v / v.rolling(20).mean(); f['vol_z168'] = v / v.rolling(168).mean()
    f['taker_share'] = h['taker_buy_volume'] / v.replace(0, np.nan)
    f['candle_body'] = (c - h['open']) / a
    f['upper_wick'] = (hi - np.maximum(c, h['open'])) / a
    f['lower_wick'] = (np.minimum(c, h['open']) - lo) / a
    hr = h.index.hour + 1                                   # the bar closes at hour + 1
    f['hour_sin'] = np.sin(2 * np.pi * hr / 24); f['hour_cos'] = np.cos(2 * np.pi * hr / 24)
    f['weekday'] = (h.index + pd.Timedelta(hours=1)).dayofweek.astype(float)
    h4 = ind.resample(df5, '4h')['close']
    f['trend_4h'] = ind.to_base((ind.ema(h4, 20) - ind.ema(h4, 50)) / h4, '4h', h.index, '1h')
    d = ind.resample(df5, '1D')['close']
    f['ret_1d'] = ind.to_base(np.log(d / d.shift(1)), '1D', h.index, '1h')
    f['ret_7d'] = ind.to_base(np.log(d / d.shift(7)), '1D', h.index, '1h')
    ctx = st.Ctx('X', df5)
    for name, var in (('volume_breakout', 1), ('bb_breakout', 1), ('donchian', 0)):
        lg, sh = lab.signals(ctx, h, name, var)
        f[f'sig_{name}'] = lg.astype(float) - sh.astype(float)
    if other5 is not None:
        oc = ind.resample(other5, '1h')['close'].reindex(h.index)
        for n in (1, 4, 24):
            f[f'other_ret_{n}h'] = np.log(oc / oc.shift(n))
    return h, f


def _labels(df5, h, f, stop_atr=STOP_ATR, rr=RR, min_net=MIN_NET, hold=HOLD):
    """Vectorised forward simulation of a long and a short entered after every 1h close."""
    STOP_ATR, RR, MIN_NET, HOLD = stop_atr, rr, min_net, hold      # local overrides of the module defaults
    o, hi5, lo5, c5 = (df5[k].to_numpy() for k in ('open', 'high', 'low', 'close'))
    idx5 = df5.index
    entry_t = h.index + pd.Timedelta(hours=1)
    e = idx5.searchsorted(entry_t)
    ok = (e < len(idx5) - HOLD) & (idx5[np.minimum(e, len(idx5) - 1)] == entry_t) & f['atr_pct'].notna().to_numpy()
    rows = np.flatnonzero(ok); e = e[ok]
    atr = (f['atr_pct'].to_numpy() * h['close'].to_numpy())[ok]
    H = sliding_window_view(hi5, HOLD)[e]; L = sliding_window_view(lo5, HOLD)[e]
    last_close = c5[e + HOLD - 1]
    out = {}
    cost_rt = TAKER + MAKER + SLIP                          # entry taker + slippage, target exit is a maker order
    for side, s in (('long', 1), ('short', -1)):
        entry = o[e] * (1 + s * SLIP)
        sd = STOP_ATR * atr
        td = np.maximum(RR * sd, entry * (cost_rt + MIN_NET))
        stop, tgt = entry - s * sd, entry + s * td
        stop_hit = (L <= stop[:, None]) if s > 0 else (H >= stop[:, None])
        tgt_hit = (H > (tgt * 1.0001)[:, None]) if s > 0 else (L < (tgt * 0.9999)[:, None])
        fs = np.where(stop_hit.any(1), stop_hit.argmax(1), HOLD)
        ft = np.where(tgt_hit.any(1), tgt_hit.argmax(1), HOLD)
        reason = np.where((fs <= ft) & (fs < HOLD), 0, np.where(ft < HOLD, 1, 2))          # 0 stop, 1 target, 2 time
        exit_px = np.where(reason == 0, stop * (1 - s * SLIP), np.where(reason == 1, tgt, last_close * (1 - s * SLIP)))
        exit_fee = np.where(reason == 1, MAKER, TAKER) * exit_px / entry
        net = s * (exit_px / entry - 1) - TAKER - exit_fee
        bars = np.where(reason == 0, fs, np.where(reason == 1, ft, HOLD - 1)) + 1
        best = (H.max(1) / entry - 1) if s > 0 else (1 - L.min(1) / entry)
        best_bar = (H.argmax(1) if s > 0 else L.argmin(1)) + 1
        out.update({f'label_{side}': (net >= MIN_NET).astype(int), f'net_{side}': net, f'exit_{side}': reason,
                    f'hold_h_{side}': bars / 12, f'stop_{side}': stop, f'target_{side}': tgt, f'entry_{side}': entry,
                    f'hind_best_move_{side}': best, f'hind_best_net_{side}': best - 2 * TAKER - 2 * SLIP,
                    f'hind_best_exit_h_{side}': best_bar / 12})
    lab_df = pd.DataFrame(out, index=h.index[rows])
    lab_df['entry_pos'] = e
    lab_df['entry_time'] = entry_t[rows]
    return lab_df


def build():
    raw = {c: pd.read_pickle(p) for c, p in COINS.items()}
    parts = []
    for coin, df5 in raw.items():
        h, f = _features(df5, raw['ETH' if coin == 'BTC' else 'BTC'])
        lb = _labels(df5, h, f)
        d = f.loc[lb.index].join(lb)
        d['coin'] = coin
        parts.append(d)
        print(f"{coin}: {len(d)} decision points {d.index[0]} -> {d.index[-1]} | share of entries that clear "
              f"costs + 0.30%: long {d['label_long'].mean():.3f}, short {d['label_short'].mean():.3f}", flush=True)
    ds = pd.concat(parts).sort_index()
    ds.to_pickle(OUT)
    print(f'saved {OUT}: {len(ds)} rows, {len([c for c in feature_columns(ds) if c not in ("entry_pos", "entry_time")])} features',
          flush=True)
    return ds


if __name__ == '__main__':
    build()
