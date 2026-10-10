# Evolutionary ML search – results

240 unique systems trained across 3 runs x 5 generations. Train 2015–2020, select on 2021–2022, final test 2023–Sep 2026 (once). Rs1L account, BNB fees, Binance minimums.

## Best fitness by generation

| Run | Gen 1 | Gen 2 | Gen 3 | Gen 4 | Gen 5 |
|---|---:|---:|---:|---:|---:|
| 11 | +0.093 | +0.586 | +0.586 | +0.586 | +0.586 |
| 22 | +0.134 | +0.291 | +0.291 | +0.326 | +0.584 |
| 33 | +0.112 | +0.121 | +0.215 | +0.389 | +0.397 |

## Finalists: validation (selected on) vs TEST (never seen)

| System | Model / rule | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 Jan–Sep | Test 2023–26 total (DD) | Test at 1.5x fees |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| run11_#1 | lgbm_rf {"leaves": 255, "rounds": 100, "min_leaf": 500, "ff": 0.8}, stop 1.0ATR, 2.0R, 12h, min 0.5%, top 10%, both, no_calendar | +72.3% | +62.7% | +0.1% | +35.9% | +14.7% | -5.4% | +47.6% (17%) | -6.9% |
| run11_#2 | lgbm_rf {"leaves": 255, "rounds": 300, "min_leaf": 100, "ff": 0.5}, stop 1.0ATR, 2.0R, 8h, min 0.5%, top 5%, both, all | +43.1% | +40.3% | +10.4% | +30.0% | +2.7% | -2.0% | +43.7% (11%) | +25.0% |
| run11_#3 | lgbm_rf {"leaves": 255, "rounds": 300, "min_leaf": 100, "ff": 0.5}, stop 1.5ATR, 2.0R, 8h, min 0.5%, top 5%, both, all | +23.1% | +24.2% | +1.2% | +9.7% | +7.2% | -3.4% | +16.0% (7%) | +8.7% |
| run22_#1 | lgbm_et {"leaves": 31, "lr": 0.03, "rounds": 600, "min_leaf": 300, "ff": 0.5}, stop 1.5ATR, 2.0R, 12h, min 0.3%, top 10%, both, no_calendar | +113.5% | +62.6% | +4.9% | +41.3% | +8.5% | +2.5% | +63.2% (15%) | +23.5% |
| run22_#2 | lgbm_et {"leaves": 31, "lr": 0.03, "rounds": 600, "min_leaf": 1000, "ff": 0.5}, stop 1.5ATR, 1.5R, 12h, min 0.5%, top 10%, both, no_calendar | +126.2% | +44.4% | +5.5% | +18.7% | +15.4% | -2.9% | +39.6% (17%) | -3.7% |
| run22_#3 | lgbm_et {"leaves": 31, "lr": 0.03, "rounds": 600, "min_leaf": 100, "ff": 0.8}, stop 1.5ATR, 2.0R, 12h, min 0.5%, top 5%, both, no_calendar | +71.6% | +42.8% | -5.8% | +16.0% | +23.5% | +9.5% | +48.2% (9%) | +31.1% |
| run33_#1 | lgbm {"leaves": 63, "lr": 0.01, "rounds": 600, "min_leaf": 1000, "ff": 0.8}, stop 1.5ATR, 1.5R, 12h, min 0.3%, top 10%, both, price_vol | +117.7% | +44.1% | -0.1% | +35.7% | +7.2% | +4.8% | +52.0% (12%) | +6.3% |
| run33_#2 | lgbm {"leaves": 15, "lr": 0.01, "rounds": 600, "min_leaf": 300, "ff": 0.8}, stop 1.5ATR, 1.5R, 12h, min 0.5%, top 10%, both, price_vol | +88.5% | +41.9% | -2.8% | +13.2% | +19.2% | +0.2% | +29.7% (12%) | -1.6% |
| run33_#3 | lgbm_rf {"leaves": 255, "rounds": 100, "min_leaf": 100, "ff": 0.5}, stop 1.5ATR, 1.5R, 8h, min 0.5%, top 10%, both, no_calendar | +33.8% | +33.2% | +4.4% | +21.6% | +7.3% | -4.9% | +28.7% (9%) | +6.3% |
| Volume breakout (rule, reference) | – | – | – | -0.8% | +30.5% | +7.1% | +13.3% | +55.2% (15%) | – |

## Which model families did best on validation

| Model | Systems tried | Median fitness | Best fitness |
|---|---:|---:|---:|
| lgbm | 42 | +0.004 | +0.397 |
| lgbm_et | 56 | +0.051 | +0.584 |
| lgbm_rf | 71 | +0.025 | +0.586 |
| logit | 38 | -0.167 | +0.085 |
| mlp | 33 | -0.101 | +0.215 |
