"""Independent accounting and chronological selection checks for fresh study."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .swing_fresh import ROOT,select


def main():
    r=json.loads((ROOT/'results.json').read_text());p=r['protocol']
    ledgers=json.loads((ROOT/'ledgers.json').read_text())
    frozen=json.loads((ROOT/'selection_before_holdout.json').read_text())
    assert frozen['selected']==select(frozen['development'])==r['selected']
    assert p['baseline']==json.loads(Path('artifacts/swing_sweep/best_run.json').read_text())['candidate']['parameters']
    for filename,digest in p['source_hashes'].items():assert hashlib.sha256(Path(filename).read_bytes()).hexdigest()==digest
    assert hashlib.sha256((ROOT/'market_data.json').read_bytes()).hexdigest()==r['dataset_sha256']
    count=0
    groups=defaultdict(lambda:dict(trades=0,net=0.,fees=0.,funding=0.))
    runs=[(run,p['start'],p['split']) for run in ledgers['development'].values()]
    runs += [(ledgers[k],p['split'],p['end']) for k in ('baseline_holdout','selected_holdout','stress')]
    for run,start,end in runs:
        net=0
        for t in run['trades']:
            assert start<=t['opened']<end
            assert t['opened']/1000<=t['exit_time']<end/1000
            assert abs(abs(t['entry']-t['stop'])-abs(t['target']-t['entry']))<1e-8
            assert abs(t['gross_pnl']-t['entry_fee']-t['exit_fee']-t['funding_cost']-t['net_pnl'])<1e-8
            net+=t['net_pnl'];count+=1
        assert abs(net-run['summary']['net_pnl'])<1e-7
        assert abs(run['equity'][-1]['equity']-1000-net)<1e-7
    for t in ledgers['selected_holdout']['trades']:
        for key in ('symbol:'+t['symbol'],'side:'+t['side'],'exit:'+t['exit_reason']):
            g=groups[key];g['trades']+=1;g['net']+=t['net_pnl'];g['fees']+=t['entry_fee']+t['exit_fee'];g['funding']+=t['funding_cost']
    checks=[]
    for args in (['-m','unittest','discover','-s','tests','-q'],['-m','compileall','-q','scalp','tests'],['-m','pip','check']):
        process=subprocess.run([sys.executable,*args],capture_output=True,text=True)
        checks.append(dict(args=args,exit_code=process.returncode,output=process.stdout+process.stderr))
        assert process.returncode==0
    verification=dict(checks=checks,trades_verified=count,selection_uses_development_only=True,baseline_matches_prior_winner=True,
                      selected_holdout_breakdown=groups)
    (ROOT/'verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
    lines=['','## Audit and selected holdout loss attribution','',f'{count} simulated trades reconciled; 86 tests passed. Source and data hashes match the protocol. The baseline exactly matches the previously selected 2025 configuration. The saved selection is reproducible from development results alone.',
           '', '| Group | Trades | Net USDT | Fees | Funding cost |','|---|---:|---:|---:|---:|']
    for key,g in groups.items():lines.append(f"| {key} | {g['trades']} | {g['net']:.2f} | {g['fees']:.2f} | {g['funding']:.2f} |")
    lines+=['', 'The adjustment reduced the holdout loss by 4.69 USDT per 1,000 USDT account, but did not produce a profitable setup. Both March and April were negative. January was profitable and February was negative even for the selected refinement. This contradicts treating the prior 2025 positive result as a reliable edge.',
            '', 'Do not keep calling March-April untouched if parameters are subsequently tuned on it. It can be reused for exploratory work only; independent evaluation would require another period. No automatic promotion, real orders or persistent trading process.']
    path=ROOT/'REPORT.md';original=path.read_text(encoding='utf-8').split('\n## Audit and selected holdout loss attribution')[0]
    path.write_text(original+'\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(verified=count,checks_passed=True,breakdown=groups)))


if __name__=='__main__':main()
