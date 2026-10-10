# Evolutionary ML search – results

239 unique systems trained across 3 runs x 5 generations. Train 2015–2020, select on 2021–2022, final test 2023–Sep 2026 (once). Rs1L account, BNB fees, Binance minimums.

## Best fitness by generation

| Run | Gen 1 | Gen 2 | Gen 3 | Gen 4 | Gen 5 |
|---|---:|---:|---:|---:|---:|
| 11 | +0.123 | +0.185 | +0.244 | +0.245 | +0.332 |
| 22 | +0.104 | +0.141 | +0.174 | +0.174 | +0.283 |
| 33 | +0.149 | +0.210 | +0.277 | +0.459 | +0.459 |

## Finalists: validation (selected on) vs TEST (never seen)

| System | Model / rule | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 Jan–Sep | Test 2023–26 total (DD) | Test at 1.5x fees |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| run11_#1 | lgbm {"leaves": 15, "lr": 0.1, "rounds": 600, "min_leaf": 1000, "ff": 0.5}, stop 2.0ATR, 1.0R, 8h, min 0.2%, top 10%, both, no_calendar | +40.3% | +37.1% | -4.5% | +43.2% | -6.0% | -12.4% | +11.1% (24%) | -20.8% |
| run11_#2 | lgbm {"leaves": 15, "lr": 0.03, "rounds": 300, "min_leaf": 100, "ff": 0.5}, stop 1.5ATR, 1.5R, 8h, min 0.2%, top 5%, long, no_calendar | +30.9% | +31.2% | +6.0% | +10.1% | +4.9% | -2.9% | +18.1% (8%) | +7.9% |
| run11_#3 | sk_et {"trees": 400, "depth": 10, "leaf": 200, "maxf": "0.5"}, stop 1.5ATR, 2.0R, 12h, min 0.2%, top 5%, both, no_calendar | +32.3% | +27.1% | +0.1% | +3.1% | +15.4% | +6.7% | +27.0% (9%) | +15.9% |
| run22_#1 | sk_rf {"trees": 200, "depth": 0, "leaf": 200, "maxf": "0.5"}, stop 2.0ATR, 1.5R, 12h, min 0.3%, top 10%, both, no_calendar | +36.8% | +33.1% | -1.9% | +20.0% | +18.7% | +5.9% | +48.4% (7%) | +29.9% |
| run22_#2 | lgbm {"leaves": 63, "lr": 0.1, "rounds": 100, "min_leaf": 1000, "ff": 0.5}, stop 1.5ATR, 1.5R, 12h, min 0.5%, top 10%, short, all | +36.5% | +25.2% | +4.1% | +10.5% | +11.9% | -1.5% | +27.0% (12%) | +0.1% |
| run22_#3 | lgbm {"leaves": 63, "lr": 0.01, "rounds": 100, "min_leaf": 1000, "ff": 0.5}, stop 1.0ATR, 1.5R, 12h, min 0.5%, top 5%, both, all | +51.6% | +23.9% | +5.6% | +19.2% | +1.3% | -5.4% | +21.9% (15%) | -9.6% |
| run33_#1 | sk_hgb {"iters": 200, "lr": 0.1, "leaves": 31, "l2": 1.0}, stop 1.0ATR, 2.0R, 8h, min 0.5%, top 5%, both, no_calendar | +76.5% | +51.8% | -5.3% | +4.1% | -10.0% | -12.7% | -20.1% (31%) | -53.0% |
| run33_#2 | sk_rf {"trees": 200, "depth": 0, "leaf": 50, "maxf": "sqrt"}, stop 1.5ATR, 2.0R, 12h, min 0.3%, top 5%, both, all | +68.3% | +39.5% | -5.9% | +9.2% | +22.9% | +3.6% | +31.5% (8%) | +17.5% |
| run33_#3 | lgbm_et {"leaves": 31, "lr": 0.1, "rounds": 600, "min_leaf": 1000, "ff": 0.5}, stop 1.5ATR, 2.0R, 8h, min 0.2%, top 5%, both, price_vol | +36.1% | +37.2% | +3.7% | +11.4% | +0.2% | -2.0% | +14.7% (14%) | -6.3% |
| Volume breakout (rule, reference) | – | – | – | -0.8% | +30.5% | +7.1% | +13.3% | +55.2% (15%) | – |

## Which model families did best on validation

| Model | Systems tried | Median fitness | Best fitness |
|---|---:|---:|---:|
| lgbm | 61 | +0.029 | +0.332 |
| lgbm_et | 27 | -0.022 | +0.332 |
| lgbm_rf | 9 | -0.129 | +0.081 |
| logit | 18 | -0.153 | +0.089 |
| mlp | 32 | -0.052 | +0.244 |
| sk_et | 27 | +0.012 | +0.250 |
| sk_hgb | 31 | -0.021 | +0.459 |
| sk_rf | 34 | +0.086 | +0.367 |
