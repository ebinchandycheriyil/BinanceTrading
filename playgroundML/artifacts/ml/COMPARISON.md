# ML comparison on the same unseen test: 2023-01 → 2026-09 (BTC + ETH, ≤12h day trades)

All systems: trained on 2015–2020 (deep nets: fit 2015–2019, early stop 2020), selected on 2021–2022, retrained
up to 2022 and traded on 2023–Sep 2026 once. Rs1L account, BNB-discount Binance fees, slippage, exchange minimums.

| Approach | Systems trained | Finalists | Profitable on test | Best test return (DD, 1.5x fees) | Median finalist |
|---|---:|---:|---:|---|---:|
| Search 1 – Windows: LightGBM gbdt/rf/et, logistic, MLP | 240 | 9 | 9/9 | +63.2% (15%, +23.5%) – LightGBM extra-trees | +43.7% |
| Search 2 – WSL: + scikit-learn RandomForest / ExtraTrees / HistGB | ~240 | 9 | 8/9 | +48.4% (**7%**, **+29.9%**) – scikit-learn RandomForest | +21.9% |
| Deep learning – RTX 5050: CNN / GRU / Transformer (41k–1.5M params), 3-seed ensembles | 24 + 18 | 2 | 2/2 | +40.1% (10%, +20.1%) – Transformer 1.24M params | +40.0% |
| **Rule: volume breakout (no ML)** | – | – | – | **+55.2% (15%)** | – |

Deep finalists by year: 2023 -3.7% / -3.0%, 2024 +18.8% / +17.8%, 2025 +22.4% / +24.1%, 2026 Jan–Sep -0.8% / -0.9%.

## Findings
1. ML finds a real but modest edge: 19 of 20 finalists across three independent studies were profitable on years
   they never saw, and most survive 1.5x fees. Random entries lose 40–50% a year.
2. No ML approach clearly beats the simple volume-breakout rule on total return; the better ML systems match it with
   about half the drawdown (scikit-learn RandomForest 7%, Transformer ensembles 8–10%, vs 15%).
3. Validation scores overstate everything (2021–22 returns of +24% to +126% a year became roughly +5–18% a year on
   test). The single highest validation score (HistGradientBoosting) was the only finalist that lost on test (-20%).
4. Bigger deep nets did not help: 1–1.5M-parameter GRUs were the worst in 2022; 18 of 24 nets made money in 2021 and
   lost in 2022. Seed ensembles were essential – single-seed winners shrank when re-trained with other seeds.
5. 2026 so far is flat-to-negative for almost every ML system; volume breakout is +13%.

The 2023–2026 test has now been used by three searches. Treat these numbers as a ranking, and judge any candidate
on forward paper trading. Access log: `holdout_access.log`.
