# Fixed-expiry spot/futures carry: source notes and model limits

Researched 2026-09-26. Public Binance data and official Binance documentation only. These notes describe a paper-research design; proposed thresholds below are modeling choices, not exchange requirements or validated profitable settings.

## Instrument scope and verified availability

Use BUY spot plus SELL a **linear USDT-settled dated future of the same base asset**, with matched net base quantity. Exclude perpetuals, inverse COIN-M contracts, synthetic assets, and unmatched quote/settlement currencies.

The public `GET https://fapi.binance.com/fapi/v1/exchangeInfo` response at 2026-09-26 12:26:33 UTC listed these four `TRADING` instruments with both quote and margin asset USDT:

| Symbol | Type | Delivery timestamp (UTC) |
|---|---|---|
| BTCUSDT_261225 | CURRENT_QUARTER | 2026-12-25 08:00 |
| ETHUSDT_261225 | CURRENT_QUARTER | 2026-12-25 08:00 |
| BTCUSDT_270326 | NEXT_QUARTER | 2027-03-26 08:00 |
| ETHUSDT_270326 | NEXT_QUARTER | 2027-03-26 08:00 |

This is a dated observation, not an enduring contract list. The paper adapter refreshes discovery every five minutes; eventual live routing would need fresh status/filter checks at order submission. The same response contained `TRADIFI_PERPETUAL` as well as `PERPETUAL`; therefore `contractType != PERPETUAL` is an unsafe eligibility test. Use an explicit dated-contract type allowlist and a plausible, finite delivery horizon. The far-future delivery timestamp used by perpetuals is not a real expiry.

The [USD-M exchange-information documentation](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information) provides contract type, delivery date, status, assets and trading filters. Apply the actual tick, lot, market-lot and notional filters; precision fields are not substitutes for step sizes. The [official quarterly listing announcement](https://www.binance.com/en/support/announcement/detail/cfadaba578ab465b906a9e063b12f26f) explicitly distinguishes USDT settlement for USD-M BTC/ETH quarterlies from coin settlement for COIN-M contracts.

## Funding, expiry and settlement

[USD-M contract specifications](https://www.binance.com/en-AE/support/faq/detail/360033161972) describe linear contracts with perpetual and quarterly expirations and identify funding as a perpetual-contract feature. [Binance's delivery-futures introduction](https://www.binance.com/en/blog/all/421499824684900664) states that delivery products do not charge periodic funding. Its historic promotional trading fees are **not** current fee assumptions.

The [quarterly settlement FAQ](https://www.binance.com/en/support/faq/detail/a3401595e1734084959c61491bc0dbe3) describes expiry at 08:00 UTC on the last Friday of the quarter, a settlement price averaged from index observations over the final 30 minutes, a taker-equivalent settlement fee, and close-only trading during the final ten minutes. Exceptional settlement postponements are possible. Its worked inverse-contract formula must not be copied into a linear USDT P&L calculation.

Our proposed implementation exits **before** expiry. It should charge ordinary exit execution costs, not an additional settlement fee. It must not silently auto-settle, invent an executable spot price equal to the futures settlement index, or assume all basis has disappeared at the planned exit. If the deadline is missed or expiry occurs, keep an explicit unresolved exposure/settlement state requiring reconciliation. A dated expiry fixes contractual settlement mechanics; it does not guarantee a profitable pre-expiry unwind.

## Fees and matched quantities

The [futures fee explanation](https://www.binance.com/en/support/faq/detail/360033544231) uses executed notional times commission rate. Model futures taker fees at the user-approved **5 bps per fill**, and spot fees at **10 bps per fill** as a conservative research assumption. These are not authenticated account rates. Special fees, tax commissions, discounts and promotions require separate verification before any live use.

The [spot commission FAQ](https://developers.binance.com/docs/binance-spot-api-docs/faqs/commission_faq) explains standard, special and tax commissions, received-asset charging, and optional BNB payment. The [spot fee explanation](https://www.binance.com/en-NZ/support/faq/detail/e85d6e703b874674840122196b89780a) illustrates base-asset deductions on BUY and quote-asset deductions on SELL. If a gross buy is `Q` and its base commission is `b`, only `Q-b` is available to hedge. Selling futures for the gross spot order amount creates an unintended short position.

For planning, a desired net hedge `q` requires approximately `Q=q/(1-r_spot_buy)` gross spot quantity, rounded according to spot filters. Recompute the futures hedge from actual simulated net receipts and its own step size; explicitly record residual dust. [Spot filters](https://developers.binance.com/docs/binance-spot-api-docs/filters) also apply independently to the later spot sale. Do not assume that both instruments share a lot size.

## Economics and accounting

Let `S0` be the spot entry ask, `F0` the futures entry bid, and `q` the matched net quantity. The headline basis value is `q*(F0-S0)`. It is a **convergence scenario**, not earned profit. A conservative opportunity estimate is:

```
projected_economic_surplus = q*(F0-S0)
    - spot_buy_fee_drag - futures_entry_fee
    - estimated_spot_exit_fee - estimated_futures_exit_fee
    - entry_and_exit_slippage
    - residual_exit_basis_and_spread_reserve
    - capital_charge

capital_charge = committed_capital * annual_charge_rate * planned_days/365
committed_capital = gross_spot_cash + 100%_futures_notional_collateral
                   + fee_and_operating_cash_reserves
```

Bid/ask entry quotes already include entry spreads: do not subtract them twice. If fills already include slippage, do not subtract the same slippage again. A base-deducted entry fee represented by extra gross spot purchase cash must likewise not be charged a second time as cash commission. Exit fees use exit executed notionals, not entry notionals.

An exact cash-ledger check, before capital charge, is `net_spot_sale_proceeds - gross_spot_purchase_cash + q*(futures_entry_fill-futures_exit_fill) - futures_entry_fee - futures_exit_fee`, plus separately recorded residual inventory value. Futures collateral release is return of principal, not profit; shorting a derivative does not create spendable spot-sale proceeds. Display actual simulated trading P&L separately from the imputed opportunity cost of capital.

At the assumed fees, four commissions total approximately **30 bps of one matched-leg notional**, or 15 bps of two-leg gross notional, before execution costs. Return on committed capital is roughly half a basis return quoted on one leg when spot plus full futures collateral are reserved. Annualized projected returns are simple extrapolations, not repeatable realized yields.

The relevant exit basis is `futures_exit_ask - spot_exit_bid`; it can widen. At settlement the averaged index can also differ from the actual spot disposal price. Show sensitivity to residual basis and delayed exits. One hundred percent initial futures collateral reduces leverage but does not remove liquidation risk: a sufficiently large price rise exhausts a short's collateral, while offsetting spot gains in another wallet do not replenish it automatically.

## Design suggestions considered during implementation

These are research suggestions, not a description of every implemented rule or empirically optimized values. The final runtime defaults and implemented controls are documented in [REPORT.md](REPORT.md) and `carry_settings.json`. In particular, the implementation uses a one-hour pre-expiry deadline, 4% capital charge, 10% additional annualized-surplus hurdle, eight-second quote-age limit, and three-second capture-skew limit. Repeated quote confirmation remains a possible future improvement; current paper entries use a single paired observation.

- BTC and ETH only; at most one basket per base asset; no borrowing, BNB discounts, automatic rollover or automatic settlement.
- Repeated consistent quotes before entry; at most 5-second quote age, at most 2-second cross-market capture skew, and quantity no larger than 10% of displayed executable depth. A reception timestamp cannot prove exchange freshness; log that distinction.
- Reserve full spot cost, 100% futures notional collateral, commissions and an additional cash buffer. Reject entries exceeding total available capital or hedge/dust tolerances.
- Plan to unwind 24 hours before expiry; accept new baskets only with at least 7 days remaining. Entry deadline and contract maturity must be persisted, not recomputed to extend a trade after restart.
- Research scenario: 2 bps slippage per fill, at least 10 bps residual exit-basis/spread reserve on matched notional, and a 5% annual capital charge. Neither reserve is a bound on actual costs; rerun with doubled reserves and disclose unknown future exit prices.
- Require at least 2 USDT projected surplus **after** capital charge and at least 2% annualized additional surplus on fully committed capital. These opportunity filters intentionally allow no trades; the absolute threshold should eventually reflect actual operating costs. Do not lower filters simply because no contract qualifies.
- Monitor liquidation-side collateral and executable basket P&L independently. Suspend entries on stale/missing data, contract-status changes, invalid/crossed books, insufficient depth, or unresolved one-leg fills. A small aggregate delta does not establish adequate futures collateral.
- Suggested early-risk trigger: request a paper unwind while futures marked equity falls below 25% of current futures notional, or when executable basket loss reaches its configured cash risk budget. This is a conservative model trigger, **not** Binance's liquidation formula; exchange maintenance tiers would need a separate model.
- On a time/risk exit with unavailable quotes, record a pending exit and try again when executable data returns. Never manufacture a fill or report the position closed. A one-leg fill or expiry with unresolved positions is a fault requiring reconciliation.

Public quotes alone cannot establish live account eligibility, actual fees, partial-fill behavior, latency, liquidation protection, or future realized profit. The useful first output is an auditable paper opportunity/rejection ledger, followed by actual forward marks and exits, not an assurance that carry is profitable.
