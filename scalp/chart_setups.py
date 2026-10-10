"""Research-inspired five-minute chart hypotheses, not proven trading systems.

All references and precise adaptations are recorded in artifacts/round3/SOURCES.md.
"""
import statistics
from .signals import Signal, aggregate_five, ema, wilder


NAMES = ("band_reentry", "retest", "trend_pullback")


def context(candles):
    if len(candles) < 300 or candles[-1].ts % 300000 != 240000:
        return None
    candles = candles[-400:]
    if any(b.ts - a.ts != 60000 for a, b in zip(candles, candles[1:])):
        return None
    bars = aggregate_five(candles)
    if len(bars) < 55:
        return None
    closes = [c.close for c in bars]
    tr, plus, minus = [], [], []
    for a, b in zip(bars, bars[1:]):
        tr.append(max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close)))
        up, down = b.high - a.high, a.low - b.low
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)
    atr = wilder(tr)[-1]
    if atr <= 0:
        return None
    dx = [100 * abs(p - m) / (p + m) if p + m else 0
          for p, m in zip(wilder(plus), wilder(minus))]
    adx = wilder(dx)[-1]
    ef, es = ema(closes, 20), ema(closes, 50)
    center = statistics.mean(closes[-20:])
    sd = statistics.pstdev(closes[-20:])
    prev_center = statistics.mean(closes[-21:-1])
    prev_sd = statistics.pstdev(closes[-21:-1])
    volumes = [c.volume for c in bars]
    base_volume = statistics.mean(volumes[-21:-1])
    prev_base_volume = statistics.mean(volumes[-22:-2])
    return dict(bars=bars, atr=atr, adx=adx, ef=ef, es=es, center=center, sd=sd,
                prev_center=prev_center, prev_sd=prev_sd,
                volume_ratio=volumes[-1] / base_volume if base_volume else 0,
                prev_volume_ratio=volumes[-2] / prev_base_volume if prev_base_volume else 0)


def plans(ctx):
    """Map each method to a side, invalidation stop and price-based objective."""
    b = ctx["bars"]
    prev, cur = b[-2:]
    atr, adx, ef, es = (ctx[k] for k in ("atr", "adx", "ef", "es"))
    trend = 1 if ef[-1] > es[-1] and ef[-1] > ef[-3] else -1 if ef[-1] < es[-1] and ef[-1] < ef[-3] else 0
    result = {}
    # Re-entry after an outside close, only in a low-ADX, comparatively flat
    # regime. A band touch alone is deliberately insufficient.
    if adx < 20 and abs(ef[-1] - es[-1]) <= atr and .8 <= ctx["volume_ratio"] <= 2:
        lower, upper = ctx["center"] - 2 * ctx["sd"], ctx["center"] + 2 * ctx["sd"]
        if prev.close < ctx["prev_center"] - 2 * ctx["prev_sd"] and lower < cur.close < ctx["center"] and cur.close > cur.open:
            result["band_reentry"] = ("BUY", min(prev.low, cur.low) - .25 * atr, ctx["center"])
        elif prev.close > ctx["prev_center"] + 2 * ctx["prev_sd"] and ctx["center"] < cur.close < upper and cur.close < cur.open:
            result["band_reentry"] = ("SELL", max(prev.high, cur.high) + .25 * atr, ctx["center"])
    # Breakout level excludes BOTH the breakout and retest candles.
    upper, lower = max(c.high for c in b[-22:-2]), min(c.low for c in b[-22:-2])
    if adx >= 25 and ctx["prev_volume_ratio"] >= 1.5:
        if trend == 1 and prev.close > upper and cur.low <= upper + .25 * atr and cur.close > upper and cur.close > cur.open:
            stop = min(cur.low, upper) - .5 * atr
            target = cur.close + min(2.5 * (cur.close - stop), upper - lower)
            result["retest"] = ("BUY", stop, target)
        elif trend == -1 and prev.close < lower and cur.high >= lower - .25 * atr and cur.close < lower and cur.close < cur.open:
            stop = max(cur.high, lower) + .5 * atr
            target = cur.close - min(2.5 * (stop - cur.close), upper - lower)
            result["retest"] = ("SELL", stop, target)
    # Trend pullback: reclaim EMA20 after a close through it. Stop is beyond
    # the three-bar swing; objective is capped at the prior resistance/support.
    if adx >= 25 and ctx["volume_ratio"] >= 1.2:
        if trend == 1 and prev.close <= ef[-2] and cur.close > ef[-1] and cur.close > cur.open:
            stop = min(c.low for c in b[-3:]) - .25 * atr
            target = min(max(c.high for c in b[-21:-1]), cur.close + 2.5 * (cur.close - stop))
            result["trend_pullback"] = ("BUY", stop, target)
        elif trend == -1 and prev.close >= ef[-2] and cur.close < ef[-1] and cur.close < cur.open:
            stop = max(c.high for c in b[-3:]) + .25 * atr
            target = max(min(c.low for c in b[-21:-1]), cur.close - 2.5 * (stop - cur.close))
            result["trend_pullback"] = ("SELL", stop, target)
    return result


def all_chart_signals(candles, cfg):
    result = {name: Signal(reason="waiting for a complete 5m chart setup") for name in NAMES}
    ctx = context(candles)
    if ctx is None:
        return result
    close = ctx["bars"][-1].close
    for name, (side, stop, target) in plans(ctx).items():
        sign = 1 if side == "BUY" else -1
        stop_fraction = sign * (close - stop) / close
        if not cfg.min_stop_fraction <= stop_fraction <= cfg.max_stop_fraction or sign * (target - close) <= 0:
            result[name] = Signal(reason="structural stop/target outside allowed range")
            continue
        features = dict(strategy=name, atr=ctx["atr"], adx=ctx["adx"], volume_ratio=ctx["volume_ratio"],
                        close=close, atr_fraction=ctx["atr"] / close, signal_timeframe="5m")
        result[name] = Signal(side, name + " confirmed on closed 5m bar", stop_fraction, features, stop, target)
    return result
