# Momentum with 1:1 price targets

Selected before validation: momentum5. Acceptance passed: False.

Development June 29–July 13, 2026; untouched validation July 13–27 UTC. Each fold begins with 1,000 USDT.

| Method | Dev week 1 net | Dev week 2 net | Validation trades | Net | Fees | Win rate | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| momentum5 | -19.39 | -20.81 | 84 | -34.26 | 22.10 | 11.9% | 0.1957625537900706 |
| momentum15 | -12.48 | -20.90 | 84 | -35.37 | 22.28 | 9.5% | 0.1689262352554402 |

Targets/stops are exactly 1:1 from the actual simulated entry. Commissions and funding are deducted separately; this is not net 1:1. Minimum distance 0.4%, maximum 0.8%, otherwise 2 times minute ATR. At 0.4% executable fill-price distances and approximately 0.10% round-trip commission, binary-outcome break-even is approximately 62.5% wins, ignoring funding and early exits. Spread/slippage affect entry and barrier reachability; do not subtract them again from an already executable stop/target payoff.

Five-minute weak-follow-through exit, hard 30-minute deadline; targets/stops may exit sooner. Risk 0.25% per position, total 0.5%, gross exposure <=1x equity, six entries/day and 0.3% daily commission budget. No limit-fill assumptions.

Signals: 5- or 15-minute signed log return exceeding max(0.15%,1.5 ATR/price), relative volume >=1.2, EMA9/21 alignment and EMA21 slope. Fresh threshold crossing or five-bar breakout; no entry beyond three ATR from EMA9. All calculations use closed bars only. Two fixed hypotheses; no parameter search.

Research motivation: [Wen et al., intraday momentum and reversal](https://www.sciencedirect.com/science/article/pii/S1062940822000833). These rules are original hypotheses, not a replication or a profitability claim.

Limitations: retrospective validation, assumed spread/slippage, no historical depth/partial fills/lot filters/taxes; daily bootstrap assumes independent days. No live orders or automatic default changes. Raw trades, rejection reasons, loss diagnostics and per-method cost stress are in results.json.