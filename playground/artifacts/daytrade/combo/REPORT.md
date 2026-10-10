# Fees, portfolio and account-size test (BTC + ETH, day trades <= 12h)

Frozen strategies from the BTC study; ETH not used for selection. USD/INR 96.38. ETH data starts Dec 2017, so 2015–2020 is mostly BTC. Each period starts a fresh account; returns are for the whole period.

## Test 1 – fee scenarios (Rs1L account, BTC + ETH)

| Strategy | Fees / entry | 2015-2020 | 2021-2023 | 2024-2026 |
|---|---|---:|---:|---:|
| volume_breakout | regular fees, market entry | +146.8% (DD 5%) | +8.6% (DD 16%) | +54.4% (DD 10%) |
| volume_breakout | BNB fees, market entry | +154.3% (DD 5%) | +11.1% (DD 15%) | +58.8% (DD 10%) |
| volume_breakout | BNB fees, maker (limit) entry | +119.8% (DD 5%) | +6.3% (DD 13%) | +62.6% (DD 7%) |
| bb_breakout | regular fees, market entry | +136.4% (DD 8%) | +2.0% (DD 18%) | +22.2% (DD 13%) |
| bb_breakout | BNB fees, market entry | +136.9% (DD 8%) | +5.8% (DD 15%) | +23.5% (DD 13%) |
| bb_breakout | BNB fees, maker (limit) entry | +77.4% (DD 9%) | -9.0% (DD 25%) | +29.2% (DD 10%) |
| squeeze | regular fees, market entry | +53.9% (DD 7%) | -18.1% (DD 28%) | +1.8% (DD 13%) |
| squeeze | BNB fees, market entry | +60.4% (DD 7%) | -17.2% (DD 27%) | +3.7% (DD 12%) |
| squeeze | BNB fees, maker (limit) entry | +44.3% (DD 6%) | -13.5% (DD 23%) | +3.4% (DD 12%) |
| PORTFOLIO (all 3) | regular fees, market entry | +159.6% (DD 8%) | -2.4% (DD 24%) | +9.2% (DD 17%) |
| PORTFOLIO (all 3) | BNB fees, market entry | +176.9% (DD 7%) | +1.9% (DD 23%) | +17.0% (DD 16%) |
| PORTFOLIO (all 3) | BNB fees, maker (limit) entry | +94.9% (DD 8%) | -5.1% (DD 25%) | +23.2% (DD 16%) |

## Test 4 – single strategy vs portfolio (BNB fees, market entry, Rs1L)

| Strategy | 2015-2020 | 2021-2023 | 2024-2026 |
|---|---:|---:|---:|
| volume_breakout | +18.5%/yr · Sharpe 1.87 · 71% months up | +3.6%/yr · Sharpe 0.40 · 47% months up | +18.3%/yr · Sharpe 1.34 · 67% months up |
| bb_breakout | +17.0%/yr · Sharpe 1.49 · 71% months up | +1.9%/yr · Sharpe 0.21 · 56% months up | +8.0%/yr · Sharpe 0.65 · 64% months up |
| squeeze | +9.0%/yr · Sharpe 1.31 · 65% months up | -6.1%/yr · Sharpe -0.79 · 42% months up | +1.3%/yr · Sharpe 0.21 · 52% months up |
| PORTFOLIO (all 3) | +20.3%/yr · Sharpe 1.56 · 77% months up | +0.6%/yr · Sharpe 0.11 · 47% months up | +5.9%/yr · Sharpe 0.44 · 58% months up |

## Account size – portfolio, BNB fees, market entry

| Start | Period | Trades taken | Return | Max DD | Ends at | Avg risk per trade |
|---|---|---:|---:|---:|---:|---:|
| Rs10k | 2015-2020 | 1791 | +193.9% | 10.7% | Rs29,392 | 0.59% |
| Rs10k | 2021-2023 | 1371 | +19.5% | 27.6% | Rs11,946 | 0.70% |
| Rs10k | 2024-2026 | 1292 | +21.1% | 24.3% | Rs12,108 | 0.68% |
| Rs1L | 2015-2020 | 1824 | +176.9% | 7.5% | Rs276,927 | 0.50% |
| Rs1L | 2021-2023 | 1420 | +1.9% | 22.9% | Rs101,859 | 0.48% |
| Rs1L | 2024-2026 | 1301 | +17.0% | 16.5% | Rs116,966 | 0.48% |
| Rs5L | 2015-2020 | 1824 | +177.2% | 7.5% | Rs1,385,800 | 0.50% |
| Rs5L | 2021-2023 | 1420 | +2.1% | 23.2% | Rs510,475 | 0.50% |
| Rs5L | 2024-2026 | 1301 | +16.5% | 17.6% | Rs582,517 | 0.50% |
