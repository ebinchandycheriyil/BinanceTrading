# Second research round

Verdict: **no validated profitable strategy**.

Fresh market data: development August 29–September 12, validation September 12–19, 2026 (UTC). No legacy trade records used.
Retrospective validation on an earlier unseen period, not a forward test. Five hypotheses fixed before downloading the sample.

| Candidate | Development week 1 net | Development week 2 net |
|---|---:|---:|
| baseline | -55.37 | -41.39 |
| patience15 | -52.25 | -39.95 |
| patience30 | -56.40 | -39.52 |
| cost_gate15 | -23.53 | -7.50 |
| breakout15 | -13.74 | -4.91 |

Selected before validation: **breakout15**, maximizing the weaker development week. Each fold starts at $1,000.

| Validation | Trades | Net USDT | Net/trade | Profit factor | Win rate |
|---|---:|---:|---:|---:|---:|
| Original baseline | 51 | -47.02 | -0.92 | 0.001 | 2.0% |
| breakout15 | 18 | -19.62 | -1.09 | 0.323 | 16.7% |
| Doubled costs, filter re-evaluated | 5 | 2.31 | 0.46 | 1.381 | 40.0% |

**Same-trade doubled-cost diagnostic:** -34.74 USDT across the same 18 selected trades. This freezes quantity, entry decisions and exit timestamps and reprices fees/slippage only.
The full doubled-cost rerun changes the volatility/cost filter, sizes and targets. Its smaller trade sample is not a like-for-like cost sensitivity; a positive result there does not validate the original strategy. The same-trade calculation was added after seeing the rerun discrepancy, solely as an accounting diagnostic; it is not used to select a strategy.

Selected daily mean net: -2.80 USDT; day-block bootstrap 95% interval [-5.09, -0.41]. Only seven validation days; this interval is descriptive and assumes independent days.

Funding is charged using historical rates and marks at minute boundaries; positive rate debits longs and credits shorts. Fills include assumed spread/slippage and commissions. Intrabar executable-price inversion was corrected to avoid double-counting costs. Stops win same-bar ties.

Historical depth, partial fills, sub-minute sequencing and historical lot filters are still absent. Volatility-to-cost screening is not a forecast of returns. Configurations are research candidates; defaults were not changed.

Raw validation trades: validation_trades.csv. Full runs and selection evidence: results.json and selection_before_validation.json.

Funding API reference: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data

All 35 functional tests passed. Retired execution/GUI modules were removed. No legacy trade records or exchange order endpoints were used.

A separate 60-second public-book observation recorded 12 samples per symbol: median BTC spread 0.01192 bps; ETH 0.03723 bps. This short current snapshot does not establish historical spreads or executable slippage, and was not used to lower replay costs. Raw observations are in book_observation.json.
