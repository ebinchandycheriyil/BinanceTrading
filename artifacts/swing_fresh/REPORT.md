# Fresh 2026 evaluation and small refinements

Acceptance passed: False. Frozen candidate: breakout_tf2_lb28_atr4_h48.

January 1–March 1, 2026: first evaluate the 2025 winner unchanged, then tune 27 nearby settings. March 1–May 1: independent holdout for the frozen choice. Each stage starts at 1,000 USDT and is flat at the boundary. This is fresh historical data, not prospective live trading.

| Configuration | Period | Trades | Net USDT | Return | Hourly DD | PF |
|---|---|---:|---:|---:|---:|---:|
| 2025 baseline | Jan-Feb | 31 | 2.93 | 0.29% | 1.40% | 1.1198843976211246 |
| Refined selection | Jan-Feb | 29 | 7.81 | 0.78% | 0.94% | 1.386812924392147 |
| 2025 baseline | Mar-Apr | 27 | -22.01 | -2.20% | 2.28% | 0.27221860508240137 |
| Refined selection | Mar-Apr | 26 | -17.33 | -1.73% | 1.80% | 0.3178520366417445 |
| Refined, double fee/slip | Mar-Apr | 26 | -18.67 | -1.87% | 1.93% | 0.28042902897973165 |

## Selected monthly results

| Month | Marked equity change USDT |
|---|---:|
| 2026-01 | 16.14 |
| 2026-02 | -8.33 |
| 2026-03 | -6.61 |
| 2026-04 | -10.72 |

## Refinement

Fixed 2h breakout / 4h EMA trend. Lookback 28/30/32 bars, stop 3.5/3.75/4 ATR, maximum hold 48/54/60 hours. Equal target distance. All other risk, execution and entry/exit rules unchanged. Selection prioritizes at least 12 development trades, then the weaker marked month, then total net. Baseline was 30 bars / 3.75 ATR / 54h.

Positive development configurations: 27/27. Monthly stability, not highest total return, determines the selected candidate.

## Acceptance gates

- development_positive_months: False
- development_trades: True
- holdout_trades: True
- holdout_net: False
- holdout_pf: False
- holdout_drawdown: True
- bootstrap_lower_positive: False
- stress_positive: False

Weekly marked-equity bootstrap 95% interval: [-4.22, 0.06] USDT/week, based on 8 complete weeks. This is a small sample with limited statistical power; IID week resampling is not multiple-testing corrected.

Costs: 5bps taker per side, 4bps spread, 2bps slippage per side, historical funding. Stress doubles fees and slippage with a full rerun, so trades can change. Risk: 0.25%/trade, 0.5% combined stop risk, <=1x gross, 1.5% daily equity loss halt. Protective targets/stops and trend invalidation can exit before the maximum hold.

Accounting and 1:1 checks passed for 889 simulated trades. Funding mark fallbacks: 0.

Limitations: no historical book depth, partial fills, lot filters, liquidation, taxes or intrahour drawdown. Stop wins ambiguous intrabar ties. Funding is bucketed to hourly open. Prior 2025 optimization selected from 286 settings; this short holdout cannot prove a durable edge. No reselection or post-holdout tuning. No exchange orders or automatic deployment.

[Development grid](development_grid.csv) | [Holdout trades](holdout_trades.csv) | [Full results](results.json)
## Audit and selected holdout loss attribution

889 simulated trades reconciled; 86 tests passed. Source and data hashes match the protocol. The baseline exactly matches the previously selected 2025 configuration. The saved selection is reproducible from development results alone.

| Group | Trades | Net USDT | Fees | Funding cost |
|---|---:|---:|---:|---:|
| symbol:ETH/USDT:USDT | 12 | -4.83 | 0.73 | 0.09 |
| side:SELL | 9 | -8.91 | 0.58 | 0.10 |
| exit:TREND_INVALIDATION | 11 | -13.79 | 0.68 | 0.01 |
| symbol:BTC/USDT:USDT | 14 | -12.50 | 0.89 | -0.03 |
| exit:STOP | 4 | -9.73 | 0.30 | 0.01 |
| side:BUY | 17 | -8.42 | 1.04 | -0.04 |
| exit:TARGET | 2 | 4.50 | 0.16 | 0.00 |
| exit:TIME_LIMIT | 7 | 3.14 | 0.36 | 0.02 |
| exit:END_OF_SAMPLE | 2 | -1.45 | 0.12 | 0.02 |

The adjustment reduced the holdout loss by 4.69 USDT per 1,000 USDT account, but did not produce a profitable setup. Both March and April were negative. January was profitable and February was negative even for the selected refinement. This contradicts treating the prior 2025 positive result as a reliable edge.

Do not keep calling March-April untouched if parameters are subsequently tuned on it. It can be reused for exploratory work only; independent evaluation would require another period. No automatic promotion, real orders or persistent trading process.