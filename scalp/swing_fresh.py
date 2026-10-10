"""Fresh 2026 test with small Jan-Feb refinements and frozen Mar-Apr evaluation."""
import argparse
import csv
from dataclasses import asdict,replace
import hashlib
import itertools
import json
from pathlib import Path

from . import swing_study
from .experiments import stamp
from .signals import Candle
from .swing import HOUR,features,simulate
from .swing_sweep import SweepConfig,chart_features,name,period_changes

ROOT=Path('artifacts/swing_fresh')
BASE=dict(method='breakout',timeframe=2,lookback=30,atr_multiple=3.75,max_hours=54)


def candidates():
    return [dict(BASE,lookback=l,atr_multiple=a,max_hours=h)
            for l,a,h in itertools.product((28,30,32),(3.5,3.75,4),(48,54,60))]


def save(file,value):
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/file).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def protocol():
    return dict(start=stamp('2026-01-01'),split=stamp('2026-03-01'),end=stamp('2026-05-01'),
        baseline=BASE,candidates=candidates(),
        selection='At least 12 Jan-Feb trades preferred; maximize worst marked month, then total net, then name. No March-April input.',
        evaluation='Record unchanged baseline before Jan-Feb tuning; freeze selected settings before March-April; show baseline and selected with no holdout reselection',
        gate='Both development months positive, >=12 development trades; holdout >=20 trades, net>0, PF>=1.15, marked DD<=5%, weekly bootstrap lower95>0, doubled-cost replay net>0',
        costs=asdict(SweepConfig()),ratio='1:1 price distances; unchanged risk and portfolio controls',
        source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path('scalp/swing.py'),Path('scalp/swing_sweep.py'))})


def select(rows):
    return sorted(rows,key=lambda r:(r['summary']['trades']<12,-min(r['months'].values()),-r['summary']['net_pnl'],r['id']))[0]


def download():
    # Reuse the validated hourly/funding downloader with an isolated artifact root.
    old_root,old_protocol=swing_study.ROOT,swing_study.protocol
    try:
        swing_study.ROOT,swing_study.protocol=ROOT,protocol
        swing_study.download()
    finally:
        swing_study.ROOT,swing_study.protocol=old_root,old_protocol


def run():
    plan=json.loads((ROOT/'protocol.json').read_text())
    if plan!=protocol():raise ValueError('Frozen protocol/source changed')
    blob=(ROOT/'market_data.json').read_bytes();raw=json.loads(blob)
    all_data={s:[Candle(**c) for c in rows] for s,rows in raw['data'].items()}
    expected=list(range(plan['start']-400*HOUR,plan['end'],HOUR))
    if any([c.ts for c in rows]!=expected for rows in all_data.values()):raise ValueError('Chronology mismatch')
    prepared={};runs={};checked=0
    def evaluate(c,start,end,stress=False):
        nonlocal checked
        # Strip holdout prices AND funding before any development calculation.
        data={s:[r for r in rows if r.ts<end] for s,rows in all_data.items()}
        funding={s:[r for r in rows if r['ts']<end] for s,rows in raw['funding'].items()}
        key=(end,c['lookback'])
        if key not in prepared:
            prepared[key]={s:chart_features(rows,2,c['lookback'],features(rows)) for s,rows in data.items()}
        cfg=SweepConfig(c['method'],c['atr_multiple'],c['max_hours'])
        if stress:cfg=replace(cfg,fee=cfg.fee*2,slip=cfg.slip*2)
        result=simulate(data,prepared[key],funding,cfg,start,end)
        for t in result['trades']:
            assert abs(abs(t['entry']-t['stop'])-abs(t['target']-t['entry']))<1e-8
            assert abs(t['gross_pnl']-t['entry_fee']-t['exit_fee']-t['funding_cost']-t['net_pnl'])<1e-8
            assert t['hold_hours']<=c['max_hours'];checked+=1
        row=dict(id=name(c),parameters=c,summary=result['summary'],months=period_changes(result['equity'],'month'))
        return row,result
    baseline_dev,base_run=evaluate(BASE,plan['start'],plan['split'])
    save('baseline_before_tuning.json',dict(row=baseline_dev,run=base_run,protocol=plan))
    print(json.dumps(dict(stage='UNCHANGED_BASELINE_JAN_FEB',**baseline_dev)),flush=True)
    development=[]
    for c in candidates():
        if c==BASE:row,result=baseline_dev,base_run
        else:row,result=evaluate(c,plan['start'],plan['split'])
        development.append(row);runs[row['id']]=result
    winner=select(development);chosen=winner['parameters']
    frozen=dict(selected=winner,development=development,protocol=plan,dataset_sha256=hashlib.sha256(blob).hexdigest())
    save('selection_before_holdout.json',frozen)
    print(json.dumps(dict(stage='SELECTION_FROZEN',**winner)),flush=True)
    baseline_holdout,base_holdout_run=evaluate(BASE,plan['split'],plan['end'])
    if chosen==BASE:selected_holdout,selected_run=baseline_holdout,base_holdout_run
    else:selected_holdout,selected_run=evaluate(chosen,plan['split'],plan['end'])
    stress_row,stress_run=evaluate(chosen,plan['split'],plan['end'],True)
    ci=swing_study.bootstrap(selected_run['equity']);s=selected_holdout['summary']
    gates=dict(development_positive_months=min(winner['months'].values())>0,
               development_trades=winner['summary']['trades']>=12,
               holdout_trades=s['trades']>=20,holdout_net=s['net_pnl']>0,
               holdout_pf=s['profit_factor'] is None or s['profit_factor']>=1.15,
               holdout_drawdown=s['drawdown_pct']<=5,
               bootstrap_lower_positive=ci['lower95']>0,stress_positive=stress_row['summary']['net_pnl']>0)
    # Follow the frozen schedule continuously as an extra diagnostic. Positions
    # are flat at the split in the stage results; no hidden carryover is assumed.
    result=dict(frozen,baseline_development=baseline_dev,baseline_holdout=baseline_holdout,
                selected_holdout=selected_holdout,stress=stress_row,bootstrap=ci,gates=gates,passed=all(gates.values()),
                accounting_trades_verified=checked,funding_mark_fallbacks=raw['funding_mark_fallbacks'])
    save('results.json',result)
    save('ledgers.json',dict(development=runs,baseline_holdout=base_holdout_run,selected_holdout=selected_run,stress=stress_run))
    save('frozen_candidate.json',dict(parameters=chosen,ratio='1:1 price',research_only=True))
    with (ROOT/'development_grid.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['id',*BASE,*s,'January_net','February_net']);writer.writeheader()
        for row in sorted(development,key=lambda r:-r['summary']['net_pnl']):
            writer.writerow(dict(id=row['id'],**row['parameters'],**row['summary'],January_net=row['months']['2026-01'],February_net=row['months']['2026-02']))
    with (ROOT/'holdout_trades.csv').open('w',newline='',encoding='utf-8') as f:
        trades=[dict(candidate=label,**t) for label,run in (('baseline',base_holdout_run),('selected',selected_run)) for t in run['trades']]
        if trades:
            writer=csv.DictWriter(f,fieldnames=list(trades[0]));writer.writeheader();writer.writerows(trades)
    lines=['# Fresh 2026 evaluation and small refinements','',f"Acceptance passed: {result['passed']}. Frozen candidate: {winner['id']}.",
           '', 'January 1–March 1, 2026: first evaluate the 2025 winner unchanged, then tune 27 nearby settings. March 1–May 1: independent holdout for the frozen choice. Each stage starts at 1,000 USDT and is flat at the boundary. This is fresh historical data, not prospective live trading.',
           '', '| Configuration | Period | Trades | Net USDT | Return | Hourly DD | PF |','|---|---|---:|---:|---:|---:|---:|']
    for label,row,period in (('2025 baseline',baseline_dev,'Jan-Feb'),('Refined selection',winner,'Jan-Feb'),('2025 baseline',baseline_holdout,'Mar-Apr'),('Refined selection',selected_holdout,'Mar-Apr'),('Refined, double fee/slip',stress_row,'Mar-Apr')):
        q=row['summary'];lines.append(f"| {label} | {period} | {q['trades']} | {q['net_pnl']:.2f} | {q['return_pct']:.2f}% | {q['drawdown_pct']:.2f}% | {q['profit_factor']} |")
    lines+=['','## Selected monthly results','', '| Month | Marked equity change USDT |','|---|---:|']
    for m,v in dict(winner['months'],**selected_holdout['months']).items():lines.append(f'| {m} | {v:.2f} |')
    lines+=['','## Refinement','', 'Fixed 2h breakout / 4h EMA trend. Lookback 28/30/32 bars, stop 3.5/3.75/4 ATR, maximum hold 48/54/60 hours. Equal target distance. All other risk, execution and entry/exit rules unchanged. Selection prioritizes at least 12 development trades, then the weaker marked month, then total net. Baseline was 30 bars / 3.75 ATR / 54h.',
            '',f"Positive development configurations: {sum(r['summary']['net_pnl']>0 for r in development)}/27. Monthly stability, not highest total return, determines the selected candidate.",
            '', '## Acceptance gates','']
    for k,v in gates.items():lines.append(f'- {k}: {v}')
    lines+=['',f"Weekly marked-equity bootstrap 95% interval: [{ci['lower95']:.2f}, {ci['upper95']:.2f}] USDT/week, based on {ci['weeks']} complete weeks. This is a small sample with limited statistical power; IID week resampling is not multiple-testing corrected.",
            '', 'Costs: 5bps taker per side, 4bps spread, 2bps slippage per side, historical funding. Stress doubles fees and slippage with a full rerun, so trades can change. Risk: 0.25%/trade, 0.5% combined stop risk, <=1x gross, 1.5% daily equity loss halt. Protective targets/stops and trend invalidation can exit before the maximum hold.',
            '',f"Accounting and 1:1 checks passed for {checked} simulated trades. Funding mark fallbacks: {raw['funding_mark_fallbacks']}.",
            '', 'Limitations: no historical book depth, partial fills, lot filters, liquidation, taxes or intrahour drawdown. Stop wins ambiguous intrabar ties. Funding is bucketed to hourly open. Prior 2025 optimization selected from 286 settings; this short holdout cannot prove a durable edge. No reselection or post-holdout tuning. No exchange orders or automatic deployment.',
            '', '[Development grid](development_grid.csv) | [Holdout trades](holdout_trades.csv) | [Full results](results.json)']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(stage='HOLDOUT',baseline=baseline_holdout,selected=selected_holdout,stress=stress_row,bootstrap=ci,gates=gates,passed=result['passed'])),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('download','run'))
    download() if p.parse_args().command=='download' else run()
