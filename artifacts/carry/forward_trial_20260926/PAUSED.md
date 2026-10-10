# User-requested pause

The user requested a pause before shutting down the laptop. The worker exited gracefully with `STOPPED_EARLY`, `stop_reason=operator_request`. Its recorded process was verified absent. The `carry-paper-trial-review` heartbeat was updated to `PAUSED`.

Saved checkpoint: 94 observations, no operational errors, no entries or completed trades, no open positions, 1,000 USDT simulated balance. The SQLite account, raw observations, frozen protocol, status and final account report are retained in this directory.

Do not restart or unpause anything until the user explicitly resumes. On resume, preserve this segment and its original absolute deadline, settings and results. The current runner intentionally rejects restarting a terminal `STOPPED_EARLY` trial. Set up a clearly identified continuation segment or explicit resume support; do not simply erase the stop marker, overwrite the historical protocol, or count laptop downtime as monitored market time. Keep the settings unchanged and record the observation gap. No real-money routing has been authorized or implemented.
