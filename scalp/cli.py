import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
import time
from .engine import PaperEngine, summarize
from .market import BinanceData
from .research import study
from .settings import load_settings
from .signals import Candle, analyze


def output(event):
    print(json.dumps(event, allow_nan=False), flush=True)


class AccountLock:
    """OS-held lock automatically releases on crash; prevents duplicate writers."""
    def __init__(self, db):
        self.path = str(Path(db).resolve()) + ".lock"

    def __enter__(self):
        self.file = open(self.path, "a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Another process owns this paper account") from None
        return self

    def __exit__(self, *args):
        self.file.close()


def run_paper(args, cfg):
    market = BinanceData(cfg.symbols)
    with AccountLock(args.db):
        engine = PaperEngine(cfg, args.db, emit=output)
        candles, quotes = {}, {}
        next_refresh = 0
        start = time.monotonic()
        output(dict(event="START", mode="PAPER", settings=asdict(cfg), db=str(Path(args.db).resolve())))
        try:
            while True:
                cycle_start = time.monotonic()
                quotes = {}
                for s in cfg.symbols:
                    try:
                        quotes[s] = market.quote(s)
                    except Exception as exc:
                        output(dict(event="DATA_ERROR", symbol=s, error=str(exc)))
                engine.monitor(quotes, time.time())
                if time.time() >= next_refresh:
                    for s in cfg.symbols:
                        try:
                            candles[s] = market.candles(s)
                        except Exception as exc:
                            candles.pop(s, None)
                            output(dict(event="CANDLE_ERROR", symbol=s, error=str(exc)))
                    next_refresh = (int(time.time()) // 60 + 1) * 60 + 2
                    for s in cfg.symbols:
                        try:
                            quotes[s] = market.quote(s)
                        except Exception:
                            quotes.pop(s, None)
                    engine.monitor(quotes, time.time())
                    for s in cfg.symbols:
                        if candles.get(s):
                            engine.enter(s, analyze(candles[s], cfg), candles[s][-1].ts,
                                         quotes, time.time(), market.round_qty, **market.minimums(s))
                output(engine.status(quotes, time.time()))
                if args.once or (args.minutes and time.monotonic() - start >= args.minutes * 60):
                    break
                time.sleep(max(0.1, cfg.poll_seconds - (time.monotonic() - cycle_start)))
        except KeyboardInterrupt:
            output(dict(event="INTERRUPT", mode="PAPER"))
        finally:
            if not args.once:
                fresh = {}
                for s in engine.s["positions"]:
                    try:
                        fresh[s] = market.quote(s)
                    except Exception:
                        pass
                engine.monitor(fresh, time.time(), force_reason="SHUTDOWN")
            output(dict(event="SUMMARY", mode="PAPER", **summarize(engine.s["trades"]),
                        remaining_open=len(engine.s["positions"])))
            engine.journal.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Raw Binance futures PAPER trading. No private API or live orders.")
    parser.add_argument("--config", help="JSON settings; does not read .env")
    sub = parser.add_subparsers(dest="command", required=True)
    paper = sub.add_parser("paper", help="Monitor public prices and simulate orders")
    paper.add_argument("--db", default="paper_trades.db")
    paper.add_argument("--once", action="store_true", help="One cycle; simulated positions persist")
    paper.add_argument("--minutes", type=float, default=0, help="0 runs until Ctrl+C")
    report = sub.add_parser("report")
    report.add_argument("--db", default="paper_trades.db")
    report.add_argument("--output", default="artifacts/paper_report.json")
    fetch = sub.add_parser("download")
    fetch.add_argument("--days", type=int, default=7)
    fetch.add_argument("--output", default="artifacts/history.json")
    research = sub.add_parser("study")
    research.add_argument("--input", default="artifacts/history.json")
    research.add_argument("--output", default="artifacts/study.json")
    args = parser.parse_args(argv)
    cfg = load_settings(args.config)
    if args.command == "paper":
        if not math.isfinite(args.minutes) or args.minutes < 0:
            parser.error("--minutes must be finite and nonnegative")
        run_paper(args, cfg)
        return
    if args.command == "report":
        if not Path(args.db).exists():
            parser.error("Paper account does not exist")
        engine = PaperEngine(cfg, args.db)
        result = dict(mode="PAPER", summary=summarize(engine.s["trades"]),
                      trades=engine.s["trades"], positions=engine.s["positions"])
        engine.journal.close()
    elif args.command == "download":
        if not 1 <= args.days <= 30:
            parser.error("--days must be between 1 and 30")
        market = BinanceData(cfg.symbols)
        end = int(time.time() // 60) * 60000
        result = dict(source="Binance USD-M via CCXT", end_ms=end, data={})
        for s in cfg.symbols:
            rows = market.history(s, end - args.days * 86400000, end)
            result["data"][s] = [asdict(c) for c in rows]
            output(dict(event="DOWNLOADED", symbol=s, bars=len(rows)))
    else:
        with open(args.input, encoding="utf-8") as handle:
            raw = json.load(handle)
        data = {s: [Candle(**c) for c in rows] for s, rows in raw["data"].items()}
        for s in cfg.symbols:
            timestamps = [c.ts for c in data[s]]
            if timestamps != sorted(set(timestamps)):
                raise ValueError("History must be sorted with unique timestamps")
        result = study(data, cfg, progress=output)
        result["settings"] = asdict(cfg)
        result["source"] = raw.get("source")
        result["input"] = str(Path(args.input).resolve())
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    output(dict(event="SAVED", path=str(path.resolve())))
    if "summary" in result:
        output(result["summary"])


if __name__ == "__main__":
    main()
