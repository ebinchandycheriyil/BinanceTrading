# ML on labelled entry points (BTC + ETH, 1h decisions, ≤12h trades)

## Dataset
174,725 hourly decision points (BTC Jul 2015 – Sep 2026, ETH Dec 2017 – Sep 2026); each has a BUY and a SELL label.
Rule: entry at the next 5m open, stop 1.5 × ATR(1h), target ≥ 1.5 × stop and ≥ costs + 0.30%, 12h limit;
label = 1 if the net result after BNB fees (0.045% taker / 0.018% maker) and slippage is ≥ +0.30%.
Base rate: only 34–38% of random entries clear the bar. 48 causal features (returns, volatility, 19-indicator
readings, volume/taker flow, time, 4h/daily trend, other-coin move, rule signals). Hindsight best-exit columns
are stored for analysis only.

Checks passed: labels equal the full simulator on 800 random trades (0.000 bp); all 48 features identical
when recomputed on data truncated at four dates (no look-ahead).

## Walk-forward (train on all earlier years, 13h purge, top-10% probability threshold calibrated inside training)

| Year | LightGBM | Logistic | Volume breakout | Random, same count | Shuffled labels |
|---|---:|---:|---:|---:|---:|
| 2019 | +27.6% | +24.8% | +9.2% | -45% | -20% |
| 2020 | +29.5% | +54.3% | +26.5% | -20% | -40% |
| 2021 | +101.0% | +20.8% | +7.6% | -27% | -28% |
| 2022 | +10.7% | -4.0% | +4.0% | -38% | -22% |
| 2023 | +2.3% | -9.4% | -0.8% | -43% | -70% |
| 2024 | +27.8% | +17.1% | +30.5% | -52% | -19% |
| 2025 Jan–Sep | +15.2% | +1.5% | +7.3% | -43% | -48% |
| **Holdout Oct 2025 – Sep 2026 (single look)** | **-21.4%** | **-12.4%** | **+12.5%** | -53% | -46% |

Rs1L account, Binance minimums, one position per coin. Robustness (walk-forward years): 5/10-minute entry delay
did not hurt; 3 seeds all positive every year; 1.5x fees positive in 6/7 years.

## Verdict
**Fails the final holdout.** The model ranks hours far better than chance (it beats random and shuffled
controls every period, holdout included), but in the latest 12 months its edge was smaller than costs: long side
-0.22% per trade, short -0.07%; losses concentrated in Oct–Nov 2025, Feb 2026 and Jun 2026. Plain volume
breakout made +12.5% in the same year. Not suitable for real money.

Holdout access is logged in `holdout_access.log`; the holdout can no longer be used to judge changes.
Files: `walkforward.csv`, `checks.csv`, `holdout.csv`, `feature_importance.csv`, `protocol.json`.
