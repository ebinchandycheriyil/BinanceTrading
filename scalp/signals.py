"""Deterministic indicators on CLOSED 1m candles; no native TA-Lib dependency."""
from dataclasses import dataclass, field, replace
import math

from .settings import Settings


@dataclass(frozen=True)
class Candle:
    ts: int  # UTC open time in milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        if self.ts % 60000 or self.ts < 0:
            raise ValueError("Candle timestamp must be aligned to a UTC minute")
        if not all(math.isfinite(x) for x in (self.open, self.high, self.low, self.close, self.volume)):
            raise ValueError("Nonfinite candle")
        if self.low <= 0 or self.volume < 0 or self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Invalid OHLCV candle")


@dataclass(frozen=True)
class Signal:
    side: str = "HOLD"
    reason: str = ""
    stop_fraction: float = 0.0
    features: dict = field(default_factory=dict)
    stop_price: float | None = None
    target_price: float | None = None


def ema(values, period):
    result = [values[0]]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result.append(result[-1] + alpha * (value - result[-1]))
    return result


def wilder(values, period=14):
    result = [sum(values[:period]) / period]
    for value in values[period:]:
        result.append((result[-1] * (period - 1) + value) / period)
    return result


def aggregate_five(candles):
    groups = {}
    for c in candles:
        groups.setdefault(c.ts // 300000, []).append(c)
    result = []
    for bucket, rows in sorted(groups.items()):
        if len(rows) == 5 and [c.ts for c in rows] == [bucket * 300000 + i * 60000 for i in range(5)]:
            result.append(Candle(rows[0].ts, rows[0].open, max(c.high for c in rows),
                                 min(c.low for c in rows), rows[-1].close,
                                 sum(c.volume for c in rows)))
    return result


def analyze(candles, cfg: Settings):
    if cfg.strategy in ("momentum5", "momentum15"):
        from .momentum import momentum_signal
        return momentum_signal(candles, cfg)
    if cfg.strategy in ("band_reentry", "retest", "trend_pullback"):
        from .chart_setups import all_chart_signals
        return all_chart_signals(candles, cfg)[cfg.strategy]
    if len(candles) < 300:
        return Signal(reason="warmup: need 300 closed 1m candles")
    candles = candles[-400:]
    if any(b.ts - a.ts != 60000 for a, b in zip(candles, candles[1:])):
        return Signal(reason="missing or duplicate candles")
    closes = [c.close for c in candles]
    fast, slow = ema(closes, 9), ema(closes, 21)
    higher = aggregate_five(candles)
    if len(higher) < 50:
        return Signal(reason="incomplete higher timeframe")
    hc = [c.close for c in higher]
    bias = 1 if ema(hc, 20)[-1] > ema(hc, 50)[-1] else -1
    tr, gains, losses, plus, minus = [], [], [], [], []
    for prev, cur in zip(candles, candles[1:]):
        tr.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
        delta = cur.close - prev.close
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
        up, down = cur.high - prev.high, prev.low - cur.low
        plus.append(up if up > down and up > 0 else 0)
        minus.append(down if down > up and down > 0 else 0)
    atrs = wilder(tr)
    dx = [100 * abs(p - m) / (p + m) if p + m else 0
          for p, m in zip(wilder(plus), wilder(minus))]
    atr, adx = atrs[-1], wilder(dx)[-1]
    gain, loss = wilder(gains)[-1], wilder(losses)[-1]
    rsi = 100 - 100 / (1 + gain / loss) if loss else (100 if gain else 50)
    macd = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    histogram = macd[-1] - ema(macd, 9)[-1]
    window = candles[-60:]
    total_volume = sum(c.volume for c in window)
    vwap = sum((c.high + c.low + c.close) / 3 * c.volume for c in window) / total_volume if total_volume else closes[-1]
    volume_base = sum(c.volume for c in candles[-21:-1]) / 20
    volume_ratio = candles[-1].volume / volume_base if volume_base else 0
    stop = max(cfg.min_stop_fraction, cfg.atr_stop_multiple * atr / closes[-1])
    features = dict(atr=atr, adx=adx, rsi=rsi, macd_hist=histogram, vwap=vwap,
                    volume_ratio=volume_ratio, bias=bias, strategy=cfg.strategy,
                    close=closes[-1], atr_fraction=atr / closes[-1])
    if stop > cfg.max_stop_fraction:
        return Signal(reason="volatility too high", features=features)
    # Pullback/reclaim or a fresh crossover; no entry just because alignment persists.
    long_trigger = (fast[-2] <= slow[-2] and fast[-1] > slow[-1]) or (candles[-2].low <= fast[-2] and closes[-2] <= fast[-2] and closes[-1] > fast[-1])
    short_trigger = (fast[-2] >= slow[-2] and fast[-1] < slow[-1]) or (candles[-2].high >= fast[-2] and closes[-2] >= fast[-2] and closes[-1] < fast[-1])
    direction = 1 if bias == 1 and fast[-1] > slow[-1] and long_trigger else -1 if bias == -1 and fast[-1] < slow[-1] and short_trigger else 0
    if cfg.strategy == "breakout":
        upper = max(c.high for c in candles[-21:-1])
        lower = min(c.low for c in candles[-21:-1])
        direction = (1 if bias == 1 and closes[-1] > upper and fast[-1] > slow[-1]
                     else -1 if bias == -1 and closes[-1] < lower and fast[-1] < slow[-1] else 0)
    if not direction:
        return Signal(reason="waiting for trend-aligned trigger", features=features)
    if cfg.strategy != "ema_only":
        filters = {
            "weak trend": adx >= cfg.adx_min,
            "RSI extension": 45 <= rsi <= 70 if direction == 1 else 30 <= rsi <= 55,
            "MACD disagreement": histogram * direction > 0,
            "VWAP disagreement": (closes[-1] - vwap) * direction > 0,
            "overextended from EMA": abs(closes[-1] - fast[-1]) <= 1.5 * atr,
        }
        if cfg.strategy != "no_volume":
            filters["low volume"] = volume_ratio >= cfg.volume_ratio_min
        failed = [name for name, ok in filters.items() if not ok]
        if failed:
            return Signal(reason=", ".join(failed), features=features)
    return Signal("BUY" if direction == 1 else "SELL",
                  "volume-confirmed 20-bar breakout" if cfg.strategy == "breakout" else "trend pullback/crossover confirmed",
                  stop, features)


def cost_filter(signal, cfg, spread_bps):
    """ATR horizon scaling is a volatility screen, not a return prediction."""
    if signal.side == "HOLD" or cfg.min_move_cost_ratio == 0:
        return signal
    costs = (2 * cfg.fee_bps + 2 * cfg.slippage_bps + spread_bps) / 10000
    movement = signal.features.get("atr_fraction", 0) * math.sqrt(cfg.min_hold_minutes)
    ratio = movement / costs if costs > 0 else 1e12
    features = dict(signal.features, movement_cost_ratio=ratio)
    if ratio < cfg.min_move_cost_ratio:
        return Signal(reason="expected volatility budget too small relative to costs", features=features)
    return replace(signal, features=features)
