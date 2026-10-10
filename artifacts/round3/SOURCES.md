# Sources and testable hypotheses

Reviewed September 26, 2026. None of these sources proves that a five-to-thirty-minute BTC/ETH futures strategy will be profitable today. The implemented rules are explicitly our adaptations, not reproductions of a published profitable portfolio.

## Exchange commissions

[Binance Futures fee calculation](https://www.binance.com/en/support/faq/detail/360033544231) describes per-fill fees on executed contract value. Its USD-M example uses 0.02% maker and 0.05% taker. Immediate execution is taker; merely calling an order a limit order does not guarantee maker status. The account-specific rate table did not return an authenticated fee tier. The user approved 2/5 bps assumptions without discounts.

At unchanged prices, a $1,000 notional round trip is $1 commission with taker/taker execution, $0.70 with maker/taker, or $0.40 with maker/maker. The latter two are fee-only hypothetical comparisons; this system does not assume passive orders are filled. Spread, slippage and funding are separate. Both trading sides count toward turnover and commission.

## Evidence on technical trading

[Deprez and Frommel, Are simple technical trading rules profitable in bitcoin markets?](https://backoffice.biblio.ugent.be/download/01HY3C3S169G1N6QNYR55NZMFB/01HY60XZGZYHNQ6188MSVJT0SG) evaluates large families of technical rules, transaction costs, multiple testing and out-of-sample portfolios. Its abstract reports favorable historical results for selected portfolios. That is evidence to test rule families carefully, not proof for our symbols, execution model or holding interval. We inspected the abstract; full-text retrieval later timed out, so no exact strategy replication is claimed.

[Bysik and Slepaczuk, 2026 preprint](https://arxiv.org/abs/2606.00060) studies hourly BTC forecasts with walk-forward evaluation. The abstract reports that costs destroy naive sign-based trading and that execution filtering helps selected configurations. It is an hourly machine-learning study, not a validated scalping prescription. We adopt explicit cost hurdles and turnover controls, not its reported returns or models.

## Chart methods

[John Bollinger's official rules](https://www.bollingerbands.com/bollinger-band-rules) warn that touching a band alone is not a trade signal, that trends can follow the outer bands, and that related indicators can provide redundant confirmation. We test a 20-bar/two-standard-deviation re-entry pattern on five-minute candles only in a low-ADX, relatively flat regime. A prior outside close, confirmed re-entry and directional candle are required; the mid-band is the objective. These extra conditions are ours, not a claimed canonical profitable Bollinger system.

[CME support and resistance](https://www.cmegroup.com/education/courses/technical-analysis/support-and-resistance) explains moving-average support, prior highs/lows, and the possible reversal of a broken level's role. We translate these concepts into two separate hypotheses: a breakout followed by a retest, and a trend pullback that reclaims EMA20. Stops sit beyond chart invalidation and targets respect prior levels or a bounded measured move. CME education explains concepts; it does not provide empirical profitability for our implementations.

## Declared rules before data/results

- Universe: BTC/USDT and ETH/USDT linear perpetuals. Only completed five-minute patterns; entry at the following one-minute open.
- Band re-entry: ADX<20, EMA20/50 separation no greater than ATR, relative volume 0.8–2.0; stop beyond the two-bar extreme plus 0.25 ATR, objective at the middle band.
- Breakout retest: ADX>=25, EMA20/50 trend alignment; prior candle breaks a twenty-bar level with relative volume>=1.5; next candle retests and closes on the breakout side; stop beyond the retest plus 0.5 ATR; objective limited by 2.5R or prior range width.
- Trend pullback: ADX>=25, trend alignment, volume>=1.2, close reclaims EMA20; stop beyond the three-bar swing plus 0.25 ATR, objective capped at prior resistance/support and 2.5R.
- All: skip if the structural stop is outside 0.1–0.8%, target distance is below twice estimated round-trip costs, or estimated net reward/risk is below 1.0. No artificial target extension to make a setup pass.
- Fifteen-minute weak-trade exit and thirty-minute maximum; protective exits may occur earlier. At most six entries per UTC day for the whole portfolio; budget planned commissions at 0.3% of day-start equity. Reserve exits for open positions; never suppress a protective exit to meet a fee budget.
- Fees: taker/taker 5 bps per fill, slip 2 bps per side, spread 4 bps, historical funding. Costs are assumptions, not measured historical execution.
- Development: July 31–August 14, 2026, split into two seven-day folds. Validation: August 14–28, fourteen days, previously unused. Historical validation precedes earlier studies; it is not prospective forward evidence.
- Pick a candidate using development only. Show all three predeclared validation results for transparency, but do not change selection after inspection. Positive validation alone does not satisfy the research gate.
