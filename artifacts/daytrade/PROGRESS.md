# Strategy research progress log

Running log of what was tried, what was learned, and what is running. Newest entries at the bottom.
Detailed numbers: [REPORT.md](REPORT.md). Every config: `dev_grid*.csv`.

## Evidence rules (do not relax)
- Select on development data only (2022-01 → 2024-07, original 10 coins).
- Validation (2024-07 → 2025-07) once per selection. Holdout (2025-07 → 2026-10) and the 29 unseen coins are
  single-use; every access is in `holdout_access.log`.
- Costs: Binance regular fees (0.02% maker / 0.05% taker, confirmed on Binance FAQ 2026-05-01) + slippage + funding.
  A config must also survive 1.5x costs.

## 2026-10-02
| Step | Result |
|---|---|
| Diagnosed 24h carry trial | 0 trades: $1,000 baskets below lot step / under profit hurdle; DNS outages |
| Round 1 – 8 classic intraday families, 208 configs | all lose; ~zero gross edge, fees are the loss; fades lose even before fees |
| Round 2 – trend/NR7/RSI2/seasonality/funding, 88 configs | only `funding_squeeze` promising |
| Round 3 – funding + open interest + retail ratio | `squeeze_oi` 16/16 positive, passed validation (+8.8%), failed holdout under 1.5x costs (+3.6% base) |
| Forward paper run started 03:19 UTC | frozen 48h `squeeze_oi`, 39 coins, `live_squeeze_oi/` |
| Hold-time diagnostic (dev) | edge needs time: 1h -5%, 2h -1%, 4h +9%, 8h +22%, 12h +33%, 24h +78% |
| Round 4 – intraday `squeeze_day` (holds ≤ 12h), 192 configs | 140/192 positive, 4 qualify (all 12h); maker "retest" entries worse (miss best squeezes) |
| Round 4 validation | +3.5%, PF 1.08, DD 6.7%, avg hold 10h; negative at 1.5x costs → fails |
| Playground created (`playground/`): own code/data/results; experiments run there, main project untouched | |
| Indicator lab (playground) – 19 indicators, 304 configs, 12h max hold | 11/304 profitable in dev; 15m charts 0% profitable; nothing passed all tests |
| Unseen-coin test (29 coins, 2022–2026) | squeeze_oi 48h -15.7%, squeeze_day -9.8%: worked 2022–23 on every coin set, lost 2024–26 → edge decayed. volume_breakout (1h, 20-bar high + volume > 3x, EMA200, trail) best: +32% validation, unseen ≈ breakeven (-0.8%, avg +0.025R), fails at 1.5x costs |

| BTC-only long history (playground): Coinbase 2015-07..2017-11 + Binance spot 2017-12..2026-09, 1.18M 5m bars; 536 configs, every trade ≤12h; pick 2015–20, check 2021–23, test 2024–26 | 104/536 profitable in 2015–20; nothing passed every gate. **volume_breakout** (1h, close > 20-bar high & volume > 3x, EMA200, trail) positive in all three periods (+74.9% / +5.3% / +10.9%), +104% over 2015–26, max DD 9.8%, avg hold 8.3h, 9 of 12 years positive; fails only 1.5x cost in 2024–26 (-0.6%). Same setting was picked independently by the 10-coin lab. ORB, VWAP fade, sweeps, cascades, MACD, RSI, stochastic all lose. |
| Test 1 – fees (playground/artifacts/daytrade/combo) | BNB discount adds ~+2–4% per period; maker (limit) entry helps 2024–26 but misses fast breakouts earlier → keep market entry + BNB |
| Test 4 – portfolio VB + BB breakout + squeeze, BTC+ETH | combining did NOT help (weaker strategies take slots); volume_breakout alone is best |
| Volume breakout on ETH (never used for selection) | positive every full year 2018–2026; 2018–20 +44.5%, 2021–23 +6.0%, 2024–26 +40.6% (DD ≤ 10%). BTC+ETH 2024–26: +58.8% (DD 10%) at Rs1L |
| Account size (portfolio) | Rs10k works on ETH; BTC min order ~$86 forces ~0.6–0.7% risk/trade instead of 0.5% (higher DD) |
| Test 2 – funding carry, 39 coins (playground/artifacts/daytrade/carry) | ~5–7%/yr, max DD < 3%, works from Rs10k (coins with $5 minimum); mid-2024–mid-2025 only +1.5% |
| Test 3 – recorder started 2026-10-02 10:54 local | liquidations (all coins) + BTC/ETH book every 5 s → playground/artifacts/daytrade/recorder/ |
| Paper run #3 started 2026-10-02 05:34 UTC – funding carry, Rs10k, 33 coins with spot+perp (`playground/artifacts/daytrade/live_carry`) | live logic reproduces backtest exactly (15/9/51 entries, identical returns); also measures spot–perp basis drift the backtest ignored |
| First live paper trades (05:00 UTC) | VB long BTC @ 86,632 (stop 85,612); squeeze long LTC @ 69.73 |
| Fix: a second copy of the VB runner was writing to the same folder (duplicate events, cleaned). All runners now hold a single-instance lock (`run.lock`) and refuse a duplicate start | |
| ML (playgroundML/): 174,725 labelled hourly buy/sell entries (BTC 2015–, ETH 2017–), label = net ≥ +0.30% after fees within 12h; LightGBM + numpy logistic, walk-forward 2019–2025 | LightGBM positive every walk-forward year (+2% to +101%), robust to entry delay and seeds; **failed the single holdout Oct 2025–Sep 2026: -21.4%** (VB +12.5% same year). scikit-learn DLLs are blocked by Windows application control → numpy/LightGBM-native used |
| ML evolutionary search (playgroundML/artifacts/ml/evolve): 240 systems, 5 model families, 3 runs x 5 generations; train 2015–20, select 2021–22, test 2023–Sep 2026 once | all 9 finalists profitable on test (+16% to +63%, DD 7–17%), 6/9 survive 1.5x fees; but validation (+24% to +126%/yr) hugely overstated them, and plain volume breakout made +55.2% on the same test. Trees (LightGBM rf/et/gbdt) >> logistic/MLP. Best: lgbm extra-trees, 1.5 ATR stop, 2R, 12h, top 10%, both sides, no calendar: +63.2% (DD 15%), +23.5% at 1.5x fees |
| WSL: Ubuntu installed, needs Windows restart; `restart_runners.ps1` relaunches all 4 background processes; `playgroundML/wsl_setup.sh` + `daytrade/ml_evolve_sk.py` ready (adds sklearn RF/ET/HGB) | done after restart: Ubuntu + scikit-learn + PyTorch 2.14/CUDA 13 + XGBoost on RTX 5050 inside WSL |
| WSL search with scikit-learn trees (playgroundML/artifacts/ml/evolve_wsl) | 8/9 finalists profitable on 2023–26; best scikit-learn RandomForest +48.4% (DD 7%, +29.9% at 1.5x fees); highest-validation system (HistGB) lost -20% |
| GPU deep learning (playgroundML/artifacts/ml/deep): 24 CNN/GRU/Transformer designs (41k–1.5M params), 3-seed ensembles | 18/24 lost in 2022; both Transformer ensembles +40% on 2023–26 (DD 8–10%, +20–22% at 1.5x fees). Bigger nets were worse. Fix applied: bf16 + batch 512 + 15-min cap (a GPU-memory spill had stalled the first run) |
| Combined (playgroundML/artifacts/ml/COMPARISON.md) | 19/20 ML finalists profitable on unseen years, but none clearly beats plain volume breakout (+55%); best ML ≈ same return at half the drawdown. 2023–26 test now used 3x |
| Paper run #4 started 2026-10-02 15:33 UTC – ML Random Forest (WSL search run22_#1), BTC+ETH, `playgroundML/artifacts/ml/live_rf` | model trained in WSL on 2015–Sep 2026, exported to numpy (matches scikit-learn to 3e-16) so it runs on Windows; live-walk = backtest on 58/58 trades. Fixed: live runner now stores the take-profit target. Head-to-head with paper run #2 (volume breakout) |
| Note | files written from WSL into the OneDrive folder can vanish – copy WSL results with `wsl tar ... | tar -x` from Windows |
| 2026-10-02 19:00 UTC – all 4 paper bots + recorder moved to Oracle VM (E2.1.Micro, 1 GB RAM, Oracle Linux 9, `opc@129.225.83.139`, `deploy/vps/`) | laptop runs stopped, state copied (history continues). 1 GB fit: systemd timer runs each bot `--once` in turn 25 s after every 5m close (peak 78–116 MB each, whole cycle ~30 s), recorder always on without pandas (cap 200 MB). Python 3.12 via `uv` (dnf froze the VM); PCP monitoring + dnf-makecache timers disabled; +2 GB swap. Status: `ssh ... 'bash ~/trade/deploy/vps/status.sh'`. `restart_runners.ps1` now refuses without `-OnLaptop` |
| 2026-10-02 19:10 UTC – 3 more paper bots on the VM (7 total) | `ml_lgbm` run22_#1 (LightGBM extra-trees, best ML test +63.2%) → `playgroundML/artifacts/ml/live_lgbm1`; run22_#3 (most fee-robust, +48.2%, +31.1% at 1.5x fees) → `live_lgbm3`; both trained on 2015–Sep 2026 on Windows, saved as LightGBM text (`live_lgbm_run22_1/3`), share `ml_live.signals` with ml_rf. Volume breakout on the other 8 core coins (SOL XRP BNB DOGE ADA AVAX LINK LTC) → `playground/artifacts/daytrade/live_volume_breakout_alts`. Full cycle ~45 s, ~500 MB still free |
| 2026-10-03 – ML x volume-breakout agreement (playgroundML/artifacts/ml/agree) | VB trades confirmed by LightGBM earned 2-4x more per trade in BOTH 2021-22 and 2023-26 (LGBM#1: +0.29% vs +0.09%/trade on test; LGBM#3 +0.39% vs +0.10%); RF agreement inconsistent (-0.17% on test); ML-confirms-ML did not hold on test. Agreement-only filter cuts trades ~80% -> lower total return; candidate: bigger size on agreed VB trades (judge forward) |

## Open questions / next ideas
- ML next (judge only on forward paper data now): meta-label filter on volume-breakout signals; monthly retraining; positioning features from perps.
- Add ETH to the volume_breakout paper run? (already included: BTCUSDT + ETHUSDT)
- After ~4 weeks of recorder data: test liquidation-spike and book-imbalance signals.
- Forward paper run #2 started 2026-10-02 04:52 UTC: volume_breakout on BTCUSDT + ETHUSDT perps, run from
  `playground/artifacts/daytrade/live_volume_breakout` (pid in pid.txt). Live-walk check reproduced backtest
  trades exactly (BTC Mar–Apr 2025 26/26, ETH Oct–Nov 2025 26/26). Expect ~2–3 trades per coin per week.
- Forward paper results by coin group (core 10 vs extended) once ≥ 100 closed trades.
- Deployment to an always-on VPS in an Asian/EU region (Binance blocks US IPs; Colab unsuitable).
