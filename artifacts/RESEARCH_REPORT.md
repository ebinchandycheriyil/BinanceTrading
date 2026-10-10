# Research results

Data: 2026-09-19T02:58:00+00:00 through 2026-09-26T02:58:00+00:00.
Holdout starts 2026-09-24T02:34:00+00:00. 10,080 one-minute candles per market (BTC and ETH).

**Conclusion: do not deploy this strategy with real capital on this evidence.** All tested variants lost after modeled costs. The full strategy holdout contains only 12 trades; it is too small to establish an edge.

Each partition starts with a fresh simulated $1,000 account. Net results include spread/slippage in fill prices plus commissions.

| Variant | Development net USDT | Holdout trades | Holdout net USDT | Win rate | Profit factor |
|---|---:|---:|---:|---:|---:|
| confluence | -38.52 | 12 | -5.78 | 16.7% | 0.416 |
| ema_only | -78.04 | 39 | -35.67 | 0.0% | 0.000 |
| no_volume | -62.40 | 26 | -28.90 | 3.8% | 0.003 |
| double_costs | not run | 13 | -19.22 | 7.7% | 0.034 |

## What the losses show

Full-strategy holdout: gross P&L 1.71 USDT, commissions 7.49 USDT, net -5.78 USDT. Average holding time 7.83 minutes.

Nine of twelve exits were NO_FOLLOW_THROUGH. Four trades had positive gross P&L erased by commissions. Seven had little sampled favorable movement. These tags overlap and are observations, not causal attribution.

## Next research priorities

1. Collect several weeks of forward paper trades with the actual account fee tier and measured spreads. Keep untouched future data for evaluating changes.
2. Test whether restricting entries to more liquid, higher-volatility periods improves net expectancy. Predeclare the rule before collecting the next holdout.
3. Compare the five-minute weak-trade exit against longer time budgets on new data; do not optimize it against this holdout.
4. Add historical funding and order-book execution models before treating replay as representative of futures execution.
5. Before any exchange routing, implement partial-fill reconciliation, durable order intents, reduce-only protective orders and exchange-native recovery tests.

## Validation and limits

23 deterministic tests passed; see verification.json and tests.txt. Public Binance reads succeeded. A bounded current-price paper session completed with zero qualifying entries; it did not test real exchange order execution.

Replay uses next-bar entries and pessimistic stop-first handling when both stop and target are touched. Historical spread is assumed at four basis points; no historical depth or exchange lot constraints are simulated. Funding, liquidation and taxes are omitted. Intrabar times and favorable/adverse excursions are approximate. Stops can fill worse on gaps; a timed exit requires a running process and usable market data.

Files: study.json contains all studies and individual holdout trades; holdout_trades.csv contains the default strategy raw trade output. history.json is the downloaded source dataset.