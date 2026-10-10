# Trading research: viability decision

**Recommendation: stop expanding the current profit-seeking parameter search and do not deploy real capital. Preserve the code as a research tool. A different, explicitly bounded data or carry-screening project remains possible, but there is no demonstrated income-producing edge here.**

## Latest expanded-pair experiment

Eight coins (BTC, ETH, BNB, XRP, ADA, LTC, LINK, BCH), 28 pair combinations; daily liquidity checks, fixed regression orientation, Engle–Granger tests, 5% Benjamini–Hochberg correction per day/window, price-series ADF diagnostics, and residual half-life limits. Only one active basket. This is still a fixed survivor universe, not a historical listing universe.

Six fixed configurations developed on July 2020–December 2021. Frozen selected model: six-day formation window, 1.75-standard-deviation entry, expected gross convergence >=0.4% of gross notional, 72-hour deadline. Equal gross basket profit/loss guard; 0.25% planned stop-plus-cost budget. None reached the preferred 30 development trades, so selection was diagnostic.

2022 and 2023 were evaluated as separate 1,000-USDT accounts with the frozen configuration. These years were previously examined for BTC/ETH directional strategies; they are new expanded-pair evaluations, not globally unseen market data.

| Evaluation | Baskets | Net USDT | Return | Hourly marked DD |
|---|---:|---:|---:|---:|
| 2022_base_w144_z1.75 | 5 | 5.25 | 0.525% | 0.259% |
| 2022_double_costs_w144_z1.75 | 5 | 4.49 | 0.449% | 0.253% |
| 2022_delayed_entry_w144_z1.75 | 5 | 5.49 | 0.549% | 0.264% |
| 2023_base_w144_z1.75 | 4 | 1.55 | 0.155% | 0.239% |
| 2023_double_costs_w144_z1.75 | 4 | 0.37 | 0.037% | 0.242% |
| 2023_delayed_entry_w144_z1.75 | 4 | 0.04 | 0.004% | 0.254% |

The delayed-entry variant shifts both entries by one hour; it is not a test of a failed or delayed second leg. Higher fees can change position sizes and exit timing. Small numerical improvements under stress do not prove execution robustness.

## Finer execution check: actual one-minute candles

The original entries, quantities and latest exit deadlines were frozen. Gross 1:1 basket guards were checked at synchronized minute opens/closes, with commissions, spread, slippage and funding retained. This is an execution diagnostic on the same baskets, not a full strategy replay: no additional entries or portfolio resizing are simulated.

| Evaluation | Baskets | Original hourly net USDT | Minute diagnostic net USDT | Earlier exits | Ambiguous baskets |
|---|---:|---:|---:|---:|---:|
| 2022 | 5 | 5.25 | 0.38 | 5 | 3 |
| 2023 | 4 | 1.55 | 1.57 | 2 | 1 |

Total estimated profit across these separate yearly accounts fell from 6.80 to 1.95 USDT. This is a sum of two diagnostics, not a compounded two-year portfolio return.

The main change was a 2022 ADA/LINK basket: the hourly simulation credited a target exit of +1.96 USDT, but minute observations reached its stop earlier, producing -2.43 USDT. Other earlier exits also reduced some apparent target overshoots.

Four baskets had possible adverse-path stop breaches within minute OHLC ranges. The two legs may hit their extremes at different times, so these are recorded as ambiguity, not invented fills. Minute endpoints still cannot establish actual synchronized fills or rule out subminute stops. The tiny remaining profit is not evidence of a deployable edge.

## Evidence limits

- Nine base-case evaluation baskets across two years are insufficient to estimate a durable edge. Both weekly bootstrap intervals cross zero.
- Daily multiple-testing correction does not remove all study-wide selection bias or guarantee future mean reversion.
- Hourly observations demonstrably missed an earlier stop in the fixed-basket diagnostic. Full details are in minute_execution.json; even minute OHLC cannot reconstruct synchronized quote paths or actual fills.
- 30016 of 31496 funding marks are estimated with the settlement-hour futures open because the API did not supply the historical mark. Funding rates and timestamps are actual; exact funding amounts retain this approximation.
- Historical order-book depth, exchange lot filters, partial fills, legging losses, liquidation and taxes are not modeled. Nominal five-USDT minimums are not a historical exchange-filter reconstruction.

## What changed after the independent audit

The earlier basket simulator bucketed funding to hourly opens. Events a few milliseconds later could be assigned to the wrong position. The new pair simulator processes actual timestamps before/after entries and exits and has dedicated boundary tests. Recalculation of the earlier alternatives evaluation changed funding costs by less than 0.09 USDT per strategy; it did not change the conclusions.

The earlier three BTC/ETH evaluation trades also had hourly adverse-price envelopes that could cross their stops. Those bounds combine nonsynchronous leg extremes: they establish execution ambiguity, not proof that actual stops occurred. The prior small profit should not be treated as validated execution evidence.

## Is this viable?

Building an automated research/trading system is technically viable. Repeatable net profitability for these strategies has not been established. Optimizing hundreds of nearby settings can find positive samples while leaving the economic edge absent.

As an income project, the present evidence does not justify more engineering or capital. For scale only, a hypothetical 10% annual net return on the simulated 1,000-USDT account would be 100 USDT per year before operating costs and taxes; this is arithmetic, not a forecast or an assumption about the user's actual capital. The observed pair returns are much smaller and statistically uncertain.

The constraints interact: low costs, adequate movement, reliable fills and sufficiently frequent opportunities all need to coexist. A 1:1 gross ratio does not create positive expectancy and yields unequal net payoffs after costs. Increasing leverage or loosening statistical filters to force trades would not resolve the missing evidence.

## Outside-the-box alternatives worth considering

1. **An opportunity scanner rather than an always-trading bot.** Calculate net fixed-expiry spot/futures basis after four commissions, spread, financing, collateral reserve and a capital-return hurdle. Show an opportunity only when the margin remains worthwhile under stress. Fixed-expiry basis has a convergence mechanism; perpetual funding is variable and cannot lock in a return. Margin and venue risks remain.
2. **Execution data research.** Record public book/trade events, estimate achievable fills and adverse selection, and test whether any signal survives those measured costs. Maker fees alone are not a strategy; unfilled orders and adverse selection must be counted. Do this only as a learning/data project unless an economically meaningful signal first emerges.
3. **A genuinely different statistical model only after the above.** Copula-based relative value has published positive and negative results across variants. It is a possible hypothesis, not a reason to assume a more complex model will solve our losses. Keep model selection bounded and preserve a separate evaluation.

## Research basis

- [Tadi and Kortchmeski: dynamic cointegration pairs](https://arxiv.org/abs/2109.10662): supports testing, with execution availability and delay assumptions, not a guarantee for this implementation.
- [Fil and Kristoufek: cryptocurrency pairs](https://ieeexplore.ieee.org/document/9200323/): reported performance depends strongly on costs and execution windows.
- [BIS: Crypto carry](https://www.bis.org/publications/working-paper-1087-crypto-carry): an economic source of returns, constrained by capital, margins and liquidation risk.
- [Albers et al.: market microstructure and execution](https://arxiv.org/abs/2108.09750): passive execution requires empirical fill analysis.
- [Copula-based crypto statistical arbitrage](https://link.springer.com/article/10.1186/s40854-024-00702-7): mixed variant outcomes; selected positive backtests do not establish our own edge.

Practical stop rule: no real deployment without positive results across separate regimes, realistic execution stress, adequate opportunity counts, and a frozen prospective paper trial. Given the completed negative/fragile tests, do not authorize another broad parameter sweep as the default next step.

Verification: 117 tests passed, compilation/dependency checks passed, 76 basket ledgers and 9 minute diagnostics reconciled including independent funding holding-interval checks. Source and input hashes matched.

[Evaluation trades](evaluation_trades.csv) | [Full results](results.json) | [Minute diagnostic](minute_execution.json) | [Verification](verification.json). No exchange orders or persistent trading process were started.