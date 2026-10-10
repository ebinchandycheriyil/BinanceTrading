"""Research-backed chart hypotheses with explicit brokerage/cost accounting.

Run: python -m scalp.fee_study download ; python -m scalp.fee_study run
"""
import argparse
import csv
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from .chart_setups import NAMES, all_chart_signals
from .costs import cost_scenarios
from .experiments import DAY, choose, day_bootstrap, fixed_trade_cost_stress, stamp
from .market import BinanceData
from .research import replay
from .settings import Settings
from .signals import Candle

ROOT = Path("artifacts/round3")


def output(**event):
    text = json.dumps(event, allow_nan=False)
    print(text, flush=True)
    with (ROOT / "progress.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def candidates():
    return {name: replace(Settings(), strategy=name, min_hold_minutes=15,
                          min_stop_fraction=.001, min_target_cost_ratio=2,
                          min_net_reward_risk=1, max_daily_entries=6,
                          daily_fee_budget_fraction=.003) for name in NAMES}


def protocol():
    return dict(version=3, start=stamp("2026-07-31"), split=stamp("2026-08-07"),
                validation=stamp("2026-08-14"), end=stamp("2026-08-28"),
                candidates={name: asdict(cfg) for name, cfg in candidates().items()},
                fees="User approved standard assumptions: maker 2 bps, taker 5 bps per fill; no discounts",
                selection="Maximum worst-development-fold net P&L; name tie break. All three validation rows shown, never used to reselect.",
                gate="Selected candidate: each dev fold >=10 trades and net>0; validation >=30 trades, net>0, PF>=1.15, daily bootstrap lower95>0, same-trade doubled-cost net>0",
                fill_model="Market taker/taker, 4 bps spread, 2 bps per-side slippage, actual funding; stop-first intrabar ties. No maker fills simulated.",
                evidence="Retrospective independent market interval, not a forward trial or a replication of published profitability")


def download():
    ROOT.mkdir(parents=True, exist_ok=True)
    plan = protocol()
    save("protocol.json", plan)
    start, end = plan["start"] - 400 * 60000, plan["end"]
    market = BinanceData(Settings().symbols)
    result = dict(source="Binance USD-M public CCXT", start_ms=start, end_ms=end, data={}, funding={})
    for symbol in Settings().symbols:
        rows = market.history(symbol, start, end)
        if [c.ts for c in rows] != list(range(start, end, 60000)):
            raise ValueError("Incomplete historical interval")
        funding = market.funding_history(symbol, start, end)
        if not funding:
            raise ValueError("Funding data unavailable")
        result["data"][symbol] = [asdict(c) for c in rows]
        result["funding"][symbol] = funding
        output(event="DOWNLOAD", symbol=symbol, bars=len(rows), funding_events=len(funding))
    save("market_data.json", result)


def cache(data, cfg, start, end):
    caches = {name: {} for name in NAMES}
    for symbol in cfg.symbols:
        rows = data[symbol]
        for i, bar in enumerate(rows):
            if start <= bar.ts < end and i:
                signals = all_chart_signals(rows[max(0, i - 400):i], cfg)
                for name in NAMES:
                    caches[name][(symbol, bar.ts)] = signals[name]
                if i % 10000 == 0:
                    output(event="CHARTS", symbol=symbol, bars=i)
    return caches


def run():
    plan = json.loads((ROOT / "protocol.json").read_text())
    if plan != json.loads(json.dumps(protocol())):
        raise ValueError("Declared protocol differs from code")
    raw_bytes = (ROOT / "market_data.json").read_bytes()
    raw = json.loads(raw_bytes)
    data = {s: [Candle(**r) for r in rows] for s, rows in raw["data"].items()}
    start, split, valid, end = (plan[k] for k in ("start", "split", "validation", "end"))
    expected = list(range(start - 400 * 60000, end, 60000))
    if any([c.ts for c in data[s]] != expected for s in Settings().symbols):
        raise ValueError("Data chronology differs from declared interval")
    configs = candidates()
    dev_data = {s: [c for c in rows if c.ts < valid] for s, rows in data.items()}
    prepared = cache(dev_data, next(iter(configs.values())), start, valid)
    development = {}
    for name, cfg in configs.items():
        development[name] = []
        for a, b in ((start, split), (split, valid)):
            result = replay(dev_data, cfg, a, b, signals=prepared[name], funding=raw["funding"])
            development[name].append(result)
            output(event="DEVELOPMENT", method=name, start=a, **result["summary"])
    selected = choose(development)
    frozen = dict(selected=selected, development=development, protocol=plan,
                  dataset_sha256=hashlib.sha256(raw_bytes).hexdigest())
    save("selection_before_validation.json", frozen)
    output(event="SELECTION_FROZEN", method=selected)
    prepared = cache(data, next(iter(configs.values())), valid, end)
    validation = {}
    for name, cfg in configs.items():
        result = replay(data, cfg, valid, end, signals=prepared[name], funding=raw["funding"])
        validation[name] = result
        output(event="VALIDATION", method=name, **result["summary"])
    trades = validation[selected]["trades"]
    uncertainty = day_bootstrap(trades, valid, end)
    sensitivity = fixed_trade_cost_stress(trades, configs[selected])
    summary = validation[selected]["summary"]
    passed = (all(f["summary"]["trades"] >= 10 and f["summary"]["net_pnl"] > 0 for f in development[selected])
              and summary["trades"] >= 30 and summary["net_pnl"] > 0
              and (summary["profit_factor"] is None or summary["profit_factor"] >= 1.15)
              and uncertainty["lower95"] > 0 and sensitivity["net_pnl"] > 0)
    result = dict(frozen, validation=validation, fee_scenarios={name: cost_scenarios(r["trades"]) for name, r in validation.items()},
                  uncertainty=uncertainty, same_trade_stress=sensitivity, passed=passed, default_changed=False,
                  verdict="eligible for forward paper testing" if passed else "no validated profitable setup")
    save("results.json", result)
    save("candidate_settings.json", asdict(configs[selected]))
    report(result)
    output(event="COMPLETE", selected=selected, passed=passed)


def report(result):
    lines = ["# Fee-aware chart studies", "", f"Verdict: **{result['verdict']}**.", "",
             "BTC/ETH USDT perpetuals; July 31–August 14, 2026 development; August 14–28 validation (UTC). Each fold starts with $1,000. Fresh historical interval; no legacy trade records.", "",
             "Research and exact adaptations: [SOURCES.md](SOURCES.md). None of the source methods is assumed proven for this market/horizon.", "",
             "## Development", "", "| Method | Week 1 net | Week 2 net | Trades |", "|---|---:|---:|---:|"]
    for name, folds in result["development"].items():
        lines.append(f"| {name} | {folds[0]['summary']['net_pnl']:.2f} | {folds[1]['summary']['net_pnl']:.2f} | {sum(f['summary']['trades'] for f in folds)} |")
    lines += ["", f"Selected using development only: **{result['selected']}**. No reselection using the validation table.", "",
              "## Fourteen-day validation", "",
              "| Method | Trades | Fill-price gross | Commission | Funding cost | Net | Net/trade |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, r in result["validation"].items():
        s = r["summary"]
        lines.append(f"| {name} | {s['trades']} | {s['gross_pnl']:.2f} | {s['fees']:.2f} | {s['funding_cost']:.4f} | {s['net_pnl']:.2f} | {s['expectancy']:.2f} |")
    selected_stats = result["validation"][result["selected"]]["summary"]
    lines += ["", "## Selected setup cost reconciliation", "",
              f"Before spread/slippage and commissions: {selected_stats['gross_pnl'] + selected_stats['spread_slippage_cost']:.2f} USDT; modeled spread/slippage: {selected_stats['spread_slippage_cost']:.2f}; exchange commissions: {selected_stats['fees']:.2f}; funding cost: {selected_stats['funding_cost']:.4f}; final net: {selected_stats['net_pnl']:.2f}.",
              f"Executed entry-plus-exit notional: {selected_stats['executed_notional']:.2f} USDT. Fees are charged per fill, not per trade count or deposited margin.", "",
              "## Rejected setups", ""]
    for name, r in result["validation"].items():
        relevant = {k: v for k, v in r["skipped_signals"].items() if k != "waiting for a complete 5m chart setup"}
        lines.append(f"- {name}: {json.dumps(relevant)}")
    lines += ["", "Gross above is after simulated spread/slippage, before commissions and funding. Each raw trade includes both fill fees, notional, reference prices and execution drag.", "",
              "## Same-trade fee sensitivity", "",
              "| Method | Taker/taker net | Maker/taker hypothetical net | Maker/maker hypothetical net |", "|---|---:|---:|---:|"]
    for name, scenarios in result["fee_scenarios"].items():
        values = [scenarios[k]["net"] for k in ("taker_taker", "maker_taker_hypothetical", "maker_maker_hypothetical")]
        lines.append(f"| {name} | {values[0]:.2f} | {values[1]:.2f} | {values[2]:.2f} |")
    u, stress = result["uncertainty"], result["same_trade_stress"]
    lines += ["", "Maker columns change commission rates only on the same taker fills. They are NOT executable maker strategies: queues, missed fills and adverse selection are unmodeled. They cannot be used to claim lower-cost profitability.", "",
              f"Selected same-trade doubled-fee/slippage net: {stress['net_pnl']:.2f} USDT. Mean daily net bootstrap 95% interval: [{u['lower95']:.2f}, {u['upper95']:.2f}]. Fourteen days and IID day resampling are limited evidence.", "",
              "Controls: six entries/day across the portfolio, 0.3% planned daily commission budget including reserved exits, structural objectives at least twice round-trip costs, estimated net R:R >=1. Protective exits always override fee/entry caps. Gaps can exceed planned budgets.", "",
              "No parameter search was performed. Five-minute studies use only completed bars; fills use next-minute open and pessimistic stop-first ties. Funding is settled at minute boundaries. Historical depth, lot constraints, partial fills, exact intrabar timing and taxes remain outside the model. Defaults are unchanged."]
    (ROOT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    with (ROOT / "validation_trades.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["method", "id", "symbol", "side", "entry_time", "exit_time", "entry", "exit", "qty", "entry_notional", "exit_notional", "entry_fee", "exit_fee", "spread_slippage_cost", "funding_cost", "gross_pnl", "net_pnl", "hold_minutes", "exit_reason", "planned_net_reward_risk"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for method, r in result["validation"].items():
            for trade in r["trades"]:
                writer.writerow(dict(trade, method=method))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("download", "run"))
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    download() if args.command == "download" else run()


if __name__ == "__main__":
    main()
