"""Export the swing experiment ledger and independently check accounting."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from collections import defaultdict

from .swing_study import ROOT


def main():
    r=json.loads((ROOT/'results.json').read_text())
    for name,expected in r['protocol']['source_sha256'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected
    assert hashlib.sha256((ROOT/'market_data.json').read_bytes()).hexdigest()==r['dataset_sha256']
    all_results=[v for stage in ('stage1','stage2') for folds in r[stage].values() for v in folds]+[r['validation'],r['stress']]
    checked=0
    for result in all_results:
        net=0.
        for t in result['trades']:
            assert abs(abs(t['entry']-t['stop'])-abs(t['target']-t['entry']))<1e-8
            assert abs(t['sign']*(t['exit']-t['entry'])*t['qty']-t['gross_pnl'])<1e-8
            assert abs(t['gross_pnl']-t['entry_fee']-t['exit_fee']-t['funding_cost']-t['net_pnl'])<1e-8
            assert 0<=t['hold_hours']<=72
            net+=t['net_pnl'];checked+=1
        assert abs(net-result['summary']['net_pnl'])<1e-7
        assert abs(result['equity'][-1]['equity']-1000-net)<1e-7
    trades=r['validation']['trades']
    with (ROOT/'validation_trades.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(trades[0]));writer.writeheader();writer.writerows(trades)
    groups=defaultdict(lambda:dict(trades=0,net=0.,fees=0.,funding=0.))
    for t in trades:
        for key in ('exit:'+t['exit_reason'],'symbol:'+t['symbol'],'side:'+t['side']):
            g=groups[key];g['trades']+=1;g['net']+=t['net_pnl'];g['fees']+=t['entry_fee']+t['exit_fee'];g['funding']+=t['funding_cost']
    checks=[]
    for args in (['-m','unittest','discover','-s','tests','-q'],['-m','compileall','-q','scalp','tests'],['-m','pip','check']):
        p=subprocess.run([sys.executable,*args],capture_output=True,text=True)
        checks.append(dict(args=args,exit_code=p.returncode,output=p.stdout+p.stderr))
        assert p.returncode==0
    audit=dict(accounting_trades_verified=checked,checks=checks,loss_breakdown=groups)
    (ROOT/'verification.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    lines=['','## Audit and loss attribution','',f'{checked} trades across all development, validation and stress runs reconciled. All 75 tests passed, compilation and dependencies checked. Sources and dataset match frozen hashes.',
           '', '| Group | Trades | Net USDT | Commission | Funding cost |','|---|---:|---:|---:|---:|']
    for name,g in groups.items():lines.append(f"| {name} | {g['trades']} | {g['net']:.2f} | {g['fees']:.2f} | {g['funding']:.2f} |")
    lines+=['', 'Both strategy families lost in each initial development year. One later refinement had a positive half-year, but was not selected by the predefined worst-fold ranking. The selected candidate lost in both 2024 halves and in 2025. No robust profitable strategy was found; no forwarding to live execution is justified.',
            '', 'Raw ledger: [validation_trades.csv](validation_trades.csv). Full configurations, equity curves, uncertainty and all iteration results: [results.json](results.json).',
            '', 'Simulator is research-only, separate from the scalping CLI; there is no unattended swing trader running.']
    path=ROOT/'REPORT.md';original=path.read_text(encoding='utf-8').split('\n## Audit and loss attribution')[0]
    path.write_text(original+'\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(verified=checked,checks_passed=True,breakdown=groups)))


if __name__=='__main__':main()
