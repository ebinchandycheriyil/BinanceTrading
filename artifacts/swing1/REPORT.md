# Iterative swing research

Selected: breakout_atr3_h24. Acceptance passed: False.

1,000 USDT per fold; 1:1 price-distance stops and targets; fees and funding separate.

## Iteration 1: family selection

| Family | 2022 net | 2023 net |
|---|---:|---:|
| breakout | -71.85 | -132.95 |
| pullback | -146.38 | -184.44 |

## Iteration 2: stop and duration

| Candidate | 2024 H1 net | 2024 H2 net |
|---|---:|---:|
| breakout_atr1.5_h24 | -96.55 | -58.06 |
| breakout_atr1.5_h72 | -90.53 | -59.46 |
| breakout_atr2_h24 | -55.62 | -61.31 |
| breakout_atr2_h72 | -47.26 | -50.49 |
| breakout_atr3_h24 | -37.53 | -4.38 |
| breakout_atr3_h72 | -40.10 | 5.37 |

## Untouched 2025 validation

{
  "trades": 338,
  "net_pnl": -75.22068488989382,
  "return_pct": -7.522068488989381,
  "drawdown_pct": 7.982396977162172,
  "profit_factor": 0.7796451019670304,
  "win_rate": 0.4378698224852071,
  "fees": 36.769343706980635,
  "funding": 1.0802667638312022,
  "average_hold_hours": 12.785502958579881
}

Doubled fee/slippage rerun net: -115.06. Weekly bootstrap interval: [-2.98, 0.12] USDT.

Perpetual buy-and-hold benchmark: -14.08%; cash: 0%. Benchmark exposure differs substantially from risk-sized strategy.

Exit counts: {"TARGET": 119, "TIME_LIMIT": 73, "STOP": 114, "TREND_INVALIDATION": 32}

Signals use completed hourly bars and complete UTC four-hour closes: EMA20/50 trend and EMA20 slope; hourly 20-bar breakout or EMA20 reclaim. ATR14 stop, floor 0.5%, reject above 6%. Trend invalidation exits at next hourly open. Stop-first intrabar ties, gap exits at available executable open. No five-minute exit.

Funding mark-price fallbacks to settlement-hour open: 4110. Settlements bucketed to hourly open, before exits/entries; precise intrahour funding order unmodeled.

Research only; no automatic promotion or exchange orders. Historical depth, lot sizes, liquidation mechanics, taxes, latency and intrahour drawdown are not modeled. Hourly drawdown can understate intrabar risk. Simultaneous symbol sizing uses deterministic symbol order. Eight candidate configurations were considered in a staged search; bootstrap is not adjusted for selection and serial dependence across weeks. This identifies the best candidate within this bounded search, not a global optimum.

Sources: [cryptocurrency momentum research](https://www.nber.org/papers/w24877), [Binance funding API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History). These rules are hypotheses rather than a replication.
## Audit and loss attribution

4500 trades across all development, validation and stress runs reconciled. All 75 tests passed, compilation and dependencies checked. Sources and dataset match frozen hashes.

| Group | Trades | Net USDT | Commission | Funding cost |
|---|---:|---:|---:|---:|
| exit:TARGET | 119 | 247.61 | 13.74 | 0.45 |
| symbol:ETH/USDT:USDT | 162 | -26.81 | 12.74 | 0.52 |
| side:BUY | 171 | -21.80 | 20.44 | 1.92 |
| exit:TIME_LIMIT | 73 | -11.45 | 6.84 | 0.22 |
| symbol:BTC/USDT:USDT | 176 | -48.41 | 24.03 | 0.56 |
| exit:STOP | 114 | -265.03 | 12.86 | 0.27 |
| side:SELL | 167 | -53.42 | 16.33 | -0.84 |
| exit:TREND_INVALIDATION | 32 | -46.35 | 3.32 | 0.14 |

Both strategy families lost in each initial development year. One later refinement had a positive half-year, but was not selected by the predefined worst-fold ranking. The selected candidate lost in both 2024 halves and in 2025. No robust profitable strategy was found; no forwarding to live execution is justified.

Raw ledger: [validation_trades.csv](validation_trades.csv). Full configurations, equity curves, uncertainty and all iteration results: [results.json](results.json).

Simulator is research-only, separate from the scalping CLI; there is no unattended swing trader running.