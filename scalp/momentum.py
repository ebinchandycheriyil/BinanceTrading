"""Closed-bar time-series momentum with volume and trend confirmation."""
import math
from .signals import Signal, ema


def momentum_signal(candles, cfg):
    if len(candles) < 65:
        return Signal(reason='momentum warmup')
    rows = candles[-65:]
    if any(b.ts-a.ts != 60000 for a,b in zip(rows,rows[1:])):
        return Signal(reason='missing momentum candles')
    horizon = 5 if cfg.strategy == 'momentum5' else 15
    closes = [c.close for c in rows]
    fast, slow = ema(closes, 9), ema(closes, 21)
    atr = sum(max(b.high-b.low,abs(b.high-a.close),abs(b.low-a.close))
              for a,b in zip(rows[-15:-1],rows[-14:]))/14
    price = closes[-1]
    threshold = max(.0015, 1.5*atr/price)
    move = math.log(price/closes[-1-horizon])
    prior = math.log(closes[-2]/closes[-2-horizon])
    volume_base = sum(c.volume for c in rows[-21:-1])/20
    relative_volume = rows[-1].volume/volume_base if volume_base else 0
    sign = 1 if move > threshold else -1 if move < -threshold else 0
    features = dict(momentum=move, horizon=horizon, atr_fraction=atr/price,
                    volume_ratio=relative_volume, threshold=threshold)
    if not sign or relative_volume < 1.2:
        return Signal(reason='momentum or volume too weak',features=features)
    if sign*(fast[-1]-slow[-1]) <= 0 or sign*(slow[-1]-slow[-6]) <= 0:
        return Signal(reason='momentum trend disagreement',features=features)
    # Fresh threshold crossing or five-bar price breakout, not every aligned bar.
    breakout = price > max(c.high for c in rows[-6:-1]) if sign == 1 else price < min(c.low for c in rows[-6:-1])
    if sign*prior > threshold and not breakout:
        return Signal(reason='waiting for fresh momentum trigger',features=features)
    if abs(price-fast[-1]) > 3*atr:
        return Signal(reason='momentum overextended',features=features)
    stop = max(cfg.min_stop_fraction,cfg.atr_stop_multiple*atr/price)
    if stop > cfg.max_stop_fraction:
        return Signal(reason='momentum volatility too high',features=features)
    return Signal('BUY' if sign == 1 else 'SELL',cfg.strategy,stop,features)
