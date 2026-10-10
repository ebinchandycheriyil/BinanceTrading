# Carry paper trial stopped on feed failure

The shortened trial stopped at **2026-09-27 06:44:21 UTC** after five consecutive market-data failures. All public spot, dated futures and mark-price requests failed with Windows DNS resolution error `getaddrinfo failed` / `Errno 11001`. The safety cutoff worked. The worker is no longer running, and follow-up automation is paused.

There was **no position, no trade and no realized P&L**. The simulated account remains at **1,000 USDT**. No exit or settlement is outstanding.

The account recorded 572 cumulative market snapshots (including 99 preserved from the earlier segment), 2,264 contract rejections, and six data halts: one earlier recovered interruption followed by the five consecutive failures that stopped the trial. Most rejections reflected projected returns below the cost/capital hurdle or quantity below the exchange lot step. The recorded opportunities did not produce a paper entry.

The final successful market sample was at 06:41:51 UTC; the five failed cycles followed at approximately 30-second intervals from 06:42:21 to 06:44:21 UTC. The saved event journal and raw snapshots remain in `account.db`; the shortened protocol and resume manifest are unchanged.

This is an operational failure, not a strategy loss. It leaves no evidence of profitability. On a future explicit resume, verify DNS/connectivity first and create a new continuation that retains this segment and its observation gap. Do not restart this terminal trial or send live orders.
