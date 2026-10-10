# Prospective carry paper trial

Started and verified running on 26 September 2026 at 23:29 IST (17:59 UTC). Initial observation: four usable contracts, no data errors, no qualifying entries, 1,000 USDT simulated balance.

This is a 24-hour operational test of frozen rules. It does not establish the profitability of holding carry positions to convergence. All orders and positions are simulated; no credentials or real order routing are used.

- New entries stop on 27 September at 22:59 IST.
- The worker requests paper closure on 27 September at 23:29 IST.
- Blocked exits may retry until 28 September at 00:29 IST; any remaining positions are explicitly unresolved.
- Checks run approximately every 30 seconds. The computer must remain awake and connected; the worker does not prevent sleep or configure automatic login/startup.
- Five consecutive market-data failures, changed strategy source/settings or an operator stop request halt entries and begin paper closure.
- An hourly task follow-up reports material changes, failure, or completion. It stays quiet when there is no material change.

The complete strategy package and settings are fingerprinted in [protocol.json](protocol.json). Do not edit the strategy during this trial. Its end time is absolute and does not extend on restart.

Read current state from the project directory:

```powershell
.\.venv\Scripts\python.exe -m scalp.carry_trial status --root artifacts/carry/forward_trial_20260926
```

Request an orderly stop and paper closure:

```powershell
.\.venv\Scripts\python.exe -m scalp.carry_trial stop --root artifacts/carry/forward_trial_20260926
```

Status is read without taking the writer lock and includes heartbeat/data age. A saved RUNNING label alone does not prove the process is alive. [status.json](status.json) is updated every cycle; [worker.json](worker.json) records the process identity. The SQLite account stores raw observations and decisions. The final account report is written when the worker exits.

Preflight: 139 tests passed, compilation passed, and dependency checks passed. Coverage includes delayed data fetches crossing the entry/end deadlines, stop requests arriving during a fetch, stale-data failure handling, account restart, pending exit recovery and unresolved positions after the final retry deadline.
