# Fixed-timeline swing parameter sweep

286 configurations; 25 positive after modeled costs. 67408 trade accounting/ratio checks passed.

**All ranking, refinement and results use the same 2025 interval. This is exploratory optimization, not untouched validation.**

BTC/ETH perpetuals; each run starts at 1,000 USDT. Same raw hourly prices and funding as swing1. Chart entries use complete 1h/2h/4h bars; stop and target monitoring remains hourly. 4h EMA20/50 trend filter and slope remain fixed.

Base sweep: two methods x three chart periods x three lookbacks x five ATR multiples x three hold limits = 270 runs. For breakouts lookback controls the range; for pullbacks it controls EMA length. Top three base rows receive ATR +/-0.25 and holding-time +/-6h refinements. Duplicates excluded. All other controls stay fixed.

| Rank | Configuration | Trades | Net USDT | Return | DD | Win rate | PF |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | breakout_tf2_lb30_atr3.75_h54 | 138 | 26.28 | 2.63% | 1.81% | 50.7% | 1.239 |
| 2 | breakout_tf2_lb30_atr3.75_h42 | 142 | 25.17 | 2.52% | 1.94% | 52.1% | 1.237 |
| 3 | breakout_tf2_lb30_atr3.75_h72 | 134 | 21.06 | 2.11% | 1.66% | 47.8% | 1.182 |
| 4 | breakout_tf2_lb30_atr3.75_h48 | 142 | 18.97 | 1.90% | 2.06% | 50.7% | 1.165 |
| 5 | breakout_tf2_lb30_atr3.75_h66 | 137 | 17.32 | 1.73% | 1.70% | 48.9% | 1.148 |
| 6 | breakout_tf2_lb30_atr4_h42 | 133 | 15.42 | 1.54% | 2.39% | 50.4% | 1.158 |
| 7 | breakout_tf2_lb30_atr4.25_h54 | 121 | 14.01 | 1.40% | 1.86% | 49.6% | 1.159 |
| 8 | breakout_tf2_lb30_atr4_h54 | 130 | 13.21 | 1.32% | 2.16% | 48.5% | 1.130 |
| 9 | breakout_tf2_lb30_atr4_h72 | 125 | 12.92 | 1.29% | 2.12% | 44.8% | 1.125 |
| 10 | breakout_tf2_lb30_atr4.25_h72 | 117 | 12.31 | 1.23% | 1.46% | 44.4% | 1.132 |
| 11 | breakout_tf2_lb30_atr4_h48 | 133 | 10.91 | 1.09% | 2.24% | 49.6% | 1.104 |
| 12 | breakout_tf2_lb30_atr3.5_h72 | 144 | 9.93 | 0.99% | 2.41% | 47.2% | 1.075 |
| 13 | breakout_tf2_lb30_atr4.25_h42 | 125 | 9.47 | 0.95% | 2.12% | 49.6% | 1.108 |
| 14 | breakout_tf4_lb10_atr4_h48 | 88 | 7.58 | 0.76% | 1.08% | 43.2% | 1.144 |
| 15 | breakout_tf2_lb30_atr2_h72 | 220 | 7.19 | 0.72% | 3.05% | 51.4% | 1.030 |

## Best five: cost and parameter sensitivity

| Candidate | Double fee/slip net | Profitable neighbors | Median neighbor net | Weekly bootstrap 95% interval |
|---|---:|---:|---:|---|
| breakout_tf2_lb30_atr3.75_h54 | 13.42 | 17/19 | 10.913115928794852 | [-0.43, 1.45] |
| breakout_tf2_lb30_atr3.75_h42 | 10.73 | 13/16 | 7.690197244966752 | [-0.40, 1.36] |
| breakout_tf2_lb30_atr3.75_h72 | 10.82 | 14/16 | 10.4205533502621 | [-0.58, 1.42] |
| breakout_tf2_lb30_atr3.75_h48 | 5.80 | 17/21 | 9.927990771729347 | [-0.55, 1.27] |
| breakout_tf2_lb30_atr3.75_h66 | 5.01 | 17/19 | 10.913115928794852 | [-0.61, 1.29] |

## Best result by quarter

| Quarter | Marked equity change USDT |
|---|---:|
| 2025-Q1 | 12.41 |
| 2025-Q2 | 13.88 |
| 2025-Q3 | -5.18 |
| 2025-Q4 | 5.17 |

Quarter/month attribution uses continuous marked equity, including open positions. No account reset between quarters; each full-year configuration is a fresh account. Neighbor comparisons exclude the winner and preserve family/timeframe/lookback.

Costs: 5 bps taker per fill, 4 bps total spread, 2 bps slippage per side, historical funding. Stress reruns double fees and slippage and may change trades. Net 1:1 is not claimed: targets and stops are equal executable price distances before commission/funding.

Limitations: many same-data comparisons invite overfitting; best net is subject to selection bias. Bootstrap is descriptive and not corrected for multiple testing. Small parameter changes are not independent evidence. Hourly intrabar order is unknown (stop wins ties); historical depth, partial fills, lot filters, taxes, liquidation and intrahour drawdown are unmodeled. Prior report documents any funding-price fallback. No exchange orders or strategy promotion.

[All runs CSV](all_runs.csv) | [Best trade ledger](best_trades.csv) | [Full results](results.json)
## Detailed audit

82 tests passed; compilation and dependencies passed. Original swing1 baseline reproduced exactly on the same prices, funding and rules. All month/quarter changes reconcile to each run's net. Source and dataset hashes match the saved protocol.

67408 simulated trades checked across parameter and stress runs. These are repeated simulations of the same period, not independent observations.

Best fixed-trade doubled-fee/slippage sensitivity: 11.70 USDT. This holds sizes and timestamps constant; it is an accounting sensitivity rather than execution replay.

| Best-run breakdown | Trades | Net USDT | Commission | Funding |
|---|---:|---:|---:|---:|
| symbol:ETH/USDT:USDT | 48 | 21.53 | 2.61 | 0.42 |
| side:BUY | 79 | -4.76 | 6.33 | 1.27 |
| exit:TIME_LIMIT | 27 | 1.96 | 1.90 | 0.22 |
| symbol:BTC/USDT:USDT | 90 | 4.75 | 7.81 | 0.27 |
| exit:TARGET | 54 | 125.72 | 4.14 | 0.13 |
| exit:STOP | 20 | -49.75 | 1.68 | 0.11 |
| side:SELL | 59 | 31.04 | 4.08 | -0.58 |
| exit:TREND_INVALIDATION | 37 | -51.65 | 2.69 | 0.24 |

## Monthly equity changes of best candidate

| Month | Change USDT |
|---|---:|
| 2025-01 | 5.72 |
| 2025-02 | -0.38 |
| 2025-03 | 7.07 |
| 2025-04 | 9.38 |
| 2025-05 | 11.33 |
| 2025-06 | -6.83 |
| 2025-07 | -7.68 |
| 2025-08 | 2.08 |
| 2025-09 | 0.42 |
| 2025-10 | 5.16 |
| 2025-11 | 8.37 |
| 2025-12 | -8.36 |

## Interpretation

The local refinement improved the best base-grid result from +12.92 USDT to +26.28 USDT on 1,000 USDT. The improvement was selected using the same 2025 data, so it is an in-sample gain. Twenty-five of 286 configurations were positive; the other 261 were not.

The winning region is more credible than a single isolated peak because 17 of 19 nearby settings also made money, and the best candidate remained positive under both cost sensitivity approaches. However, the weekly bootstrap confidence interval crosses zero, Q3 lost money, and selection across many configurations biases the best result upward.

Next useful step: freeze this complete candidate, then test on an unexamined period or collect forward paper results. Further tuning on 2025 can explore sensitivity but cannot establish independent profitability. No real trading or automatic deployment has been enabled.