# Automated carry paper workflow

Implemented: public opportunity scanning, cost/depth/return gates, persistent paired paper entries, monitoring, automatic paper exits and loss diagnostics. No private API calls or real order routing.

Bounded public-market check: 4 snapshots, 0 paper entries, 0 closed baskets, 0 remaining open. Cash balance 1000.00 USDT. The process stopped after the bounded test; no background monitor is running.

This short run tests connectivity and decision plumbing. It does not measure future opportunity frequency, attainable real fills or profitability.

Observation interval: 2026-09-26T12:35:16.227094+00:00 to 2026-09-26T12:37:20.486183+00:00.

| Contract | Observed basis | Estimated net surplus | Decision / reasons |
|---|---:|---:|---|
| BTCUSDT_261225 | 1.176% | no feasible size | quantity_below_step |
| BTCUSDT_270326 | 2.482% | no feasible size | quantity_below_step |
| ETHUSDT_261225 | 0.898% | -0.63 USDT | annual_return_below_hurdle; execution_cost_exceeds_stop_budget; negative_under_cost_stress; net_profit_below_hurdle |
| ETHUSDT_270326 | 2.017% | no feasible size | quantity_below_step |

The basis is the futures bid premium over the spot ask, before costs and before reserving both spot cash and futures collateral. It is not an account return. Projected convergence APR is a screening scenario, not achieved strategy APR: early profit/loss exits can capture only part of the estimated convergence. A positive basis can still fail all economic gates.

## Implemented rules

- Public BTC/ETH spot and linear USDT quarterly futures only; contract discovery and exchange quantity filters are refreshed. Up to two baskets, at most one per underlying asset. A completed expiry contract is not re-entered.
- 1,000 USDT paper capital; at most 40% reserved per basket and 80% in total, checked against projected post-entry liquidating equity. Reserve spot purchase cash plus 100% initial futures notional collateral. No borrowing or automatic collateral transfers.
- Assumed spot commission 0.10%, futures commission 0.05%, slippage 0.02% per fill. Buy enough spot to cover its base-asset fee; hedge only retained tradable inventory, and conservatively write off rounding dust.
- Four commissions, estimated exit spread/slippage, 0.10% residual-basis reserve, and a 4% annual opportunity charge on committed capital. Require at least 2 USDT estimated surplus and 10% annualized surplus after that charge, plus positive surplus under doubled fees/slippage/reserves. These are declared research hurdles, not optimized returns or promises.
- Entry size is capped at 10% of displayed best-level liquidity. Fresh books, bounded capture skew and a fresh futures mark are required. Spot depth uses local request age because the endpoint has no exchange timestamp.
- Planned exit one hour before expiry, with earlier profit/loss, collateral-buffer and daily-loss exits. Paper profit/loss thresholds are equal (1:1 economic P&L), capped at 0.5% of equity and 80% of projected surplus. Other exit rules and gaps mean realized outcomes differ.
- Persist state, raw observations and all entry/rejection/exit events in SQLite. Failed exits remain pending across restart. Missing data never becomes a simulated fill. Expired unresolved positions require settlement reconciliation.
- Show cash P&L separately from imputed capital cost and economic P&L. Loss tags describe cost drag, basis/execution loss and collateral exits; they do not automatically optimize strategy parameters.

## Run

```powershell
.\.venv\Scripts\python.exe -m scalp.carry_cli scan
.\.venv\Scripts\python.exe -m scalp.carry_cli paper --db carry_paper.db
.\.venv\Scripts\python.exe -m scalp.carry_cli report --db carry_paper.db
.\.venv\Scripts\python.exe -m scalp.carry_cli close --db carry_paper.db
```

The paper process runs until Ctrl+C unless --minutes is supplied. Stopping preserves open baskets; it does not keep monitoring after shutdown. Use the same config/account when resuming. close requests paper liquidation at fresh executable quotes and retains any unfilled exit as pending.

## What is still required for real automation

A repeatable positive forward sample, measured execution costs, partial-fill and one-leg recovery, authenticated fee/filter/account checks, exchange order reconciliation, native protection and explicit capital/loss limits. The current paired fills are atomic paper assumptions. Full initial collateral does not eliminate liquidation risk; the buffer trigger is not an exchange liquidation model. Exit basis and prices remain uncertain.

This is a longer-horizon carry experiment, not a five-to-thirty-minute scalper. Continue collecting prospective observations without lowering hurdles to force trades. If meaningful net opportunities do not emerge, stop this branch.

Verification: 131 tests passed, compilation and dependency checks passed, saved cash ledger reconciled. Synthetic tests verify entry/exit/restart paths; the public run does not fabricate a trade when no opportunity qualifies.

[Official sources and model limits](SOURCES.md) | [Raw forward events](forward_events.json) | [Verification](verification.json)