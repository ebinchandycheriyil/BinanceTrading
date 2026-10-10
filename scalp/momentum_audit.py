"""Verify experiment trades and export a raw ledger."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .momentum_study import ROOT


def main():
    result=json.loads((ROOT/'results.json').read_text())
    rows=[]
    for method, r in result['validation'].items():
        for t in r['trades']:
            assert abs(abs(t['target']-t['entry'])-abs(t['stop']-t['entry'])) < 1e-8
            assert abs(t['gross_pnl']-t['entry_fee']-t['exit_fee']-t['funding_cost']-t['net_pnl']) < 1e-8
            assert t['hold_minutes'] <= 30
            rows.append(dict(method=method,**t))
        assert abs(sum(t['net_pnl'] for t in r['trades'])-r['summary']['net_pnl']) < 1e-8
    with (ROOT/'validation_trades.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['method',*result['validation']['momentum5']['trades'][0].keys()])
        writer.writeheader(); writer.writerows(rows)
    checks=[]
    for args in (['-m','unittest','discover','-s','tests','-q'],['-m','compileall','-q','scalp','tests'],['-m','pip','check']):
        r=subprocess.run([sys.executable,*args],capture_output=True,text=True)
        checks.append(dict(args=args,exit_code=r.returncode,output=r.stdout+r.stderr))
        assert r.returncode==0
    prior=json.loads((ROOT/'results_before_report_correction.json').read_text())
    for field in ('development','validation','diagnostics','selected','passed'):
        assert result[field]==prior[field]
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('scalp').glob('*.py')}
    (ROOT/'verification.json').write_text(json.dumps(dict(checks=checks,hashes=hashes,
        validation_trades_verified=len(rows),ratio='1:1 executable price distances',
        report_correction='Binary win-rate illustration corrected from 72.5% to 62.5%; prior source/protocol/results archived. All trades and statistical results unchanged.'),indent=2),encoding='utf-8')
    print(json.dumps(dict(trades_verified=len(rows),checks_passed=True,results_unchanged=True)))


if __name__=='__main__':
    main()
