"""Order-level trade simulator on 5m OHLC bars with fees, slippage and funding.

Orders (one row per candidate trade, produced by a strategy):
    t          bar position where the order becomes active (signal bar + 1)
    side       +1 long / -1 short
    kind       'market' (fill at open of bar t), 'limit' (maker, resting), 'stop' (taker trigger)
    price      limit/stop trigger price (ignored for market)
    expire     bars the entry order stays live (>=1)
    stop       protective stop price (absolute)
    target     take-profit price (absolute, NaN = none); rests as a maker limit order
    max_bars   bars after fill before a forced market exit at that bar's close
    be_r       move stop to entry once price reaches +be_r R (NaN = never)
    trail      optional chandelier trail distance in price units (NaN = none): the stop ratchets to
               best extreme since entry minus trail, updated after each bar closes

Conservative fill rules:
  * limit entries/targets need price to trade THROUGH the level by 1 bp (touch is not a fill);
  * on any bar where stop and target are both reachable, the stop wins;
  * a limit-entry bar can stop out but cannot also reach the target;
  * stops gap: if a bar opens beyond the stop, the fill is the open (minus slippage);
  * one open position per (symbol, strategy); orders arriving while in a trade are dropped.
"""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

THROUGH = 1e-4


@dataclass(frozen=True)
class Costs:
    taker: float = 5e-4        # Binance USD-M base tier, per fill
    maker: float = 2e-4
    slip: float = 2e-4         # half-spread + slippage on taker fills, per side
    funding: bool = True

    def stressed(self, k):
        return replace(self, taker=self.taker * k, maker=self.maker * k, slip=self.slip * k)


# Majors are tighter; alts get a wider taker slippage assumption.
SLIP = {'BTCUSDT': 1.5e-4, 'ETHUSDT': 1.5e-4}
ALT_SLIP = 3e-4


def costs_for(symbol, base=Costs()):
    return replace(base, slip=SLIP.get(symbol, ALT_SLIP) * base.slip / 2e-4)


def simulate(df, funding, orders, costs, symbol='', strategy='', max_cost_r=None):
    """max_cost_r: skip fills whose estimated round-trip cost exceeds this fraction of the
    stop distance (a trader declines setups whose stop is too tight to pay for execution)."""
    if orders is None or len(orders) == 0:
        return pd.DataFrame()
    o = df['open'].to_numpy(); h = df['high'].to_numpy()
    lo = df['low'].to_numpy(); c = df['close'].to_numpy()
    ts = df.index.as_unit('ns').asi8 // 10**6
    n = len(o)
    if len(funding):
        f_ts = funding.index.as_unit('ns').asi8 // 10**6
        f_cum = np.concatenate([[0.0], np.cumsum(funding.to_numpy())])
    else:
        f_ts = np.array([], dtype='int64'); f_cum = np.array([0.0])

    orders = orders.sort_values('t', kind='stable')
    if 'trail' not in orders:
        orders = orders.assign(trail=np.nan)
    cols = {k: orders[k].to_numpy() for k in ('t', 'side', 'kind', 'price', 'expire', 'stop',
                                               'target', 'max_bars', 'be_r', 'trail')}
    out = []
    free_at = 0
    for r in range(len(orders)):
        t = int(cols['t'][r]); side = int(cols['side'][r]); kind = cols['kind'][r]
        if t < free_at or t >= n:
            continue
        stop = float(cols['stop'][r]); target = float(cols['target'][r])
        # ---- entry ----
        end = min(n, t + max(1, int(cols['expire'][r])))
        if kind == 'market':
            j = t
            entry = o[j] * (1 + side * costs.slip); fee_in = costs.taker; maker_in = False
        else:
            px = float(cols['price'][r])
            if kind == 'limit':
                hit = (lo[t:end] < px * (1 - THROUGH)) if side > 0 else (h[t:end] > px * (1 + THROUGH))
            else:
                hit = (h[t:end] >= px) if side > 0 else (lo[t:end] <= px)
            if not hit.any():
                continue
            j = t + int(hit.argmax())
            if kind == 'limit':
                entry = min(px, o[j]) if side > 0 else max(px, o[j])
                fee_in = costs.maker; maker_in = True
            else:
                entry = (max(px, o[j]) if side > 0 else min(px, o[j])) * (1 + side * costs.slip)
                fee_in = costs.taker; maker_in = False
        risk = side * (entry - stop)
        if not risk > 0 or (not np.isnan(target) and side * (target - entry) <= 0):
            continue
        if max_cost_r is not None:
            est = fee_in + costs.taker + costs.slip * (1 if maker_in else 2)
            if est / (risk / entry) > max_cost_r:
                continue
        # ---- exit scan ----
        last = min(n - 1, j + int(cols['max_bars'][r]))
        be_r = float(cols['be_r'][r])
        k, px_out, reason, maker_out = _exit(o, h, lo, c, j, last, side, entry, stop, target,
                                             risk, be_r, maker_in, costs, float(cols['trail'][r]))
        free_at = k + 1
        gross = side * (px_out - entry) / entry
        fees = fee_in + (costs.maker if maker_out else costs.taker) * px_out / entry
        fund = 0.0
        if costs.funding and len(f_ts):
            a = np.searchsorted(f_ts, ts[j], side='right')
            b = np.searchsorted(f_ts, ts[k] + 300000, side='left')  # events through exit bar
            fund = side * (f_cum[b] - f_cum[a])
        net = gross - fees - fund
        rf = risk / entry
        out.append((symbol, strategy, side, ts[j], ts[k], entry, px_out, stop, target, rf,
                    gross, fees, fund, net, net / rf, reason, k - j + 1))
    return pd.DataFrame(out, columns=['symbol', 'strategy', 'side', 'entry_ts', 'exit_ts', 'entry',
                                      'exit', 'stop', 'target', 'risk_frac', 'gross', 'fees',
                                      'funding', 'net', 'net_r', 'reason', 'bars'])


def _first(mask):
    return int(mask.argmax()) if mask.any() else -1


def _exit(o, h, lo, c, j, last, side, entry, stop, target, risk, be_r, maker_in, costs, trail=np.nan):
    """Return (bar, price, reason, maker_exit)."""
    seg = slice(j, last + 1)
    hs, ls, os_ = h[seg], lo[seg], o[seg]
    has_target = not np.isnan(target)
    stops = np.full(len(hs), stop)
    if not np.isnan(be_r):
        lvl = entry + side * be_r * risk
        reach = (hs >= lvl) if side > 0 else (ls <= lvl)
        b = _first(reach)
        if b >= 0:
            stops[b + 1:] = entry          # active from the bar after the trigger bar closes
    if not np.isnan(trail) and len(hs) > 1:
        if side > 0:
            ratchet = np.maximum.accumulate(hs)[:-1] - trail
            stops[1:] = np.maximum(stops[1:], ratchet)
        else:
            ratchet = np.minimum.accumulate(ls)[:-1] + trail
            stops[1:] = np.minimum(stops[1:], ratchet)
    stop_hit = (ls <= stops) if side > 0 else (hs >= stops)
    if has_target:
        tgt_hit = (hs > target * (1 + THROUGH)) if side > 0 else (ls < target * (1 - THROUGH))
        if maker_in:
            tgt_hit[0] = False             # entry bar: order of fill vs. target unknown
    else:
        tgt_hit = np.zeros(len(hs), dtype=bool)
    s = _first(stop_hit); g = _first(tgt_hit)
    if s >= 0 and (g < 0 or s <= g):
        sp = stops[s]
        gap = os_[s] < sp if side > 0 else os_[s] > sp
        px = os_[s] if (gap and s > 0) else sp
        reason = 'stop' if sp == stop else 'breakeven' if sp == entry else 'trail'
        return j + s, px * (1 - side * costs.slip), reason, False
    if g >= 0:
        gap = os_[g] > target if side > 0 else os_[g] < target
        px = os_[g] if (gap and g > 0) else target
        return j + g, px, 'target', True
    return last, c[last] * (1 - side * costs.slip), 'time', False
