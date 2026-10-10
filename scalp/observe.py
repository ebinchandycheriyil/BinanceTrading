"""Bounded public-book observation; no signals, accounts or orders."""
import argparse
import json
from pathlib import Path
import statistics
import time
from .market import BinanceData
from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--output", default="artifacts/round2/book_observation.json")
    args = parser.parse_args()
    if not 10 <= args.seconds <= 600:
        parser.error("--seconds must be between 10 and 600")
    cfg = Settings()
    market = BinanceData(cfg.symbols)
    observations, errors = [], []
    start = time.monotonic()
    while time.monotonic() - start < args.seconds:
        for symbol in cfg.symbols:
            try:
                q = market.quote(symbol)
                now = time.time()
                if not q.valid(now, cfg.stale_seconds):
                    raise ValueError("Stale/invalid book")
                observations.append(dict(symbol=symbol, ts=q.ts, received=now,
                                         spread_bps=q.spread_bps, bid=q.bid, ask=q.ask,
                                         bid_quantity=q.bid_size, ask_quantity=q.ask_size))
            except Exception as exc:
                errors.append(dict(symbol=symbol, error=str(exc), ts=time.time()))
        time.sleep(min(5, max(0, args.seconds - (time.monotonic() - start))))
    summary = {}
    for symbol in cfg.symbols:
        spreads = [r["spread_bps"] for r in observations if r["symbol"] == symbol]
        summary[symbol] = (dict(samples=len(spreads), median_spread_bps=statistics.median(spreads),
                                max_spread_bps=max(spreads)) if spreads else dict(samples=0))
    result = dict(summary=summary, observations=observations, errors=errors,
                  limitation="Short present-time snapshot, not historical spread evidence; does not measure slippage or prove fills")
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
