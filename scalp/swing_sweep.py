"""Exploratory fixed-2025 parameter sweep. Same-data selection is NOT validation."""
import argparse
import csv
from dataclasses import dataclass,asdict,replace
from datetime import datetime,timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
from statistics import median

from .signals import Candle,ema
from .swing import HOUR,SwingConfig,features,simulate
from .swing_study import bootstrap
from .experiments import stamp

ROOT=Path('artifacts/swing_sweep')
DATA=Path('artifacts/swing1/market_data.json')
START=stamp('2025-01-01');END=stamp('2026-01-01')


@dataclass(frozen=True)
class SweepConfig(SwingConfig):
    def __post_init__(self):
        if type(self.max_hours) is not int or not 6<=self.max_hours<=72 or not math.isfinite(self.atr_multiple):
            raise ValueError('Invalid sweep holding limit or ATR multiple')
        SwingConfig(self.method,self.atr_multiple,24,self.fee,self.slip,self.half_spread)


def aggregate(rows,hours):
    result=[];groups={}
    for r in rows:groups.setdefault(r.ts//(hours*HOUR)*(hours*HOUR),[]).append(r)
    for ts,group in sorted(groups.items()):
        if [c.ts for c in group]!=list(range(ts,ts+hours*HOUR,HOUR)):continue
        result.append(Candle(ts,group[0].open,max(c.high for c in group),min(c.low for c in group),group[-1].close,sum(c.volume for c in group)))
    return result


def chart_features(rows,hours,lookback,trend_features):
    """Entries only on completed chart bars; original 4h exit trend refreshed hourly."""
    chart=aggregate(rows,hours);closes=[c.close for c in chart];average=ema(closes,lookback)
    triggers={}
    for i in range(max(lookback+1,15),len(chart)):
        prev=chart[i-1];ts=chart[i].ts
        if prev.ts+hours*HOUR!=ts:raise ValueError('Noncontiguous chart')
        upper=max(c.high for c in chart[i-lookback-1:i-1]);lower=min(c.low for c in chart[i-lookback-1:i-1])
        b=1 if prev.close>upper else -1 if prev.close<lower else 0
        p=1 if closes[i-2]<=average[i-2] and closes[i-1]>average[i-1] else -1 if closes[i-2]>=average[i-2] and closes[i-1]<average[i-1] else 0
        atr=sum(max(c.high-c.low,abs(c.high-a.close),abs(c.low-a.close)) for a,c in zip(chart[i-15:i-1],chart[i-14:i]))/14
        triggers[ts]=dict(breakout=b,pullback=p,atr_fraction=atr/prev.close)
    return {ts:dict(trend=f['trend'],**triggers.get(ts,dict(breakout=0,pullback=0,atr_fraction=0))) for ts,f in trend_features.items()}


def name(c):
    return f"{c['method']}_tf{c['timeframe']}_lb{c['lookback']}_atr{c['atr_multiple']:g}_h{c['max_hours']}"


def candidates():
    return [dict(method=m,timeframe=t,lookback=l,atr_multiple=a,max_hours=h)
            for m,t,l,a,h in itertools.product(('breakout','pullback'),(1,2,4),(10,20,30),(2,2.5,3,3.5,4),(24,48,72))]


def nearby(c):
    return [dict(c,atr_multiple=round(c['atr_multiple']+a,2),max_hours=c['max_hours']+h)
            for a,h in itertools.product((-.25,0,.25),(-6,0,6)) if 6<=c['max_hours']+h<=72]


def save(name,value):
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/name).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def period_changes(curve,period):
    groups={};prior=1000.
    for r in curve:
        dt=datetime.fromtimestamp((r['ts']-1)/1000,timezone.utc)
        key=f'{dt.year}-{dt.month:02}' if period=='month' else f'{dt.year}-Q{(dt.month-1)//3+1}'
        groups[key]=groups.get(key,0)+r['equity']-prior;prior=r['equity']
    return groups


def order(rows):
    return sorted(rows,key=lambda r:(r['summary']['trades']<50,-r['summary']['net_pnl'],r['id']))


def run():
    blob=DATA.read_bytes();raw=json.loads(blob)
    data={s:[Candle(**c) for c in rows] for s,rows in raw['data'].items()}
    for rows in data.values():
        if [c.ts for c in rows if START<=c.ts<END]!=list(range(START,END,HOUR)):
            raise ValueError('Fixed timeline incomplete')
    plan=dict(start=START,end=END,base_grid=candidates(),refinement='Top three base rows with >=50 trades: ATR +/-0.25 and hold +/-6h; clip to <=72h; deduplicate',
              ranking='Require >=50 trades for priority, then net P&L, then name; exploratory same-period optimization',
              risk='Unchanged: 0.25%/trade, 0.5% combined, 1x gross cap, 1.5% daily equity loss halt, 1:1 price ratio',
              costs=asdict(SwingConfig()),data_sha256=hashlib.sha256(blob).hexdigest(),
              hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path('scalp/swing.py'))})
    save('protocol.json',plan)
    prepared={};trends={s:features(rows) for s,rows in data.items()}
    for t,l in itertools.product((1,2,4),(10,20,30)):
        prepared[t,l]={s:chart_features(rows,t,l,trends[s]) for s,rows in data.items()}
    # Features include only completed bars. Price monitoring always remains hourly.
    data={s:[c for c in rows if START<=c.ts<END] for s,rows in data.items()}
    results=[];full={};seen=set();checked=0
    def evaluate(c,stage,stress=False):
        nonlocal checked
        cfg=SweepConfig(c['method'],c['atr_multiple'],c['max_hours'])
        if stress:cfg=replace(cfg,fee=cfg.fee*2,slip=cfg.slip*2)
        r=simulate(data,prepared[c['timeframe'],c['lookback']],raw['funding'],cfg,START,END)
        for trade in r['trades']:
            assert abs(abs(trade['entry']-trade['stop'])-abs(trade['target']-trade['entry']))<1e-8
            assert abs(trade['gross_pnl']-trade['entry_fee']-trade['exit_fee']-trade['funding_cost']-trade['net_pnl'])<1e-8
            assert trade['hold_hours']<=c['max_hours'];checked+=1
        row=dict(id=name(c),stage=stage,parameters=c,summary=r['summary'],quarters=period_changes(r['equity'],'quarter'),months=period_changes(r['equity'],'month'))
        if not stress:
            seen.add(name(c));results.append(row);full[name(c)]=r
            with (ROOT/'progress.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')
            if len(results)%15==0:print(json.dumps(dict(completed=len(results),best_net=order(results)[0]['summary']['net_pnl'])),flush=True)
        return row,r
    for c in candidates():evaluate(c,'base')
    seeds=[r for r in order(results) if r['summary']['trades']>=50][:3]
    save('refinement_seeds.json',seeds)
    for seed in seeds:
        for c in nearby(seed['parameters']):
            if name(c) not in seen:evaluate(c,'refinement')
    ranked=order(results);top=ranked[:5];diagnostics={}
    for row in top:
        c=row['parameters'];_,stress=evaluate(c,'stress',True)
        neighborhood=[r for r in results if all(r['parameters'][k]==c[k] for k in ('method','timeframe','lookback'))
                      and abs(r['parameters']['atr_multiple']-c['atr_multiple'])<=.5 and abs(r['parameters']['max_hours']-c['max_hours'])<=24 and r['id']!=row['id']]
        diagnostics[row['id']]=dict(stress_summary=stress['summary'],bootstrap=bootstrap(full[row['id']]['equity']),
            neighbors=len(neighborhood),profitable_neighbors=sum(r['summary']['net_pnl']>0 for r in neighborhood),
            median_neighbor_net=median(r['summary']['net_pnl'] for r in neighborhood) if neighborhood else None)
    best=ranked[0];save('best_run.json',dict(candidate=best,run=full[best['id']],diagnostics=diagnostics[best['id']]))
    # Keep full ledgers only for the five leading configurations; all summaries persist.
    save('top_runs.json',{r['id']:full[r['id']] for r in top})
    result=dict(protocol=plan,total_runs=len(results),positive_runs=sum(r['summary']['net_pnl']>0 for r in results),ranked=ranked,diagnostics=diagnostics,accounting_trades_verified=checked,
                status='Exploratory same-data optimization; no independent validation or promotion')
    save('results.json',result)
    with (ROOT/'all_runs.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['id','stage',*best['parameters'],*best['summary'],'Q1','Q2','Q3','Q4']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in ranked:writer.writerow(dict(id=row['id'],stage=row['stage'],**row['parameters'],**row['summary'],**{f'Q{i}':row['quarters'][f'2025-Q{i}'] for i in range(1,5)}))
    trades=full[best['id']]['trades']
    with (ROOT/'best_trades.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(trades[0]));writer.writeheader();writer.writerows(trades)
    lines=['# Fixed-timeline swing parameter sweep','',f"{len(results)} configurations; {result['positive_runs']} positive after modeled costs. {checked} trade accounting/ratio checks passed.",
           '', '**All ranking, refinement and results use the same 2025 interval. This is exploratory optimization, not untouched validation.**',
           '', 'BTC/ETH perpetuals; each run starts at 1,000 USDT. Same raw hourly prices and funding as swing1. Chart entries use complete 1h/2h/4h bars; stop and target monitoring remains hourly. 4h EMA20/50 trend filter and slope remain fixed.',
           '', 'Base sweep: two methods x three chart periods x three lookbacks x five ATR multiples x three hold limits = 270 runs. For breakouts lookback controls the range; for pullbacks it controls EMA length. Top three base rows receive ATR +/-0.25 and holding-time +/-6h refinements. Duplicates excluded. All other controls stay fixed.',
           '', '| Rank | Configuration | Trades | Net USDT | Return | DD | Win rate | PF |','|---|---|---:|---:|---:|---:|---:|---:|']
    for i,row in enumerate(ranked[:15],1):
        s=row['summary'];lines.append(f"| {i} | {row['id']} | {s['trades']} | {s['net_pnl']:.2f} | {s['return_pct']:.2f}% | {s['drawdown_pct']:.2f}% | {s['win_rate']:.1%} | {s['profit_factor']:.3f} |")
    lines+=['','## Best five: cost and parameter sensitivity','', '| Candidate | Double fee/slip net | Profitable neighbors | Median neighbor net | Weekly bootstrap 95% interval |','|---|---:|---:|---:|---|']
    for row in top:
        d=diagnostics[row['id']];ci=d['bootstrap']
        lines.append(f"| {row['id']} | {d['stress_summary']['net_pnl']:.2f} | {d['profitable_neighbors']}/{d['neighbors']} | {d['median_neighbor_net']} | [{ci['lower95']:.2f}, {ci['upper95']:.2f}] |")
    lines+=['','## Best result by quarter','', '| Quarter | Marked equity change USDT |','|---|---:|']
    for q,v in best['quarters'].items():lines.append(f'| {q} | {v:.2f} |')
    lines+=['','Quarter/month attribution uses continuous marked equity, including open positions. No account reset between quarters; each full-year configuration is a fresh account. Neighbor comparisons exclude the winner and preserve family/timeframe/lookback.',
            '', 'Costs: 5 bps taker per fill, 4 bps total spread, 2 bps slippage per side, historical funding. Stress reruns double fees and slippage and may change trades. Net 1:1 is not claimed: targets and stops are equal executable price distances before commission/funding.',
            '', 'Limitations: many same-data comparisons invite overfitting; best net is subject to selection bias. Bootstrap is descriptive and not corrected for multiple testing. Small parameter changes are not independent evidence. Hourly intrabar order is unknown (stop wins ties); historical depth, partial fills, lot filters, taxes, liquidation and intrahour drawdown are unmodeled. Prior report documents any funding-price fallback. No exchange orders or strategy promotion.',
            '', '[All runs CSV](all_runs.csv) | [Best trade ledger](best_trades.csv) | [Full results](results.json)']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(complete=True,runs=len(results),positive=result['positive_runs'],best=best,diagnostics=diagnostics[best['id']])),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.parse_args();run()
