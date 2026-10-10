# Fee-aware chart studies

Verdict: **no validated profitable setup**.

BTC/ETH USDT perpetuals; July 31–August 14, 2026 development; August 14–28 validation (UTC). Each fold starts with $1,000. Fresh historical interval; no legacy trade records.

Research and exact adaptations: [SOURCES.md](SOURCES.md). None of the source methods is assumed proven for this market/horizon.

## Development

| Method | Week 1 net | Week 2 net | Trades |
|---|---:|---:|---:|
| band_reentry | -0.76 | 0.00 | 1 |
| retest | 0.99 | 0.00 | 1 |
| trend_pullback | 0.00 | 0.00 | 0 |

Selected using development only: **retest**. No reselection using the validation table.

## Fourteen-day validation

| Method | Trades | Fill-price gross | Commission | Funding cost | Net | Net/trade |
|---|---:|---:|---:|---:|---:|---:|
| band_reentry | 0 | 0.00 | 0.00 | 0.0000 | 0.00 | 0.00 |
| retest | 16 | -0.62 | 6.11 | 0.0000 | -6.73 | -0.42 |
| trend_pullback | 0 | 0.00 | 0.00 | 0.0000 | 0.00 | 0.00 |

## Selected setup cost reconciliation

Before spread/slippage and commissions: 4.27 USDT; modeled spread/slippage: 4.89; exchange commissions: 6.11; funding cost: 0.0000; final net: -6.73.
Executed entry-plus-exit notional: 12218.69 USDT. Fees are charged per fill, not per trade count or deposited margin.

## Rejected setups

- band_reentry: {"target too small after round-trip costs": 32, "structural stop/target outside allowed range": 25, "net reward/risk below minimum": 7}
- retest: {"structural stop/target outside allowed range": 8, "target too small after round-trip costs": 5, "net reward/risk below minimum": 15, "position limit": 3, "daily entry limit": 1}
- trend_pullback: {"entry gap invalidated structural setup": 1, "structural stop/target outside allowed range": 3, "target too small after round-trip costs": 4, "net reward/risk below minimum": 2}

Gross above is after simulated spread/slippage, before commissions and funding. Each raw trade includes both fill fees, notional, reference prices and execution drag.

## Same-trade fee sensitivity

| Method | Taker/taker net | Maker/taker hypothetical net | Maker/maker hypothetical net |
|---|---:|---:|---:|
| band_reentry | 0.00 | 0.00 | 0.00 |
| retest | -6.73 | -4.90 | -3.06 |
| trend_pullback | 0.00 | 0.00 | 0.00 |

Maker columns change commission rates only on the same taker fills. They are NOT executable maker strategies: queues, missed fills and adverse selection are unmodeled. They cannot be used to claim lower-cost profitability.

Selected same-trade doubled-fee/slippage net: -15.28 USDT. Mean daily net bootstrap 95% interval: [-1.74, 1.02]. Fourteen days and IID day resampling are limited evidence.

Controls: six entries/day across the portfolio, 0.3% planned daily commission budget including reserved exits, structural objectives at least twice round-trip costs, estimated net R:R >=1. Protective exits always override fee/entry caps. Gaps can exceed planned budgets.

No parameter search was performed. Five-minute studies use only completed bars; fills use next-minute open and pessimistic stop-first ties. Funding is settled at minute boundaries. Historical depth, lot constraints, partial fills, exact intrabar timing and taxes remain outside the model. Defaults are unchanged.