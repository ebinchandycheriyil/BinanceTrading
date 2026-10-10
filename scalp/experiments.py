"""A preregistered development/validation experiment; never reads trade databases.

python -m scalp.experiments download
python -m scalp.experiments run
"""
import argparse
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random

from .market import BinanceData
from .research import replay
from .settings import Settings
from .signals import Candle, analyze

ROOT = Path("artifacts/round2")
DAY = 86400000


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


def output(**event):
    print(json.dumps(event, allow_nan=False), flush=True)


def save(path, content):
    path.write_text(json.dumps(content, indent=2, allow_nan=False), encoding="utf-8")


def candidates():
    base = Settings()
    return dict(
        baseline=base,
        patience15=replace(base, min_hold_minutes=15),
        patience30=replace(base, min_hold_minutes=30),
        cost_gate15=replace(base, min_hold_minutes=15, min_move_cost_ratio=2),
        breakout15=replace(base, min_hold_minutes=15, min_move_cost_ratio=2,
                           strategy="breakout", volume_ratio_min=1.2),
    )


def protocol():
    return dict(
        version=2,
        development_start=stamp("2026-08-29"),
        development_split=stamp("2026-09-05"),
        validation_start=stamp("2026-09-12"),
        validation_end=stamp("2026-09-19"),
        warmup_minutes=400,
        candidates={name: asdict(cfg) for name, cfg in candidates().items()},
        selection="Maximize the worse of two development-fold net P&Ls; tie-break by name. No validation input.",
        pass_rule="Both development folds: net>0, trades>=20, PF>=1.15. Validation: net>0, trades>=30, PF>=1.15, day-block bootstrap 95% lower>0. Doubled-cost validation net>0.",
        notes=["Earlier, newly downloaded market history; independent of the first seven-day sample",
               "This is retrospective validation, not prospective forward evidence",
               "No access to trades.db, .env, or the previous testnet trade records",
               "Fees 5 bps/side, slip 2 bps/side, spread 4 bps; stress doubles fees/slip",
               "Historical funding uses actual event mark/rate, settled at minute-open before entries",
               "No historical book depth, partial fills or historical exchange lot limits",
               "No automatic activation or default strategy change"],
    )


def download():
    ROOT.mkdir(parents=True, exist_ok=True)
    plan = protocol()
    save(ROOT / "protocol.json", plan)  # Freeze hypotheses before obtaining outcomes.
    cfg = Settings()
    market = BinanceData(cfg.symbols)
    start = plan["development_start"] - plan["warmup_minutes"] * 60000
    end = plan["validation_end"]
    result = dict(source="Binance USD-M public CCXT", start_ms=start, end_ms=end,
                  data={}, funding={})
    for symbol in cfg.symbols:
        rows = market.history(symbol, start, end)
        expected = list(range(start, end, 60000))
        if [r.ts for r in rows] != expected:
            raise ValueError(f"Incomplete/irregular history for {symbol}; cannot run declared experiment")
        result["data"][symbol] = [asdict(c) for c in rows]
        result["funding"][symbol] = market.funding_history(symbol, start, end)
        if not result["funding"][symbol]:
            raise ValueError(f"Missing funding history for {symbol}")
        output(event="DOWNLOAD", symbol=symbol, candles=len(rows), funding_events=len(result["funding"][symbol]))
    save(ROOT / "market_data.json", result)
    output(event="SAVED", path=str(ROOT / "market_data.json"))


def cache_signals(data, cfg, start, end):
    """Only past candles are used. Reused across exit/cost experiments."""
    result = {}
    for symbol in cfg.symbols:
        rows = data[symbol]
        for i, c in enumerate(rows):
            if start <= c.ts < end and i > 0:
                result[(symbol, c.ts)] = analyze(rows[max(0, i - 400):i], cfg)
                if i % 5000 == 0:
                    output(event="SIGNALS", strategy=cfg.strategy, symbol=symbol, bars=i)
    return result


def day_bootstrap(trades, start, end, samples=5000):
    days = {ts: 0.0 for ts in range(start, end, DAY)}
    for t in trades:
        bucket = int(t["exit_time"] * 1000) // DAY * DAY
        if bucket in days:
            days[bucket] += t["net_pnl"]
    values = list(days.values())
    rng = random.Random(20260926)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(samples))
    return dict(daily_pnl=days, mean_daily_net=sum(values) / len(values),
                lower95=means[int(samples * .025)], upper95=means[int(samples * .975)],
                method="IID resampling of whole UTC days, preserving within-day trade correlation; few days limits confidence")


def choose(development):
    return sorted(development, key=lambda name: (-min(f["summary"]["net_pnl"] for f in development[name]), name))[0]


def fixed_trade_cost_stress(trades, cfg):
    """Accounting sensitivity only; freeze trade selection, size and exit time."""
    original_slip = cfg.slippage_bps / 10000
    stressed_slip = 2 * original_slip
    values = []
    for trade in trades:
        sign = 1 if trade["side"] == "BUY" else -1
        entry = trade["entry"] * (1 + sign * stressed_slip) / (1 + sign * original_slip)
        exit_price = trade["exit"] * (1 - sign * stressed_slip) / (1 - sign * original_slip)
        gross = sign * (exit_price - entry) * trade["qty"]
        fees = (entry + exit_price) * trade["qty"] * 2 * cfg.fee_bps / 10000
        net = gross - fees - trade.get("funding_cost", 0)
        values.append(dict(id=trade["id"], symbol=trade["symbol"], base_net=trade["net_pnl"], stressed_net=net))
    return dict(trades=len(values), net_pnl=sum(t["stressed_net"] for t in values),
                per_trade=values, method="Same positions, size and exit timestamps; double fees/adverse slippage. Not a replay of changed stops or liquidity.")


def run():
    plan = json.loads((ROOT / "protocol.json").read_text())
    if plan != json.loads(json.dumps(protocol())):
        raise ValueError("Protocol changed; create a new declared experiment")
    raw_bytes = (ROOT / "market_data.json").read_bytes()
    raw = json.loads(raw_bytes)
    data = {s: [Candle(**r) for r in rows] for s, rows in raw["data"].items()}
    expected = list(range(plan["development_start"] - 400 * 60000, plan["validation_end"], 60000))
    if any([c.ts for c in data[s]] != expected for s in Settings().symbols):
        raise ValueError("Input does not match the complete preregistered interval")
    start, split, valid, end = (plan[k] for k in ("development_start", "development_split", "validation_start", "validation_end"))
    configs = candidates()
    # Validation bars remain excluded from all signal preparation and replay
    # until selection has been durably written.
    dev_data = {s: [c for c in rows if c.ts < valid] for s, rows in data.items()}
    standard = cache_signals(dev_data, configs["baseline"], start, valid)
    breakout = cache_signals(dev_data, configs["breakout15"], start, valid)
    development = {}
    for name, cfg in configs.items():
        cache = breakout if name == "breakout15" else standard
        development[name] = []
        for a, b in ((start, split), (split, valid)):
            output(event="DEVELOPMENT", variant=name, start=a, end=b)
            result = replay(dev_data, cfg, a, b, signals=cache, funding=raw["funding"])
            development[name].append(result)
            output(event="RESULT", variant=name, **result["summary"])
    selected = choose(development)
    selection = dict(selected=selected, development=development,
                     dataset_sha256=hashlib.sha256(raw_bytes).hexdigest(), protocol=plan)
    save(ROOT / "selection_before_validation.json", selection)
    output(event="SELECTION_FROZEN", variant=selected)
    cfg = configs[selected]
    validation_cache = cache_signals(data, cfg, valid, end)
    validation = replay(data, cfg, valid, end, signals=validation_cache, funding=raw["funding"])
    stress_cfg = replace(cfg, fee_bps=cfg.fee_bps * 2, slippage_bps=cfg.slippage_bps * 2)
    stressed = replay(data, stress_cfg, valid, end, signals=validation_cache, funding=raw["funding"])
    baseline_cache = validation_cache if cfg.strategy == "confluence" else cache_signals(data, configs["baseline"], valid, end)
    baseline = replay(data, configs["baseline"], valid, end, signals=baseline_cache, funding=raw["funding"])
    uncertainty = day_bootstrap(validation["trades"], valid, end)
    def good(summary, count):
        pf = summary["profit_factor"]
        return summary["trades"] >= count and summary["net_pnl"] > 0 and (pf is None or pf >= 1.15)
    passed = (all(good(r["summary"], 20) for r in development[selected])
              and good(validation["summary"], 30) and uncertainty["lower95"] > 0
              and stressed["summary"]["net_pnl"] > 0)
    result = dict(selection, validation=validation, cost_stress=stressed, baseline_validation=baseline,
                  fixed_trade_cost_stress=fixed_trade_cost_stress(validation["trades"], cfg),
                  uncertainty=uncertainty, research_gate_passed=passed,
                  verdict="eligible for forward PAPER evaluation" if passed else "no validated profitable strategy",
                  settings=asdict(cfg), default_changed=False)
    save(ROOT / "results.json", result)
    save(ROOT / "candidate_settings.json", asdict(cfg))
    export_report(result)
    output(event="COMPLETE", selected=selected, passed=passed,
           validation=validation["summary"], baseline=baseline["summary"], stress=stressed["summary"])


def export_report(result):
    selected = result["selected"]
    lines = ["# Second research round", "", f"Verdict: **{result['verdict']}**.", "",
             "Fresh market data: development August 29–September 12, validation September 12–19, 2026 (UTC). No legacy trade records used.",
             "Retrospective validation on an earlier unseen period, not a forward test. Five hypotheses fixed before downloading the sample.", "",
             "| Candidate | Development week 1 net | Development week 2 net |", "|---|---:|---:|"]
    for name, folds in result["development"].items():
        lines.append(f"| {name} | {folds[0]['summary']['net_pnl']:.2f} | {folds[1]['summary']['net_pnl']:.2f} |")
    lines += ["", f"Selected before validation: **{selected}**, maximizing the weaker development week. Each fold starts at $1,000.", "",
              "| Validation | Trades | Net USDT | Net/trade | Profit factor | Win rate |", "|---|---:|---:|---:|---:|---:|"]
    for name, key in (("Original baseline", "baseline_validation"), (selected, "validation"), ("Doubled costs, filter re-evaluated", "cost_stress")):
        s = result[key]["summary"]
        pf = f"{s['profit_factor']:.3f}" if s["profit_factor"] is not None else "N/A"
        lines.append(f"| {name} | {s['trades']} | {s['net_pnl']:.2f} | {s['expectancy']:.2f} | {pf} | {100*s['win_rate']:.1f}% |")
    u = result["uncertainty"]
    fixed = result["fixed_trade_cost_stress"]
    lines += ["", f"**Same-trade doubled-cost diagnostic:** {fixed['net_pnl']:.2f} USDT across the same {fixed['trades']} selected trades. This freezes quantity, entry decisions and exit timestamps and reprices fees/slippage only.",
              "The full doubled-cost rerun changes the volatility/cost filter, sizes and targets. Its smaller trade sample is not a like-for-like cost sensitivity; a positive result there does not validate the original strategy. The same-trade calculation was added after seeing the rerun discrepancy, solely as an accounting diagnostic; it is not used to select a strategy.",
              "", f"Selected daily mean net: {u['mean_daily_net']:.2f} USDT; day-block bootstrap 95% interval [{u['lower95']:.2f}, {u['upper95']:.2f}]. Only seven validation days; this interval is descriptive and assumes independent days.",
              "", "Funding is charged using historical rates and marks at minute boundaries; positive rate debits longs and credits shorts. Fills include assumed spread/slippage and commissions. Intrabar executable-price inversion was corrected to avoid double-counting costs. Stops win same-bar ties.",
              "", "Historical depth, partial fills, sub-minute sequencing and historical lot filters are still absent. Volatility-to-cost screening is not a forecast of returns. Configurations are research candidates; defaults were not changed.",
              "", "Raw validation trades: validation_trades.csv. Full runs and selection evidence: results.json and selection_before_validation.json.",
              "", "Funding API reference: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data"]
    (ROOT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    trades = result["validation"]["trades"]
    fields = ["id", "symbol", "side", "entry_time", "exit_time", "entry", "exit", "qty",
              "hold_minutes", "exit_reason", "gross_pnl", "fees", "funding_cost", "net_pnl", "diagnostics"]
    with (ROOT / "validation_trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for t in trades:
            writer.writerow(dict(t, diagnostics=json.dumps(t["diagnostics"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download", "run"])
    args = parser.parse_args()
    download() if args.command == "download" else run()


if __name__ == "__main__":
    main()
