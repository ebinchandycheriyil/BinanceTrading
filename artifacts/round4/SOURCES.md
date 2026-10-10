# Literature and fixed hypotheses

Researched September 26, 2026. These are original testable adaptations, not reproductions of published returns. Thresholds were frozen in code and protocol before downloading this interval. No prior account or testnet trade records are used.

| Primary source | Finding and limitation | Experiment implication |
|---|---|---|
| [Bianchi, Babiak and Dickerson: Trading volume and liquidity provision](https://www.cerge-ei.cz/pdf/wp/Wp730.pdf) | Reversal returns vary with market activity. Cross-sectional evidence does not establish a five-minute BTC/ETH edge. | Test a low-volume displacement followed by reversal confirmation. |
| [Wen et al.: Intraday return predictability](https://www.sciencedirect.com/science/article/pii/S1062940822000833) | Both continuation and reversal appear, conditional on liquidity and jumps. Study period and implementation differ from this test. | Test continuation and reversal separately instead of assuming every shock reverses. |
| [Cont, Kukanov and Stoikov: Price impact of order book events](https://arxiv.org/abs/1011.6402) | Best-quote imbalance includes cancellations and limit orders; their evidence concerns US stocks. Trade volume alone is a noisier measure. | Use taker volume only as confirmation. Do not label it full order-book imbalance or infer liquidity absorption directly. |
| [Tadi and Kortchmeski: Dynamic cointegration pairs trading](https://arxiv.org/abs/2109.10662) | Uses cointegration tests, mean-reversion speed, and executable quote sizes. | Promising distinct research direction, not implemented here: requires joint-leg lifecycle and execution data. Correlation alone would not reproduce their method. |
| [Binance kline API](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/market-data/rest-api/Kline-Candlestick-Data) | Raw kline field 9 is taker-buy base volume; field 5 is total base volume. | Signed trade-volume proxy = 2 * buy / total - 1. No inference about resting liquidity. |

## Exact rules

All signals use completed one-minute bars; execution is next-minute open. Baseline volume is the preceding 60 bars excluding the setup and confirmation bars. ATR-like range is the mean of 14 preceding true ranges. Directional rules are mirrored for shorts.

- Absorption reversal: setup makes a new 20-bar low then closes back above the old low, volume >=1.5 times baseline and taker imbalance <-0.2. Next bar must close higher with positive imbalance. Stop below setup low by 0.25 ATR; target prior volume-weighted close. This is a failed-breakout proxy, not proof of absorption.
- Quiet shock reversal: prior three-minute log move exceeds three times baseline one-minute standard deviation times sqrt(3), while those bars average <0.8 times baseline volume. Next close reverses with imbalance >0.1 for longs or <-0.1 for shorts. Stop outside four-bar extreme plus 0.25 ATR; target pre-shock close.
- Flow continuation: setup closes beyond preceding 20-bar range on >1.5 times volume. Confirmation continues with >baseline volume; both bars' imbalance exceeds 0.2 in the trade direction. Stop outside two-bar extreme plus 0.25 ATR; gross objective twice structural risk.

All candidates share 0.1%–0.8% stop-distance limits, target >=2 times modeled round-trip costs and estimated net reward/risk >=1. Six portfolio entries/day and 0.3% daily reserved commission budget. Risk 0.25%/trade, gross exposure <=equity, two positions maximum. Weak exits start after 15 minutes; maximum hold 30 minutes; protective stops and targets can exit sooner.

Costs: 5 bps taker each fill, 4 bps total quoted spread, 2 bps slippage each side, plus historical funding when carried across settlement. Thus flat-price round-trip friction is approximately 18 bps of notional. Approved 2 bps maker fee is only a hypothetical sensitivity, not an executable fill assumption.

Two weekly development folds May 31–June 14, 2026; untouched validation June 14–28. Selection maximizes worst development fold; eligibility additionally requires minimum participation and positive outcomes. All fixed validation rows are disclosed without reselection. Only one declared configuration per hypothesis; no post-validation tuning.

Limitations: retrospective historical validation, only two instruments and four weeks; no book queue, partial fills, actual historical spreads, taxes or exchange lot filters. Daily bootstrap assumes independent days and is not corrected for this project's repeated strategy research. A positive short sample is insufficient for a profitability claim. Fresh forward data would be needed before promotion.
