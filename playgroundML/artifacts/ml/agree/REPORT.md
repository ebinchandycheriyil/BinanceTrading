# Do ML and volume breakout agree? (daytrade/ml_agree.py)

Historical signals rebuilt without look-ahead: 2021-22 with models trained before 2021, 2023-Sep 2026 with models
trained before 2023. "Agree" = ML signal on the same coin and side within 3h before the VB entry. BTC+ETH, BNB fees.
Per-trade numbers are net of all fees. Account return = Rs1L, 0.5% risk per trade.

| VB trades | 2021-22 trades | avg net/trade | win | 2023-26 trades | avg net/trade | win |
|---|---:|---:|---:|---:|---:|---:|
| all | 393 | +0.17% | 45% | 1096 | +0.12% | 43% |
| LGBM#1 agrees | 113 | **+0.38%** | 45% | 199 | **+0.29%** | 49% |
| LGBM#1 does not | 280 | +0.09% | 45% | 897 | +0.09% | 42% |
| LGBM#3 agrees | 68 | **+0.40%** | 47% | 82 | **+0.39%** | 50% |
| LGBM#3 does not | 325 | +0.13% | 45% | 1014 | +0.10% | 43% |
| RF agrees | 86 | +0.40% | 43% | 91 | **-0.17%** | 40% |
| 2+ ML agree | 79 | +0.56% | 46% | 100 | +0.25% | 49% |
| no ML agrees | 248 | +0.21% | 46% | 869 | +0.10% | 42% |

ML confirming ML (LGBM#1 signal also given by LGBM#3 and RF): 2021-22 +0.53%/trade vs +0.13% unconfirmed, but
2023-26 +0.01% vs +0.06% -> does not hold up.

Findings: VB trades the LightGBM models agree with earned 2-4x more per trade in both periods (consistent),
RF agreement was not consistent. Filtering to agreement only cuts trades ~80%, so total return falls
(VB all +55.2% vs VB+LGBM#1 +13.4% on 2023-26 at the same 0.5% risk). Use: size up agreed VB trades, test forward.
2023-26 test now used 4x (holdout_access.log).
