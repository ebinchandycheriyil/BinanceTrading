# Multi-symbol intraday research (daytrade/)

**Verdict: no strategy is ready for real money.** One family, `squeeze_oi` (short-squeeze breakout
confirmed by funding, open interest and retail positioning), stayed profitable at base costs in all three
periods, but its edge decayed (0.28R → 0.08R → 0.04R per trade) and it failed the holdout under 1.5x costs.
It is the only candidate worth a forward paper test.

## Why the 24-hour carry trial made no trades

The trial ran the spot/dated-futures carry bot, not a day-trading strategy. With 1,000 USDT, 40% per
basket and 100% futures collateral, every one of ~1,675 snapshots was rejected for `quantity_below_step`
or projected return below the 2 USDT / 10% APR hurdles. Two segments also stopped on DNS failures. It could
not have traded at this account size.

## Setup

- Data: Binance USD-M public archives, 5m klines + funding + 5m open-interest/long-short metrics,
  Nov 2021 – Sep 2026, 10 symbols (BTC ETH SOL XRP BNB DOGE ADA AVAX LINK LTC).
- Periods: development 2022-01 → 2024-07, validation 2024-07 → 2025-07, holdout 2025-07 → 2026-10.
  Holdout access is logged (`holdout_access.log`, one access).
- Costs: taker 5 bp, maker 2 bp per fill; taker slippage 1.5 bp (BTC/ETH) / 3 bp (alts) per side;
  historical funding. Stress = 1.5x fees and slippage.
- Fills: limit orders need a 1 bp trade-through; stop wins same-bar ties; stop gaps fill at the open;
  setups whose round-trip cost exceeds 30% of the stop distance are skipped.
- Portfolio: 0.5% equity risk per trade, ≤6 positions, ≤2x notional per position, ≤5x gross.
- Gates (frozen in each `protocol*.json` before results): dev ≥120 trades, PF ≥1.15, t ≥2, ≥2 of 3 years
  positive, positive return and positive 1.5x-stress return. Validation/holdout: ≥40 trades, PF ≥1.10,
  positive return and stress return.

## Results — 15 families, 364 development configurations

| Round | Family | Idea | Dev grid positive | Best dev t | Outcome |
|---|---|---|---:|---:|---|
| 1 | orb | Asia/London/NY opening-range breakout | 0/72 | -0.36 | rejected |
| 1 | sweep | prior-day high/low sweep and reclaim | 0/16 | -5.01 | rejected |
| 1 | vwap_fade | session-VWAP overextension fade, maker orders | 0/32 | -6.36 | rejected |
| 1 | pullback | 4h trend + 15m EMA pullback | 0/8 | -10.04 | rejected |
| 1 | squeeze | 1h Bollinger/Keltner release | 0/16 | -0.47 | rejected |
| 1 | weekend | CME weekend-gap fade | 0/24 | -1.57 | rejected |
| 1 | cascade | liquidation-candle fade | 0/16 | -8.16 | rejected |
| 1 | daymom | intraday time-series momentum | 0/24 | -0.65 | rejected |
| 2 | trend_trail | 1h Donchian + chandelier trail | 2/24 | 0.16 | rejected |
| 2 | nr_breakout | Crabel NR4/NR7 range expansion | 0/16 | -0.92 | rejected |
| 2 | rsi2_dip | RSI(2) dip in 1h/4h trend | 0/16 | -2.21 | rejected |
| 2 | hour_season | fixed UTC window drift | 0/16 | -1.25 | rejected |
| 2-3 | funding_squeeze | extreme funding + break against the crowd | 47/52 | 2.28 | **failed validation** (-7.6%) |
| 3 | flow_follow | trade with outsized taker-flow bars | 0/16 | -5.98 | rejected |
| 3 | squeeze_oi | funding + OI build-up + retail skew + breakout | 16/16 | 3.09 | **passed validation, failed holdout** |

### squeeze_oi (frozen: long-only, funding < 0, OI up over 24h, retail long/short ratio in bottom 40% of 30 days, 1h close > 6h high, 2 ATR stop, 5 ATR trail, 48h max)

| Period | Trades | Win | Avg R | PF | t | Return | Max DD | 1.5x-cost return |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Development (2.5 y) | 432 | 39.6% | 0.278 | 1.48 | 3.09 | +70.7% | 8.7% | +58.7% |
| Validation (1 y) | 190 | 36.8% | 0.083 | 1.14 | 0.70 | +8.8% | 8.5% | +3.2% |
| Holdout (15 mo) | 261 | 33.0% | 0.037 | 1.06 | 0.34 | +3.6% | 16.9% | -1.9% |

## What the evidence says

1. **Classic 5m–15m setups have about zero gross edge on these coins.** Losses are roughly the cost of trading.
   Fades (sweep, VWAP, cascade) lose even before costs: crypto intraday has momentum, not mean reversion.
2. **The only repeatable signal is positioning:** crowded shorts (negative funding, rising OI, retail
   skewed short) get squeezed on breakouts. The edge is strongest in 2022–2023 and is shrinking.
3. **Costs decide the outcome.** At 1.5x costs the holdout turns negative. A lower fee tier (BNB discount,
   VIP, maker entries) is a bigger lever than any parameter.

## Caveats

Ten coins surviving to 2026 imply survivorship bias. 5m OHLC cannot resolve intrabar order (stop-first is used).
Historical depth, partial fills and liquidation are not modeled. Validation is not perfectly clean for
`squeeze_oi`: its percentile filter was written after `funding_squeeze` failed validation. Many configs were
tried; t-stats are not corrected for multiple testing.

## Forward paper run (started 2026-10-02 03:19 UTC)

`python -m daytrade.live` runs the frozen `squeeze_oi` on live public data for 39 coins at once (the core 10 plus
the top USDT perpetuals by volume). Each cycle starts 25 seconds after a 5m bar closes. It reuses the backtest strategy and simulator.
A signal is frozen when first seen and fills at the open of the 5m bar in progress, so it can't fill retroactively.
Signals first seen more than 15 minutes late are logged as `LATE_SIGNAL` and not traded. The live ask at
first sight is recorded to measure fill drift. Checks: live OI/ratio/kline/funding data match the archive, and an hour-by-hour
live-style walk reproduced backtest trades exactly (SOL Aug-2025, XRP Oct-2025). Results are reported separately
for the core 10 and the extended coins.

```powershell
.\.venv\Scripts\python.exe -m daytrade.live report --run artifacts\daytrade\live_squeeze_oi
New-Item artifacts\daytrade\live_squeeze_oi\STOP     # graceful stop; delete it and start again to resume
.\.venv\Scripts\python.exe -m daytrade.live start --run artifacts\daytrade\live_squeeze_oi
```

While the laptop sleeps nothing is monitored. On resume, open positions replay the missed bars (as if exchange stop
orders were resting), and signals from the gap become `LATE_SIGNAL`. Expect about 1 trade per coin per 2 weeks; a
meaningful sample (100+ closed trades) needs weeks.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m daytrade.data            # klines + funding
.\.venv\Scripts\python.exe -m daytrade.data metrics    # open interest / long-short ratios
.\.venv\Scripts\python.exe -m daytrade.research --tag _r3b dev --families squeeze_oi
.\.venv\Scripts\python.exe -m daytrade.research --tag _r3b validate
# ad-hoc tweak on dev or validation only (holdout refused):
.\.venv\Scripts\python.exe -m daytrade.research run squeeze_oi ls_q=0.3 sides=both --period dev
```

Files: `protocol*.json` (frozen rules), `dev_grid*.csv` (every configuration), `selection_before_validation*.json`,
`validation*.json`, `holdout_r3b.json`, `holdout_access.log`.
