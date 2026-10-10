# Alternative strategy iterations

Previously untested July 2020–December 2021 interval. Development: Jul–Dec 2020; small refinements: Jan–Jun 2021; frozen evaluation: Jul–Dec 2021. Every run begins with 1,000 USDT.

| Family | Selected configuration | Refinement net | Evaluation trades | Evaluation net | DD | Double-cost net | Passed |
|---|---|---:|---:|---:|---:|---:|---|
| pairs | family-pairs_window-144_z-1.75 | 0.00 | 3 | 3.31 | 0.11% | 2.51 | False |
| carry | family-carry_hold-336_threshold-0.12 | 31.96 | 9 | 3.69 | 0.37% | -1.69 | False |
| momentum | family-momentum_hold-72_window-360 | -11.30 | 34 | -14.50 | 1.87% | -18.28 | False |

## Iterations

21 initial configurations: nine pair models, six funding-carry rules and six cross-sectional momentum rules. Each family winner receives nine nearby refinements. Selection uses worst marked quarter with a ten-basket activity preference. Each frozen family winner is evaluated once, plus a doubled-fee/slippage rerun. No evaluation-based reselection.

Pairs: daily rolling log-price regression ETH on BTC; augmented Engle-Granger p<0.05 with one fixed lag; beta 0.3–3 and estimated residual half-life 2–72h. Entry on 1.5–2.5 residual standard deviations, reject above four; small refinements vary window by one day and entry by 0.25. Beta-weighted opposite legs, frozen hedge until exit. Daily cointegration failure exits. This is a statistical screen, not a guarantee of convergence.

Carry: long spot and short the same base-asset quantity in BTC or ETH; choose the higher trailing-seven-day annualized realized funding rate. Threshold 5/10/15% and hold 7/14 days, then small refinements. Uses past realized funding, not future funding forecasts. Spot principal and remaining futures collateral are accounted for; exit before collateral falls below 10% of short notional. This buffer is not a full liquidation simulator. Strategy can lose when funding or basis changes.

Momentum: fixed universe BTC/ETH/BNB/XRP/ADA/LTC/LINK/BCH; require prior-day estimated turnover >=10m USDT. Long the two strongest positive-return coins and short the two weakest negative-return coins, inverse-volatility weights within equal long/short notional. Initial lookbacks 7/14/28 days, holds 24/72h. A fixed surviving universe creates survivorship/selection bias; these results are not a historical-universe replication.

All families: combined executable gross basket target and stop are equal distances in USDT; net outcomes differ after fees/funding. Size uses 0.25% equity stop-plus-friction budget and gross <=0.8x. One basket at a time. Futures 5bps/fill, spot 10bps/fill; 4bps total spread and 2bps per-side slip. Stress doubles commissions and slippage. All futures funding settled for carried legs.

Execution is simultaneous at hourly opens/closes; this ignores legging delays and can miss intrahour stops or liquidation. Realized losses may exceed planned risk. No order-book depth, partial fills, historical lot constraints, taxes or financing of idle capital. Dollar P&L treats spot principal as reserved capital, not an extra profit. Daily1.5% equity halt. No real orders.

Funding mark-price fallbacks: 13976. Missing spot hours (union): 22. Carry cannot enter/exit during those hours; last known spot closes are used only for provisional valuation while actual futures funding still settles. Deadlines may overrun until quotes resume, and drawdown during unavailable quotes is uncertain. 973 basket ledgers reconciled. Detailed stage results and equity curves are saved in results.json and ledgers.json.

Sources: [pairs research](https://arxiv.org/abs/2109.10662), [BIS crypto carry](https://www.bis.org/publications/working-paper-1087-crypto-carry), [NBER crypto factors](https://www.nber.org/papers/w25882), [Engle-Granger API](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html), [spot commission example](https://www.binance.com/en-ZA/support/faq/detail/e85d6e703b874674840122196b89780a). These are adaptations, not published-strategy replications.
## Verification and evaluation detail

973 basket trades / 3372 legs reconciled. All 96 tests passed; compilation and dependency checks passed. Selection reproduced from refinement data only; source/data hashes match the saved protocol.

| Exit group | Baskets | Net USDT | Fees | Funding cost |
|---|---:|---:|---:|---:|
| family-pairs TARGET | 2 | 3.74 | 0.66 | -0.00 |
| family-pairs RELATIONSHIP_INVALID | 1 | -0.43 | 0.22 | -0.00 |
| family-carry STOP | 1 | -2.80 | 0.51 | -0.15 |
| family-carry TIME_LIMIT | 8 | 6.49 | 4.08 | -12.21 |
| family-momentum TIME_LIMIT | 9 | 1.77 | 1.02 | -0.34 |
| family-momentum TARGET | 10 | 23.91 | 1.14 | -0.14 |
| family-momentum STOP | 14 | -38.96 | 1.60 | -0.21 |
| family-momentum END_OF_SAMPLE | 1 | -1.22 | 0.11 | -0.00 |

## Weekly uncertainty

- pairs: mean weekly equity change 0.13 USDT; bootstrap 95% [0.00, 0.38], 26 complete weeks. Acceptance: False.
- carry: mean weekly equity change 0.14 USDT; bootstrap 95% [-0.18, 0.45], 26 complete weeks. Acceptance: False.
- momentum: mean weekly equity change -0.51 USDT; bootstrap 95% [-1.32, 0.19], 26 complete weeks. Acceptance: False.

Weekly bootstrap assumes independent week blocks and is not corrected for the strategy search. Zero-trade outcomes provide no evidence. A positive historical result alone does not establish future profitability.

[All iterations](all_iterations.csv) | [Evaluation legs](evaluation_legs.csv). Basket net/funding columns repeat for each constituent leg in the leg CSV; do not sum those repeated basket columns.

No strategy was automatically enabled and no exchange orders were submitted. These are retrospective simulations, not forward paper fills.