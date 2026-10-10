#!/bin/bash
# Quick health + results summary:  bash ~/trade/deploy/vps/status.sh
T=/home/opc/trade
echo "== services"; systemctl is-active trade-cycle.timer trade-recorder.service
echo "== memory"; free -m | head -2
echo "== last cycles"; tail -n 8 $T/logs/cycle.log
for r in artifacts/daytrade/live_squeeze_oi playground/artifacts/daytrade/live_volume_breakout playground/artifacts/daytrade/live_volume_breakout_alts playgroundML/artifacts/ml/live_rf playgroundML/artifacts/ml/live_lgbm1 playgroundML/artifacts/ml/live_lgbm3 playground/artifacts/daytrade/live_carry playground/artifacts/daytrade/live_donchian4h playground/artifacts/daytrade/live_vb4h; do
    [ -f $T/$r/status.json ] || continue
    echo "== $r"; $T/venv/bin/python - "$T/$r/status.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
keep = ('updated', 'hours_running', 'days', 'signals', 'open_positions', 'equity_inr', 'return_pct', 'holding', 'errors')
for k in keep:
    if k in s and s[k] not in ({}, []): print(f'  {k}: {s[k]}')
c = s.get('closed')
if c: print('  closed:', {k: c[k] for k in ('trades', 'win', 'sum_r', 'pf') if k in c})
if c and 'portfolio_pnl_usdt' in c: print(f"  return on 1,000 USDT paper account: {c['portfolio_pnl_usdt'] / 10:+.2f}%")
PY
done

echo; $T/venv/bin/python $T/deploy/vps/results.py
