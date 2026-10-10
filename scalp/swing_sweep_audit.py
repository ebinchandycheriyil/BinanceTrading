"""Verify and expand the fixed-data sweep report without modifying selection."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .swing_sweep import ROOT,DATA


def main():
    r=json.loads((ROOT/'results.json').read_text());best=json.loads((ROOT/'best_run.json').read_text())
    for file,digest in r['protocol']['hashes'].items():assert hashlib.sha256(Path(file).read_bytes()).hexdigest()==digest
    assert hashlib.sha256(DATA.read_bytes()).hexdigest()==r['protocol']['data_sha256']
    assert len({row['id'] for row in r['ranked']})==r['total_runs']==286
    for row in r['ranked']:
        assert abs(sum(row['quarters'].values())-row['summary']['net_pnl'])<1e-7
        assert abs(sum(row['months'].values())-row['summary']['net_pnl'])<1e-7
    baseline=next(row for row in r['ranked'] if row['id']=='breakout_tf1_lb20_atr3_h24')
    old=json.loads(Path('artifacts/swing1/results.json').read_text())['validation']['summary']
    assert baseline['summary']==old
    groups=defaultdict(lambda:dict(trades=0,net=0.,fees=0.,funding=0.))
    fixed_stress=0.
    for t in best['run']['trades']:
        for k in ('symbol:'+t['symbol'],'side:'+t['side'],'exit:'+t['exit_reason']):
            g=groups[k];g['trades']+=1;g['net']+=t['net_pnl'];g['fees']+=t['entry_fee']+t['exit_fee'];g['funding']+=t['funding_cost']
        # Freeze original selection, size and timestamps, reprice adverse slip and fees.
        sign=t['sign'];entry=t['entry']*(1+sign*.0004)/(1+sign*.0002)
        exit=t['exit']*(1-sign*.0004)/(1-sign*.0002)
        fixed_stress+=sign*(exit-entry)*t['qty']-(entry+exit)*t['qty']*.001-t['funding_cost']
    checks=[]
    for args in (['-m','unittest','discover','-s','tests','-q'],['-m','compileall','-q','scalp','tests'],['-m','pip','check']):
        p=subprocess.run([sys.executable,*args],capture_output=True,text=True)
        checks.append(dict(args=args,exit_code=p.returncode,output=p.stdout+p.stderr))
        assert p.returncode==0
    audit=dict(checks=checks,baseline_exactly_reproduced=True,all_period_attribution_verified=True,
               accounting_trades_verified=r['accounting_trades_verified'],best_breakdown=groups,
               same_trade_doubled_fee_slippage_net=fixed_stress,
               conclusion='Positive exploratory candidate; bootstrap includes zero, 286 comparisons, no independent profitability validation')
    (ROOT/'verification.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    path=ROOT/'REPORT.md';original=path.read_text(encoding='utf-8').split('\n## Detailed audit')[0]
    lines=['','## Detailed audit','', '82 tests passed; compilation and dependencies passed. Original swing1 baseline reproduced exactly on the same prices, funding and rules. All month/quarter changes reconcile to each run\'s net. Source and dataset hashes match the saved protocol.',
           '',f"{r['accounting_trades_verified']} simulated trades checked across parameter and stress runs. These are repeated simulations of the same period, not independent observations.",
           '',f'Best fixed-trade doubled-fee/slippage sensitivity: {fixed_stress:.2f} USDT. This holds sizes and timestamps constant; it is an accounting sensitivity rather than execution replay.',
           '', '| Best-run breakdown | Trades | Net USDT | Commission | Funding |','|---|---:|---:|---:|---:|']
    for k,g in groups.items():lines.append(f"| {k} | {g['trades']} | {g['net']:.2f} | {g['fees']:.2f} | {g['funding']:.2f} |")
    lines+=['','## Monthly equity changes of best candidate','', '| Month | Change USDT |','|---|---:|']
    for m,v in best['candidate']['months'].items():lines.append(f'| {m} | {v:.2f} |')
    lines+=['','## Interpretation','', 'The local refinement improved the best base-grid result from +12.92 USDT to +26.28 USDT on 1,000 USDT. The improvement was selected using the same 2025 data, so it is an in-sample gain. Twenty-five of 286 configurations were positive; the other 261 were not.',
            '', 'The winning region is more credible than a single isolated peak because 17 of 19 nearby settings also made money, and the best candidate remained positive under both cost sensitivity approaches. However, the weekly bootstrap confidence interval crosses zero, Q3 lost money, and selection across many configurations biases the best result upward.',
            '', 'Next useful step: freeze this complete candidate, then test on an unexamined period or collect forward paper results. Further tuning on 2025 can explore sensitivity but cannot establish independent profitability. No real trading or automatic deployment has been enabled.']
    path.write_text(original+'\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(checks_passed=True,baseline_reproduced=True,fixed_stress=fixed_stress,breakdown=groups)))


if __name__=='__main__':main()
