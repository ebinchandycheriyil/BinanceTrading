"""Chronological replay with next-bar entry, costs, and conservative bar stops."""
from dataclasses import replace
from collections import Counter
from .engine import PaperEngine, Quote, summarize
from .signals import analyze


def replay(data, cfg, start_ms=None, end_ms=None, signals=None, funding=None):
    # Replay output is saved as one artifact; per-tick SQLite checkpoints add no
    # recovery value to a deterministic replay and make long studies expensive.
    decisions = Counter()
    def record(event):
        if event["event"] == "SIGNAL":
            decisions[event["reason"]] += 1
    engine = PaperEngine(cfg, persist=False, emit=record)
    by_symbol = {s: {c.ts: c for c in data[s]} for s in cfg.symbols}
    timeline = sorted(set.intersection(*(set(v) for v in by_symbol.values())))
    histories = {s: [] for s in cfg.symbols}
    if not timeline:
        raise ValueError("No overlapping candles")
    last_quotes, last_time = {}, None
    funding_buckets = {}
    for s, rows in (funding or {}).items():
        for row in rows:
            bucket = row["ts"] // 60000 * 60000
            funding_buckets.setdefault(bucket, []).append((s, row))
    for ts in timeline:
        if end_ms is not None and ts >= end_ms:
            break
        now = ts / 1000
        bars = {s: rows[ts] for s, rows in by_symbol.items()}
        spread = cfg.max_spread_bps / 20000
        quotes = {s: Quote(c.open * (1 - spread), c.open * (1 + spread), now) for s, c in bars.items()}
        if start_ms is None or ts >= start_ms:
            # Minute replay convention: settlement before exits/entries at that
            # minute's open. Funding is never used as a signal feature.
            for s, row in funding_buckets.get(ts, []):
                engine.apply_funding(s, now, row["rate"], row["mark"])
            engine.monitor(quotes, now)
            # Histories exclude this bar: signals precede its open.
            for s in cfg.symbols:
                if histories[s]:
                    sig = signals[(s, ts)] if signals is not None else analyze(histories[s], cfg)
                    engine.enter(s, sig, histories[s][-1].ts, quotes, now)
            # Unknown high/low sequence: stop wins if both levels are touched.
            for s, p in list(engine.s["positions"].items()):
                c = bars[s]
                sign = 1 if p["side"] == "BUY" else -1
                slip = cfg.slippage_bps / 10000
                low_exit = c.low * (1 - spread) * (1 - slip) if sign == 1 else c.low * (1 + spread) * (1 + slip)
                high_exit = c.high * (1 - spread) * (1 - slip) if sign == 1 else c.high * (1 + spread) * (1 + slip)
                stop_hit = low_exit <= p["stop"] if sign == 1 else high_exit >= p["stop"]
                target_hit = high_exit >= p["target"] if sign == 1 else low_exit <= p["target"]
                if stop_hit or target_hit:
                    level = p["stop"] if stop_hit else p["target"]
                    # Thresholds are executable prices, matching monitor().
                    # Invert the fill factors; do not charge spread/slip twice.
                    factor = (1 - spread) * (1 - slip) if sign == 1 else (1 + spread) * (1 + slip)
                    mid = level / factor
                    trigger = Quote(mid * (1 - spread), mid * (1 + spread), now + 30)
                    event = engine._close(s, trigger, now + 30, "STOP" if stop_hit else "TARGET")
                    engine.commit([event])
            close_quotes = {s: Quote(c.close * (1 - spread), c.close * (1 + spread), now + 59) for s, c in bars.items()}
            engine.monitor(close_quotes, now + 59)
            last_quotes, last_time = close_quotes, now + 59
        for s, c in bars.items():
            histories[s].append(c)
            histories[s] = histories[s][-400:]
    if last_time is not None:
        engine.monitor(last_quotes, last_time, force_reason="END_OF_SAMPLE")
    result = dict(summary=summarize(engine.s["trades"]), trades=engine.s["trades"],
                  skipped_signals=dict(decisions))
    engine.journal.close()
    return result


def study(data, cfg, progress=None):
    common = sorted(set.intersection(*({c.ts for c in data[s]} for s in cfg.symbols)))
    if len(common) < 900:
        raise ValueError("Need at least 900 overlapping minutes for chronological studies")
    eligible = common[400:]
    split = eligible[int(len(eligible) * 0.7)]
    results = {}
    for name in ("confluence", "ema_only", "no_volume"):
        if progress:
            progress(dict(event="STUDY", variant=name))
        experiment = replace(cfg, strategy=name)
        results[name] = {
            "development": replay(data, experiment, start_ms=eligible[0], end_ms=split)["summary"],
            "holdout": replay(data, experiment, start_ms=split),
        }
    stressed = replace(cfg, fee_bps=cfg.fee_bps * 2, slippage_bps=cfg.slippage_bps * 2)
    if progress:
        progress(dict(event="STUDY", variant="double_costs"))
    results["double_costs"] = {"holdout": replay(data, stressed, start_ms=split)}
    return dict(split_ms=split, bars=len(common), studies=results,
                limitations=["Historical replay is not a profit forecast",
                             "Assumed spread; no historical book depth, partial fills or latency",
                             "Funding, liquidation and taxes not modeled; gross exposure capped at 1x",
                             "Intrabar stop wins ties; excursions are sampled and incomplete",
                             "Further tuning after inspecting holdout requires new unseen data"])
