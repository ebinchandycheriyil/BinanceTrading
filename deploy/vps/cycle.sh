#!/bin/bash
# One 5-minute cycle of every paper bot, run one after another so only one Python process is in memory at a time
# (fits a 1 GB server). Each bot resumes from its run folder, does one cycle (--once) and exits.
# Started by trade-cycle.timer 25 s after every 5m candle closes.
T=/home/opc/trade
PY=$T/venv/bin/python
LOG=$T/logs; mkdir -p $LOG
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MALLOC_ARENA_MAX=2

bot() {   # name  workdir  args...
    local name=$1 wd=$2; shift 2
    local t0=$(date +%s)
    (cd "$wd" && timeout 240 "$PY" "$@") >>"$LOG/$name.log" 2>&1
    local rc=$?
    echo "$(date -u '+%F %T') $name rc=$rc $(( $(date +%s) - t0 ))s" >>"$LOG/cycle.log"
}

VB=$T/playground/artifacts/daytrade/live_volume_breakout
RF=$T/playgroundML/artifacts/ml/live_rf
SQ=$T/artifacts/daytrade/live_squeeze_oi
CA=$T/playground/artifacts/daytrade/live_carry
L1=$T/playgroundML/artifacts/ml/live_lgbm1
L3=$T/playgroundML/artifacts/ml/live_lgbm3
VA=$T/playground/artifacts/daytrade/live_volume_breakout_alts
DC=$T/playground/artifacts/daytrade/live_donchian4h
V4=$T/playground/artifacts/daytrade/live_vb4h
# fastest, timing-sensitive bots first (fills are priced at the open of the 5m bar in progress)
bot volume_breakout $T/playground   -m daytrade.live start --once --run $VB --strategy lab --params @$VB/params.json --symbols BTCUSDT ETHUSDT
bot donchian4h      $T/playground   -m daytrade.live start --once --run $DC --strategy trend --params @$DC/params.json --symbols BTCUSDT ETHUSDT --risk 0.0075
bot vb4h            $T/playground   -m daytrade.live start --once --run $V4 --strategy trend --params @$V4/params.json --symbols BTCUSDT ETHUSDT --risk 0.0075
bot ml_rf           $T/playgroundML -m daytrade.live start --once --run $RF --strategy ml_rf --params @$RF/params.json --symbols BTCUSDT ETHUSDT
bot ml_lgbm_1      $T/playgroundML -m daytrade.live start --once --run $L1 --strategy ml_lgbm --params @$L1/params.json --symbols BTCUSDT ETHUSDT
bot ml_lgbm_3      $T/playgroundML -m daytrade.live start --once --run $L3 --strategy ml_lgbm --params @$L3/params.json --symbols BTCUSDT ETHUSDT
bot vb_alts        $T/playground   -m daytrade.live start --once --run $VA --strategy lab --params @$VA/params.json --symbols SOLUSDT XRPUSDT BNBUSDT DOGEUSDT ADAUSDT AVAXUSDT LINKUSDT LTCUSDT
bot squeeze_oi      $T              -m daytrade.live start --once --run $SQ
bot carry           $T/playground   -m daytrade.carry_live start --once --run $CA
