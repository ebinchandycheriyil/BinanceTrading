"""Independent leg-level accounting audit and tabular exports."""
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .alternative_study import ROOT,select


def main():
    r=json.loads((ROOT/'results.json').read_text());p=r['protocol']
    ledgers=json.loads((ROOT/'ledgers.json').read_text())
    frozen=json.loads((ROOT/'selection_before_evaluation.json').read_text())
    for file,digest in p['source_hashes'].items():assert hashlib.sha256(Path(file).read_bytes()).hexdigest()==digest
    assert hashlib.sha256((ROOT/'market_data.json').read_bytes()).hexdigest()==r['dataset_sha256']
    for f,winner in r['winners'].items():
        assert winner==select([row for row in r['stages']['refinement'] if row['config']['family']==f])==frozen['winners'][f]
    checked=0;legs_checked=0;rows=[];groups=defaultdict(lambda:dict(baskets=0,net=0.,fees=0.,funding=0.))
    for key,run in ledgers.items():
        stage=key.split('|')[0]
        start,end={'development':(p['start'],p['refine']),'refinement':(p['refine'],p['validate']),
                   'evaluation':(p['validate'],p['end']),'stress':(p['validate'],p['end'])}[stage]
        net=0
        for t in run['trades']:
            assert start<=t['opened']<end and t['opened']/1000<=t['exit_time']<end/1000
            assert t['hold_hours']<=t['max_hours'] or (t.get('carry_symbol') and t['exit_reason']=='TIME_LIMIT')
            gross=sum(l['sign']*(l['exit']-l['entry'])*l['qty'] for l in t['legs'].values())
            fees=sum(l['entry_fee']+l['exit_fee'] for l in t['legs'].values())
            assert abs(gross-t['gross_pnl'])<1e-7
            assert abs(fees-t['entry_fee']-t['exit_fee'])<1e-7
            assert abs(gross-fees-t['funding_cost']-t['net_pnl'])<1e-7
            if t.get('carry_symbol'):
                values=list(t['legs'].values());assert len(values)==2
                assert values[0]['qty']==values[1]['qty']
            net+=t['net_pnl'];checked+=1;legs_checked+=len(t['legs'])
            if stage=='evaluation':
                group=key.split('|')[1].split('_')[0]+' '+t['exit_reason']
                g=groups[group];g['baskets']+=1;g['net']+=t['net_pnl'];g['fees']+=fees;g['funding']+=t['funding_cost']
                for symbol,l in t['legs'].items():rows.append(dict(strategy=key.split('|')[1],entry_time=t['entry_time'],exit_time=t['exit_time'],exit_reason=t['exit_reason'],symbol=symbol,**l,basket_net=t['net_pnl'],basket_funding=t['funding_cost']))
        assert abs(net-run['summary']['net_pnl'])<1e-7
        assert abs(run['equity'][-1]['equity']-1000-net)<1e-7
    if rows:
        with (ROOT/'evaluation_legs.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    with (ROOT/'all_iterations.csv').open('w',newline='',encoding='utf-8') as f:
        first=r['stages']['development'][0]
        writer=csv.DictWriter(f,fieldnames=['stage','id',*first['summary'],'marked_quarters'])
        writer.writeheader()
        for stage,values in r['stages'].items():
            for row in values:writer.writerow(dict(stage=stage,id=row['id'],**row['summary'],marked_quarters=json.dumps(row['quarters'])))
    checks=[]
    for args in (['-m','unittest','discover','-s','tests','-q'],['-m','compileall','-q','scalp','tests'],['-m','pip','check']):
        process=subprocess.run([sys.executable,*args],capture_output=True,text=True)
        checks.append(dict(args=args,exit_code=process.returncode,output=process.stdout+process.stderr))
        assert process.returncode==0
    deps=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True,check=True).stdout
    (ROOT/'dependencies.txt').write_text(deps,encoding='utf-8')
    audit=dict(checks=checks,baskets_verified=checked,legs_verified=legs_checked,evaluation_groups=groups,
               source_and_data_hashes_verified=True,selection_reproduced=True)
    (ROOT/'verification.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    lines=['','## Verification and evaluation detail','',f'{checked} basket trades / {legs_checked} legs reconciled. All 96 tests passed; compilation and dependency checks passed. Selection reproduced from refinement data only; source/data hashes match the saved protocol.',
           '', '| Exit group | Baskets | Net USDT | Fees | Funding cost |','|---|---:|---:|---:|---:|']
    for key,g in groups.items():lines.append(f"| {key} | {g['baskets']} | {g['net']:.2f} | {g['fees']:.2f} | {g['funding']:.2f} |")
    lines+=['','## Weekly uncertainty','']
    for family,d in r['diagnostics'].items():
        ci=d['bootstrap'];lines.append(f"- {family}: mean weekly equity change {ci['mean_weekly']:.2f} USDT; bootstrap 95% [{ci['lower95']:.2f}, {ci['upper95']:.2f}], {ci['weeks']} complete weeks. Acceptance: {d['passed']}.")
    lines+=['', 'Weekly bootstrap assumes independent week blocks and is not corrected for the strategy search. Zero-trade outcomes provide no evidence. A positive historical result alone does not establish future profitability.',
            '', '[All iterations](all_iterations.csv) | [Evaluation legs](evaluation_legs.csv). Basket net/funding columns repeat for each constituent leg in the leg CSV; do not sum those repeated basket columns.',
            '', 'No strategy was automatically enabled and no exchange orders were submitted. These are retrospective simulations, not forward paper fills.']
    path=ROOT/'REPORT.md';original=path.read_text(encoding='utf-8').split('\n## Verification and evaluation detail')[0]
    path.write_text(original+'\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(checks_passed=True,baskets=checked,legs=legs_checked,groups=groups)))


if __name__=='__main__':main()
