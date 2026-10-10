# Trade-flow hypothesis study

Verdict: **no validated profitable setup**.

BTC/ETH USDT perpetuals; May 31–June 14, 2026 development; June 14–28 validation (UTC). Each fold starts with $1,000. Fresh historical interval; no legacy trade records.

Research and exact adaptations: [SOURCES.md](SOURCES.md). None of the source methods is assumed proven for this market/horizon.

## Development

| Method | Week 1 net | Week 2 net | Trades |
|---|---:|---:|---:|
| absorption_reversal | -9.92 | -6.12 | 17 |
| quiet_shock_reversal | 0.00 | 0.00 | 0 |
| flow_continuation | -16.26 | -8.79 | 47 |

Selected using development only: **quiet_shock_reversal**. No reselection using the validation table.

## Fourteen-day validation

| Method | Trades | Fill-price gross | Commission | Funding cost | Net | Net/trade |
|---|---:|---:|---:|---:|---:|---:|
| absorption_reversal | 1 | 0.13 | 0.52 | 0.0000 | -0.39 | -0.39 |
| quiet_shock_reversal | 0 | 0.00 | 0.00 | 0.0000 | 0.00 | 0.00 |
| flow_continuation | 50 | -20.85 | 11.11 | 0.0101 | -31.97 | -0.64 |

## Selected setup cost reconciliation

Before spread/slippage and commissions: 0.00 USDT; modeled spread/slippage: 0.00; exchange commissions: 0.00; funding cost: 0.0000; final net: 0.00.
Executed entry-plus-exit notional: 0.00 USDT. Fees are charged per fill, not per trade count or deposited margin.

## Rejected setups

- absorption_reversal: {"no flow setup": 40097, "target too small after round-trip costs": 115, "invalid flow structural levels": 77, "net reward/risk below minimum": 21, "entry gap invalidated structural setup": 9}
- quiet_shock_reversal: {"no flow setup": 40320}
- flow_continuation: {"no flow setup": 39838, "invalid flow structural levels": 93, "target too small after round-trip costs": 153, "net reward/risk below minimum": 145, "position limit": 8, "daily loss or losing-streak pause": 5, "entry gap invalidated structural setup": 4, "daily entry limit": 24}

Gross above is after simulated spread/slippage, before commissions and funding. Each raw trade includes both fill fees, notional, reference prices and execution drag.

## Same-trade fee sensitivity

| Method | Taker/taker net | Maker/taker hypothetical net | Maker/maker hypothetical net |
|---|---:|---:|---:|
| absorption_reversal | -0.39 | -0.24 | -0.08 |
| quiet_shock_reversal | 0.00 | 0.00 | 0.00 |
| flow_continuation | -31.97 | -28.64 | -25.31 |

Maker columns change commission rates only on the same taker fills. They are NOT executable maker strategies: queues, missed fills and adverse selection are unmodeled. They cannot be used to claim lower-cost profitability.

Selected same-trade doubled-fee/slippage net: 0.00 USDT. Mean daily net bootstrap 95% interval: [0.00, 0.00]. Fourteen days and IID day resampling are limited evidence.

Controls: six entries/day across the portfolio, 0.3% planned daily commission budget including reserved exits, structural objectives at least twice round-trip costs, estimated net R:R >=1. Protective exits always override fee/entry caps. Gaps can exceed planned budgets.

No parameter search was performed. Flow studies use only completed one-minute bars; fills use next-minute open and pessimistic stop-first ties. Funding is settled at minute boundaries. Historical depth, lot constraints, partial fills, exact intrabar timing and taxes remain outside the model. Defaults are unchanged.

Research only: these flow signals are not connected to the live paper CLI. Trade-volume imbalance is not full order-book imbalance.


## Audit and interpretation

All 59 functional tests, compilation and dependency checks passed. Each validation trade reconciles gross minus both commissions and funding to net; no hold exceeds 30 minutes. Source and input hashes match the frozen protocol.

The ranked selection made zero trades, so its zero net and zero-width bootstrap interval provide no evidence of an edge. It failed minimum participation. No candidate is eligible.

| Candidate | Doubled fee/slippage net | 95% interval for mean daily net |
|---|---:|---|
| absorption_reversal | -1.13 | [-0.08, 0.00] |
| quiet_shock_reversal | 0.00 | [0.00, 0.00] |
| flow_continuation | -47.52 | [-3.29, -1.25] |

Continuation produced -11.96 USDT before modeled spread/slippage, commissions and funding. Its 34 weak-follow-through exits lost 20.98 USDT net; 12 stops lost 17.68. Fees worsened a signal failure. The failed-breakout reversal had only one validation trade: +0.13 after execution drag, erased by 0.52 commission. Quiet-shock conditions never coincided in validation.

Next distinct research direction: joint BTC/ETH residual mean reversion, using formal stationarity checks and a two-leg execution simulator with four charged fills. This was researched but not implemented or validated in this round. Full order-book imbalance would require collecting event data; it cannot be reconstructed from taker-volume bars.

No post-validation parameter tuning, automated promotion, or real exchange orders.