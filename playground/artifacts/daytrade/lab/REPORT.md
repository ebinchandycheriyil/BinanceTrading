# Indicator lab results

All trades: market entry at the next 5m bar, 12h max hold, Binance regular fees + slippage + funding. Returns use 0.5% equity risk per trade on 1,000 USDT. Cells: trades · return · profit factor · return at 1.5x costs.

| Indicator | Best setting (picked on dev) | Win rate (dev) | Development 2022–24, 10 coins | Validation 2024–25, 10 coins | Unseen 29 coins 2022–26 | Verdict |
|---|---|---:|---|---|---|---|
| volume_breakout | 20-bar high, vol>3x, 1h, trail, filter ema200 | 42% | 3126 · +34.2% · PF 1.085 · stress -16.3% | 1165 · +32.3% · PF 1.184 · stress +10.4% | 8968 · -0.8% · PF 1.058 · stress -56.7% | **PROMISING** |
| bb_breakout | 20,2.5, 1h, trail, filter ema200 | 43% | 4385 · +5.0% · PF 1.067 · stress -47.2% | 1616 · +6.7% · PF 1.038 · stress -16.4% | 13372 · -7.8% · PF 1.036 · stress -87.5% | **PROMISING** |
| squeeze_day (12h) | frozen earlier | 47% | 513 · +34.6% · PF 1.326 · stress +24.3% | 214 · +3.5% · PF 1.08 · stress -1.7% | 2256 · -9.8% · PF 0.997 · stress -34.6% | **PROMISING** |
| squeeze_oi (48h) | frozen earlier | 40% | 432 · +70.7% · PF 1.477 · stress +58.7% | 190 · +8.8% · PF 1.141 · stress +3.1% | 1991 · -15.7% · PF 1.005 · stress -35.8% | **PROMISING** |
| rsi_reversal | 14 30/70, 1h, trail, filter ema200 | 42% | 378 · -13.6% · PF 0.852 · stress -19.8% | 142 · -10.0% · PF 0.661 · stress -13.3% | 1290 · -23.9% · PF 0.89 · stress -35.6% | **FAILS** |
| bb_reversal | 20,2.5, 1h, trail, filter ema200 | 45% | 1337 · -28.5% · PF 0.881 · stress -45.6% | 435 · -14.2% · PF 0.836 · stress -20.7% | 3948 · -41.2% · PF 0.939 · stress -63.7% | **FAILS** |
| mfi | 14 20/80, 1h, trail, filter ema200 | 45% | 1100 · -17.1% · PF 0.958 · stress -34.1% | 505 · -31.6% · PF 0.666 · stress -36.5% | 3877 · -55.7% · PF 0.889 · stress -73.1% | **FAILS** |
| ema_cross | 20/50, 1h, trail, filter ema200 | 44% | 2419 · +1.6% · PF 1.024 · stress -32.8% | 951 · -14.2% · PF 0.913 · stress -29.5% | 8556 · -56.5% · PF 0.962 · stress -86.0% | **FAILS** |
| supertrend | 10,3, 1h, trail, filter ema200 | 41% | 2668 · -18.3% · PF 0.98 · stress -45.3% | 1099 · -42.1% · PF 0.801 · stress -54.4% | 9457 · -58.6% · PF 0.93 · stress -88.1% | **FAILS** |
| pivots | S1/R1 bounce, 15min, trail, filter ema200 | 37% | 659 · -5.9% · PF 0.978 · stress -18.1% | 313 · -13.6% · PF 0.814 · stress -10.0% | 2616 · -68.8% · PF 0.826 · stress -83.1% | **FAILS** |
| adx_dmi | ADX>20, 1h, trail, filter ema200 | 41% | 3007 · -46.3% · PF 0.926 · stress -66.6% | 1109 · -31.9% · PF 0.851 · stress -42.7% | 9504 · -73.2% · PF 0.92 · stress -92.8% | **FAILS** |
| cci | +-200, 1h, trail, filter ema200 | 45% | 1808 · -28.6% · PF 0.921 · stress -51.8% | 606 · -18.1% · PF 0.84 · stress -26.6% | 5497 · -78.8% · PF 0.873 · stress -89.2% | **FAILS** |
| ichimoku | 7/22/44, 1h, trail, filter ema200 | 42% | 3873 · +31.5% · PF 1.082 · stress -31.0% | 1440 · -25.4% · PF 0.919 · stress -39.9% | 12637 · -79.5% · PF 0.967 · stress -94.7% | **FAILS** |
| psar | 0.01/0.1, 1h, trail, filter ema200 | 42% | 5105 · -23.8% · PF 0.997 · stress -69.8% | 2276 · -34.8% · PF 0.923 · stress -52.9% | 18810 · -87.3% · PF 0.944 · stress -98.2% | **FAILS** |
| heikin_ashi | flip after 3, 1h, trail, filter ema200 | 40% | 3358 · -61.1% · PF 0.877 · stress -79.5% | 1303 · -27.0% · PF 0.906 · stress -43.4% | 9924 · -89.6% · PF 0.893 · stress -97.4% | **FAILS** |
| stochastic | 5,3,3 <20, 1h, trail, filter ema200 | 44% | 2057 · -43.1% · PF 0.871 · stress -58.8% | 1116 · -29.0% · PF 0.835 · stress -41.1% | 8767 · -90.8% · PF 0.827 · stress -96.5% | **FAILS** |
| keltner_breakout | EMA20+2ATR, 1h, trail, filter ema200 | 41% | 4908 · -29.2% · PF 1.003 · stress -66.7% | 2036 · +22.2% · PF 1.094 · stress -13.1% | 16481 · -92.0% · PF 0.965 · stress -98.5% | **FAILS** |
| donchian | 55, 1h, trail, filter ema200 | 40% | 4314 · -67.4% · PF 0.898 · stress -83.2% | 2011 · -33.2% · PF 0.954 · stress -52.4% | 15979 · -93.4% · PF 0.94 · stress -98.8% | **FAILS** |
| macd | 12/26/9, 1h, trail, filter ema200 | 43% | 3010 · -33.1% · PF 0.947 · stress -60.8% | 1264 · -54.8% · PF 0.711 · stress -64.6% | 10447 · -94.4% · PF 0.855 · stress -98.5% | **FAILS** |
| rsi_momentum | 14 cross 55/45, 1h, trail, filter ema200 | 40% | 9242 · -79.5% · PF 0.947 · stress -95.2% | 3583 · -62.7% · PF 0.867 · stress -79.1% | 30978 · -99.5% · PF 0.899 · stress -100.0% | **FAILS** |
| vwap_cross | 2-bar confirm, 1h, trail, filter ema200 | 40% | 10544 · -88.4% · PF 0.888 · stress -98.2% | 4105 · -68.6% · PF 0.823 · stress -84.8% | 35223 · -99.8% · PF 0.87 · stress -100.0% | **FAILS** |

## Which settings help (stage 1: all 304 configs, development period)

| Setting | Value | Configs | Share profitable | Median avg R per trade | Median trades |
|---|---|---:|---:|---:|---:|
| tf | 15min | 152 | 0% | -0.139 | 16392 |
| tf | 1h | 152 | 7% | -0.054 | 5191 |
| exit | target2R | 152 | 3% | -0.122 | 9454 |
| exit | trail | 152 | 5% | -0.100 | 9409 |
| filt | ema200 | 152 | 6% | -0.097 | 6469 |
| filt | none | 152 | 1% | -0.122 | 13100 |
| var | 0 | 152 | 1% | -0.111 | 9709 |
| var | 1 | 152 | 6% | -0.112 | 8110 |

## Per indicator, all 16 configs (development)

| Indicator | Profitable configs | Best return | Median return |
|---|---:|---:|---:|
| adx_dmi | 0/16 | -33.8% | -89.7% |
| bb_breakout | 1/16 | +5.0% | -97.4% |
| bb_reversal | 0/16 | -28.5% | -97.5% |
| cci | 0/16 | -28.6% | -98.5% |
| donchian | 0/16 | -67.4% | -99.3% |
| ema_cross | 1/16 | +1.6% | -92.9% |
| heikin_ashi | 0/16 | -61.1% | -96.6% |
| ichimoku | 3/16 | +31.7% | -68.3% |
| keltner_breakout | 0/16 | -29.2% | -96.2% |
| macd | 0/16 | -33.1% | -99.6% |
| mfi | 2/16 | +1.9% | -66.9% |
| pivots | 0/16 | -5.9% | -86.3% |
| psar | 0/16 | -23.8% | -99.6% |
| rsi_momentum | 0/16 | -79.5% | -99.7% |
| rsi_reversal | 0/16 | -13.6% | -91.0% |
| stochastic | 0/16 | -43.1% | -99.5% |
| supertrend | 0/16 | -18.3% | -98.0% |
| volume_breakout | 4/16 | +45.4% | -88.1% |
| vwap_cross | 0/16 | -88.4% | -99.7% |

## High win rate is not profit

| Config | Win rate | Avg R | Return |
|---|---:|---:|---:|
| mfi 14 10/90 1h trail ema200 | 51% | +0.047 | +1.9% |
| mfi 14 10/90 1h target2R ema200 | 50% | +0.031 | +1.1% |
| cci +-200 1h trail ema200 | 45% | -0.031 | -28.6% |
| pivots S1/R1 bounce 1h trail none | 45% | -0.052 | -86.1% |
| mfi 14 20/80 1h trail ema200 | 45% | -0.017 | -17.1% |

Stage 1 profitable configs: 11 / 304.

Full data: `stage1.csv`, `stage2.json`, `selection.json`, `protocol.json`.
