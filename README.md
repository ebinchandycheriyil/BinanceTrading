# Headless Binance futures scalp research

This is a **paper-trading system**, using public Binance USD-M perpetual data through CCXT. It prints newline-delimited JSON: SIGNAL, ENTRY, EXIT, STATUS, DATA_HALT and RISK_HALT. It never reads API keys or submits exchange orders. There is no chart dependency.

**New automated carry workflow:** `python -m scalp.carry_cli paper` discovers public BTC/ETH dated-futures opportunities, applies fees/liquidity/capital-return gates, and automatically opens, monitors and exits qualifying **paper** spot/futures baskets. It persists accounts and pending exits across restarts. The current-market smoke test found no qualifying trades. This longer-horizon experiment is separate from the scalper; [capabilities and current results](artifacts/carry/REPORT.md).

A frozen 24-hour operational paper trial was launched on 26 September 2026. See its [protocol, timing and status/stop commands](artifacts/carry/forward_trial_20260926/README.md). That page describes the launch; use its status command for current liveness. The bounded supervisor in `scalp.carry_trial` stops new entries before the deadline and attempts paper closure at the end. Its forced trial-end exits must not be treated as full-convergence strategy results.

**Latest research:** no deployable edge has been established. An expanded 28-pair screen produced only nine evaluation baskets across 2022–2023. A one-minute execution diagnostic reduced their combined estimated profit from 6.80 to 1.95 USDT across two separate 1,000-USDT yearly accounts. This diagnostic freezes entries and sizes; it is not a full strategy replay. The recommendation is to stop the current profit-seeking parameter search and preserve the code for research. See the [viability decision and evidence](artifacts/pair_viability/REPORT.md).

**Multi-symbol intraday research (October 2026):** `daytrade/` is a vectorized 5m backtester over 10 perpetuals (2022–2026) with maker/taker/stop order types, trailing stops, funding, open-interest data and a dev/validation/logged-holdout protocol. Fifteen strategy families (364 configurations) were tested. Only `squeeze_oi` (a short-squeeze breakout confirmed by funding, open interest and retail positioning) passed validation; it failed the holdout under 1.5x costs. See the [report](artifacts/daytrade/REPORT.md). Run `python -m daytrade.research --help`.

## Run on Windows

For the new carry workflow:

```powershell
# Public opportunity/rejection output, without opening paper positions
.\.venv\Scripts\python.exe -m scalp.carry_cli scan
# Monitor continuously; Ctrl+C stops the process and preserves paper positions
.\.venv\Scripts\python.exe -m scalp.carry_cli paper --db carry_paper.db
# Read saved state or request paper closure using fresh quotes
.\.venv\Scripts\python.exe -m scalp.carry_cli report --db carry_paper.db
.\.venv\Scripts\python.exe -m scalp.carry_cli close --db carry_paper.db
```

Use `--minutes 60` for a bounded hour or `--once` for one cycle. A stopped process cannot monitor; open baskets persist for the next run. The `close` command retains exits as pending when quotes or depth are inadequate. Supply `--config carry_settings.json` **before** the subcommand to use the checked-in defaults explicitly. Resuming an account requires identical settings.

Carry decisions include every entry/exit fee, base-deducted spot-buy commission, conservative dust write-off, spread/slippage and a 4% annual imputed capital charge. Cash P&L and economic P&L are reported separately. Entry gates require at least 2 USDT projected economic surplus, 10% projected annualized surplus on spot cash plus full initial futures collateral, and positive doubled-cost surplus. These are declared screening assumptions, not achieved strategy returns; early exits can capture only part of the projected convergence. Up to two paper baskets are allowed, one per BTC/ETH underlying, with 40% per-basket and 80% total capital caps. Paper profit/loss guards are equal, capped at 0.5% of equity; a one-hour pre-expiry deadline, daily-loss limit and futures-collateral buffer can close earlier. The strategy does not satisfy the original 5–30-minute holding requirement.

All raw snapshots, rejections and paper entry/exit events are stored in a separate SQLite account. Synthetic tests exercise successful entries and exits, restarts and pending-exit recovery. Public smoke testing is a connectivity check, not evidence of profitability. Atomic two-leg fills are a simulation assumption; real fill reconciliation, partial fills, venue-native protection and account-specific requirements remain unimplemented. See [official sources and limits](artifacts/carry/SOURCES.md). No real exchange orders are supported.

The original scalping runner remains available:

```powershell
.\.venv\Scripts\python.exe main.py paper
```

The local environment has been installed. On a fresh checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py paper
```

`start_bot.bat` does the same. Linux: `sh start_bot.sh`. Python 3.10+; verified here with Python 3.14.7.

Useful commands:

```powershell
# One scan; simulated positions persist for the next run
.\.venv\Scripts\python.exe main.py paper --once
# Bounded monitored session; tries to close paper positions on shutdown
.\.venv\Scripts\python.exe main.py paper --minutes 30
# Reports include individual trades and diagnostic features
.\.venv\Scripts\python.exe main.py report
# Download closed 1-minute candles and run chronological comparisons
.\.venv\Scripts\python.exe main.py download --days 7
.\.venv\Scripts\python.exe main.py study
# Deterministic tests, no API keys or network calls
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`paper_trades.db` stores account state and an append-only event journal. Signals are deduplicated by symbol and closed candle. Restarts preserve entry time and exit deadline. Only one writer may use an account database. Use a separate `--db` for each experiment; saved accounts reject configuration changes. Use the same `--config` to report on a custom account.

## Trading rules

- BTC/USDT and ETH/USDT linear perpetuals, both BUY/long and SELL/short. One position per symbol; two positions total. Repeated trades allowed after a five-minute cooldown.
- Signals use closed 1-minute candles and complete aggregated 5-minute candles. EMA 20/50 trend bias, EMA 9/21 crossover or pullback reclaim, ADX, RSI, MACD, rolling 60-minute volume-weighted typical price, relative volume and ATR. The rolling volume price is not a session-anchored VWAP.
- HOLD is a valid output. No forced entry or claim that these filters create an edge.
- ATR stop, bounded to 0.2%-0.8%; configured estimated net reward:risk 1.5. The target includes a cost allowance. Realized R can differ, especially on gaps.
- $1,000 simulated starting equity; 0.25% planned risk per trade, 0.5% combined planned risk, gross notional capped at 1x equity. Fees and slippage are included in planned risk; the limits are not guarantees on loss during price gaps.
- Weak trades close after five minutes if favorable movement is below 0.15 stop-distance units. Otherwise a 30-minute deadline applies. Stops, targets, daily-loss exits and shutdown can close sooner than five minutes.
- Quote polling targets five seconds. Fresh bid/ask data is required for fills. Entries reject stale/missing quotes, unclosed/stale candles, wide spreads and insufficient size. Entry quantity rounds down to exchange precision, must meet exchange minimums, and is capped at 10% of visible best-level quantity.
- A 1.5% UTC daily equity drawdown halts new entries for the day and closes paper positions with available quotes. Three consecutive losses pause entries for 30 minutes.
- After at least 20 recent trades, negative net P&L halves planned trade risk. It returns to baseline if that rolling sample becomes nonnegative. No loss-chasing size increases or automatic parameter optimization.

## Configuration

Use `python main.py --config scalp_settings.json paper --db experiment.db`. The checked-in example has the defaults. Fractions are decimals: `0.0025` means 0.25%; fee/slippage values are basis points per side. Fees default to 5 bps per side and slippage to 2 bps per side. These are **assumptions**, not your account fee schedule. Override them for realistic research.

## Loss analysis and studies

Every exit records net/gross P&L, both commissions, entry features, holding time, sampled favorable/adverse excursion, exit reason and realized R. Diagnostic tags identify lack of follow-through, reversal after favorable movement, cost-erased profit, time expiry and delayed exits. These are observations, not causal proof of why a trade lost.

The study compares full confluence, a simpler EMA/trend trigger, removal of the volume filter, and doubled fees/slippage. A fixed chronological 70/30 development/holdout split follows 400 warmup bars. Each partition starts a fresh paper account. Signals enter on the next bar; no current/future candle enters the signal calculation. If a bar touches stop and target, the stop wins. Holdout is not used to choose parameters automatically.

Replay assumes the configured maximum spread, has no historical depth or partial-fill model, and does not apply exchange minimum lot filters. Intrabar trigger times are approximate, exits are all-or-none, and excursion diagnostics use sampled prices. The original study omits funding; rounds two and three include historical funding. Liquidation and taxes are not modeled. Small samples and a short holdout do not establish profitability. Drawdown in summary reports is closed-trade drawdown, not continuous equity drawdown. Profit factor is gross winning net P&L divided by absolute gross losing net P&L; null means no losing trades.

## Operational limits

This build supports paper orders only. Real or testnet order routing is not implemented in the new engine. Before such deployment, implement verified exchange fills, order-state reconciliation, partial fills, reduce-only protective orders, exchange-native stop recovery, funding and account-specific costs, and failure-injection tests.

A 30-minute exit is a target, not an offline guarantee: the process and fresh market data must be available. Network timeouts can delay the polling loop. On stale data the engine blocks new entries and records a DATA_HALT rather than inventing a fill. On restart it attempts overdue exits at the next fresh quote. Ctrl+C or the bounded-session deadline attempts paper closure; unfillable positions remain in the journal. `--once` intentionally leaves paper positions open.

The retired Python modules and obsolete connection scripts have been removed. The active engine is self-contained under `scalp/`. It does not use legacy trade records or `.env`.

## Second research round

```powershell
.\.venv\Scripts\python.exe -m scalp.experiments download
.\.venv\Scripts\python.exe -m scalp.experiments run
```

This declares five candidates before downloading a separate 21-day market sample: baseline, 15-minute patience, 30-minute patience, volatility/cost screening with 15-minute patience, and a volume-confirmed 20-bar breakout. Development comprises two consecutive seven-day folds; a candidate is selected using its weaker development fold. Only then is it evaluated on the seven-day validation interval. The validation interval is earlier historical data not used in the first study, not a prospective forward test.

The expanded functional suite passes 35 tests. The round includes historical funding charges and doubled-cost stress. At a funding boundary, carried positions settle before exits and new entries; sub-minute ordering is approximate. The default `paper` runner still does not accrue funding automatically. The experiment's movement/cost ratio uses ATR times the square root of the patience interval as a volatility screen, not a forecast of profit.

`artifacts/round2/protocol.json` records the selection and pass criteria. `selection_before_validation.json` records the development decision. `results.json`, `REPORT.md` and `validation_trades.csv` contain the outcomes. Candidate settings are research-only; the runner does not automatically promote them or change defaults.

To measure current public-book spreads and top-level liquidity without entering trades:

```powershell
.\.venv\Scripts\python.exe -m scalp.observe --seconds 60
```

The resulting snapshot does not measure slippage or historical spreads. It is a diagnostic for execution assumptions, not a reason to retroactively lower backtest costs.

## Fee-aware chart research

## Iterative swing research

### Expanded-pair viability and execution audit

Run `python -m scalp.pair_viability download`, `python -m scalp.pair_viability run`, `python -m scalp.pair_minute_check`, then `python -m scalp.pair_viability_audit`. Optional research dependencies are in `requirements-research.txt`.

This bounded study checks all 28 combinations of eight fixed surviving coins. Daily liquidity, cointegration, price-series stationarity diagnostics, multiple-testing correction and half-life gates determine eligibility. Six configurations are compared on July 2020–December 2021; one is frozen before separate 2022 and 2023 evaluations. Those market years were previously examined for directional strategies, so they are not globally untouched data. None of the development configurations reached the preferred 30-basket sample.

The new simulator fixes funding event ordering using actual timestamps. Futures taker commission is 0.05% per fill, with 0.04% spread and 0.02% slippage per side. One basket can be active, with equal gross profit/loss guards and a 72-hour deadline. This is a longer-horizon alternative to the original scalping requirement. The minute diagnostic rechecks the nine frozen evaluation baskets against actual minute candles and exact holding intervals. It flags nonsynchronous OHLC stop ambiguity without inventing fills. Historical funding marks, simultaneous fills, lot filters and survivorship remain limitations. [Full results](artifacts/pair_viability/results.json), [minute diagnostic](artifacts/pair_viability/minute_execution.json), and [accounting/test verification](artifacts/pair_viability/verification.json).

### Alternative strategies: pairs, funding carry, relative momentum

Install optional research dependencies with `python -m pip install -r requirements-research.txt`. Run `python -m scalp.alternative_study download`, `python -m scalp.alternative_study run`, then `python -m scalp.alternative_audit`. The bounded process tests 21 initial configurations, 27 small refinements, and three frozen evaluation configurations plus three cost-stress reruns. It uses July 2020–December 2021, reserving July–December 2021 for the final evaluation.

The new basket simulator supports cointegration-screened BTC/ETH hedges, equal-quantity spot/perpetual funding carry, and four-leg relative momentum across eight fixed surviving coins. It uses actual public spot/futures history and funding, charges every leg, and blocks carry execution during missing spot candles. Spot fees are assumed 0.10% per fill, futures 0.05%. Basket profit/loss guards are 1:1 before fees/funding, observed hourly; simultaneous execution and historical liquidity remain approximations. This research does not enable live trading.

Final evaluation on 1,000 USDT per strategy: pairs +3.31 USDT across only three baskets; carry +3.69 across nine, falling to -1.69 under doubled costs; relative momentum -14.50 across 34. None passed the acceptance criteria. [Detailed report](artifacts/alternatives1/REPORT.md), [all iterations](artifacts/alternatives1/all_iterations.csv), [evaluation legs](artifacts/alternatives1/evaluation_legs.csv).

### Fixed-timeline parameter sweep

The subsequent fresh-data study is in [artifacts/swing_fresh/REPORT.md](artifacts/swing_fresh/REPORT.md). Run `python -m scalp.swing_fresh download`, `python -m scalp.swing_fresh run`, and `python -m scalp.swing_fresh_audit`. It first evaluates the previous winner on January–February 2026, tests 27 small changes only in that window, and freezes the selection before March–April. The refined 28-bar / 4 ATR / 48h configuration lost 1.73% in the holdout versus 2.20% for the previous configuration. No candidate passed the acceptance gates or was promoted. These are historical tests, not live paper results.

At the user's request, `python -m scalp.swing_sweep` repeatedly evaluates the same full-year 2025 chart with 270 base configurations and 16 unique local refinements. Entry charts vary between 1h/2h/4h, range/EMA lookbacks between 10/20/30 bars, ATR stops between 2 and 4, and holding limits between 24/48/72 hours. Local changes use 0.25 ATR and six-hour increments. All targets retain equal stop distance; hourly execution and funding accounting stay fixed. `python -m scalp.swing_sweep_audit` checks accounting and writes detailed attribution.

The best same-data result was 2h breakout / 30-bar range / 3.75 ATR / 54h maximum hold: +2.63% after costs, 138 trades, 1.81% hourly measured drawdown. Twenty-five of 286 configurations were positive. This is an exploratory, selected backtest; the bootstrap interval crosses zero and no independent validation or deployment is claimed. [Detailed report](artifacts/swing_sweep/REPORT.md), [all parameter runs](artifacts/swing_sweep/all_runs.csv), and [best trades](artifacts/swing_sweep/best_trades.csv).

### Earlier development and validation

The separate research engine in `scalp/swing.py` supports hourly entries, completed four-hour trend filters, 24/72-hour holding limits, equal stop/target distances, funding, gap exits and hourly marked equity. It does not change the scalping CLI or send exchange orders.

Run `python -m scalp.swing_study download`, then `python -m scalp.swing_study run`, and `python -m scalp.swing_audit`. The frozen process compares breakout and pullback families in 2022–2023, refines the selected family over two 2024 halves, and validates once on 2025. Selection is recorded before validation. It is a bounded search, not a claim to find a global optimum. Results and limitations are in [artifacts/swing1/REPORT.md](artifacts/swing1/REPORT.md).

The best-ranked configuration was breakout with 3 ATR stop/target distances and a 24-hour maximum hold. It failed validation: -7.52% net over 338 trades in 2025. No strategy was promoted and no background swing process is running.

## Momentum with 1:1 targets

Run `python -m scalp.momentum_study download` followed by `python -m scalp.momentum_study run`. Round5 compares fixed 5-minute and 15-minute momentum signals on June 29–July 27, 2026, splitting development and validation. See [results](artifacts/round5/REPORT.md) and [raw ledger](artifacts/round5/validation_trades.csv). Both lost money in validation; defaults remain unchanged.

The new `reward_risk_basis="price"` uses equal executable stop/target distances when `reward_risk=1`. The existing default `"net"` retains its cost allowance. Fees make net outcomes asymmetric even with 1:1 price distances. Momentum configs preserve five-minute weak-follow-through exits and the 30-minute deadline; protective exits can occur earlier. This is not a pure stop-or-target-only experiment. Both strategies are available to the paper CLI through their round5 settings files; no exchange orders are supported or sent.

### Earlier chart study

The latest research round is documented in [artifacts/round4/REPORT.md](artifacts/round4/REPORT.md), with primary sources and exact rules in [SOURCES.md](artifacts/round4/SOURCES.md). Run `python -m scalp.flow_study download` then `python -m scalp.flow_study run` to reproduce the frozen trade-flow experiment. This uses public Binance taker-buy base volume, with failed-breakout reversal, quiet-shock reversal and flow continuation hypotheses. These signals are research-only and are not selectable in the paper CLI. The generated round4 candidate settings describe shared risk controls, not a deployable flow strategy. None passed validation; no defaults were changed. The independent market interval is May 31–June 28, 2026; no legacy trade database is read.

```powershell
.\.venv\Scripts\python.exe -m scalp.fee_study download
.\.venv\Scripts\python.exe -m scalp.fee_study run
```

This tests Bollinger range re-entry, support/resistance breakout retests, and EMA20 trend pullbacks on complete five-minute candles. Each setup supplies a structural stop and objective, rechecked against the actual simulated entry. The target must exceed twice estimated round-trip costs, with estimated net reward/risk at least 1.0. The research settings limit the entire portfolio to six new entries per UTC day and planned daily commissions to 0.3% of day-start equity, reserving an allowance for exits. Protective exits bypass entry and commission caps; actual gaps can exceed plans.

All executable simulations use taker fees on entry AND exit. The agreed assumptions are 5 bps taker and 2 bps maker, without discounts. Maker fee columns are hypothetical sensitivity calculations on the same taker fills, not maker-execution backtests. The maker schedule is implemented in `scalp/costs.py`; active market fills use the configured `fee_bps` as taker commission. The CSV itemizes each side's fee, each side's notional, execution drag, funding and net P&L.

Research configs are written to `artifacts/round3/candidate_settings.json`; defaults are not automatically promoted. A diagnostic paper scan can be run explicitly using that file with a separate account:

```powershell
.\.venv\Scripts\python.exe main.py --config artifacts/round3/candidate_settings.json paper --once --db research_paper.db
```

`--once` can open a simulated position and leave it persisted; use `paper --minutes 30` for a bounded monitored session. These remain research/paper modes with no exchange order submission.
