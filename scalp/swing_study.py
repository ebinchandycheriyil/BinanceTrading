"""Bounded development iterations, then a single untouched validation year."""
import argparse
from dataclasses import asdict,replace
import hashlib
import json
from pathlib import Path
import random
from collections import Counter

from .experiments import stamp
from .market import BinanceData
from .signals import Candle
from .swing import HOUR,SwingConfig,features,simulate,fill

ROOT=Path('artifacts/swing1')


def save(name,value):
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/name).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def protocol():
    return dict(start=stamp('2022-01-01'),end=stamp('2026-01-01'),
        stage1='Breakout vs pullback, ATR2, 72h; rank worst annual net across 2022 and 2023',
        stage2='Selected family only: ATR1.5/2/3 crossed with 24/72h; rank worst half-year net across 2024',
        eligibility='At least 20 trades in each selection fold; if none, diagnostic ranking only',
        validation='Freeze before 2025; no validation reselection',
        gate='Every selected 2024 fold positive; 2025 net>0, >=50 trades, PF>=1.15, hourly marked DD<=10%, weekly block bootstrap lower95>0, doubled-cost replay net>0',
        ratio='1:1 executable price distances, fees and funding deducted separately',
        base=asdict(SwingConfig()),risk='0.25%/trade, 0.5% aggregate planned stop risk, 1x gross cap, daily 1.5% loss halt',
        source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path('scalp/swing.py'))})


def download():
    plan=protocol();save('protocol.json',plan)
    market=BinanceData(('BTC/USDT:USDT','ETH/USDT:USDT'))
    start=plan['start']-400*HOUR;end=plan['end'];raw=dict(data={},funding={},funding_mark_fallbacks=0)
    for s in ('BTC/USDT:USDT','ETH/USDT:USDT'):
        cursor=start;rows={}
        while cursor<end:
            page=market.exchange.fetch_ohlcv(s,'1h',since=cursor,limit=1000)
            if not page:raise ValueError('Empty candle page')
            for r in page:
                if start<=r[0]<end:rows[int(r[0])]=asdict(Candle(int(r[0]),*map(float,r[1:6])))
            next_cursor=int(page[-1][0])+HOUR
            if next_cursor<=cursor:raise ValueError('Pagination stuck')
            cursor=next_cursor
        if sorted(rows)!=list(range(start,end,HOUR)):raise ValueError('Incomplete hourly chronology')
        raw['data'][s]=[rows[t] for t in sorted(rows)]
        cursor=start;events={}
        while cursor<end:
            page=market.exchange.fapiPublicGetFundingRate(dict(symbol=market.exchange.market(s)['id'],startTime=cursor,endTime=end-1,limit=1000))
            if not page:break
            for r in page:
                ts=int(r['fundingTime']);mark=r.get('markPrice')
                if not mark:
                    mark=rows[ts//HOUR*HOUR]['open'];raw['funding_mark_fallbacks']+=1
                events[ts]=dict(ts=ts,rate=float(r['fundingRate']),mark=float(mark))
            cursor=int(page[-1]['fundingTime'])+1
            if len(page)<1000:break
        if not events or min(events)>start+12*HOUR or max(events)<end-12*HOUR or any(b-a>12*HOUR for a,b in zip(sorted(events),sorted(events)[1:])):
            raise ValueError('Funding coverage gap')
        raw['funding'][s]=[events[t] for t in sorted(events)]
        print(json.dumps(dict(download=s,bars=len(rows),funding=len(events))),flush=True)
    save('market_data.json',raw)


def rank(results):
    eligible={n:r for n,r in results.items() if all(f['summary']['trades']>=20 for f in r)}
    pool=eligible or results
    return sorted(pool,key=lambda n:(-min(f['summary']['net_pnl'] for f in pool[n]),n))[0]


def bootstrap(curve):
    # Nonoverlapping seven-day marked-equity changes, including zero-trade periods.
    blocks=[];prev=1000.
    for i in range(167,len(curve),168):
        blocks.append(curve[i]['equity']-prev);prev=curve[i]['equity']
    # Omit last incomplete block explicitly rather than giving it full-week weight.
    rng=random.Random(20260926)
    draws=sorted(sum(rng.choice(blocks) for _ in blocks)/len(blocks) for _ in range(5000))
    return dict(mean_weekly=sum(blocks)/len(blocks),lower95=draws[125],upper95=draws[4875],weeks=len(blocks),
                method='IID resampling of nonoverlapping 7-day marked-equity changes; final partial week omitted, no multiple-testing correction')


def benchmark(data,funding,cfg,start,end):
    # Equal capital in two long perpetuals; includes funding, not spot buy-and-hold.
    total=0
    for s,rows in data.items():
        r=[c for c in rows if start<=c.ts<end]
        entry=fill(r[0].open,1,cfg);qty=500/(entry*(1+cfg.fee));exit=fill(r[-1].close,-1,cfg)
        financing=sum(qty*e['mark']*e['rate'] for e in funding[s] if start<e['ts']<end)
        total+=qty*(exit-entry)-qty*(entry+exit)*cfg.fee-financing
    return dict(net_pnl=total,return_pct=total/10,description='50/50 BTC/ETH perpetual buy-and-hold, initial gross <=1x, costs and funding; different risk exposure from strategy',cash_return_pct=0)


def run():
    plan=json.loads((ROOT/'protocol.json').read_text())
    if plan!=protocol():raise ValueError('Frozen source/protocol changed')
    blob=(ROOT/'market_data.json').read_bytes();raw=json.loads(blob)
    data={s:[Candle(**r) for r in rows] for s,rows in raw['data'].items()}
    expected=list(range(plan['start']-400*HOUR,plan['end'],HOUR))
    if any([c.ts for c in rows]!=expected for rows in data.values()):raise ValueError('Chronology mismatch')
    def evaluate(cfg,start,end):
        # Never prepare future-year features during selection.
        subset={s:[r for r in rows if r.ts<end] for s,rows in data.items()}
        key=end
        if key not in prepared:prepared[key]={s:features(rows) for s,rows in subset.items()}
        result=simulate(subset,prepared[key],raw['funding'],cfg,start,end)
        print(json.dumps(dict(config=asdict(cfg),start=start,**result['summary'])),flush=True)
        return result
    prepared={}
    stage1={method:[evaluate(SwingConfig(method),stamp(a),stamp(b)) for a,b in (('2022-01-01','2023-01-01'),('2023-01-01','2024-01-01'))]
            for method in ('breakout','pullback')}
    family=rank(stage1);save('iteration1.json',dict(selected_family=family,results=stage1))
    configs={f'{family}_atr{atr}_h{h}':SwingConfig(family,atr,h) for atr in (1.5,2,3) for h in (24,72)}
    stage2={n:[evaluate(c,stamp(a),stamp(b)) for a,b in (('2024-01-01','2024-07-01'),('2024-07-01','2025-01-01'))] for n,c in configs.items()}
    selected=rank(stage2);cfg=configs[selected]
    frozen=dict(selected=selected,config=asdict(cfg),stage1=stage1,stage2=stage2,protocol=plan,dataset_sha256=hashlib.sha256(blob).hexdigest())
    save('selection_before_validation.json',frozen)
    print(json.dumps(dict(selection_frozen=selected)),flush=True)
    valid=evaluate(cfg,stamp('2025-01-01'),stamp('2026-01-01'))
    stress=evaluate(replace(cfg,fee=cfg.fee*2,slip=cfg.slip*2),stamp('2025-01-01'),stamp('2026-01-01'))
    ci=bootstrap(valid['equity']);s=valid['summary']
    passed=(all(r['summary']['net_pnl']>0 and r['summary']['trades']>=20 for r in stage2[selected])
            and s['net_pnl']>0 and s['trades']>=50 and (s['profit_factor'] is None or s['profit_factor']>=1.15)
            and s['drawdown_pct']<=10 and ci['lower95']>0 and stress['summary']['net_pnl']>0)
    result=dict(frozen,validation=valid,stress=stress,bootstrap=ci,passed=passed,
                benchmark=benchmark(data,raw['funding'],cfg,stamp('2025-01-01'),stamp('2026-01-01')),
                funding_mark_fallbacks=raw['funding_mark_fallbacks'])
    save('results.json',result)
    lines=['# Iterative swing research','',f'Selected: {selected}. Acceptance passed: {passed}.',
           '', '1,000 USDT per fold; 1:1 price-distance stops and targets; fees and funding separate.',
           '', '## Iteration 1: family selection','', '| Family | 2022 net | 2023 net |','|---|---:|---:|']
    for n,folds in stage1.items():lines.append(f"| {n} | {folds[0]['summary']['net_pnl']:.2f} | {folds[1]['summary']['net_pnl']:.2f} |")
    lines+=['','## Iteration 2: stop and duration','', '| Candidate | 2024 H1 net | 2024 H2 net |','|---|---:|---:|']
    for n,folds in stage2.items():lines.append(f"| {n} | {folds[0]['summary']['net_pnl']:.2f} | {folds[1]['summary']['net_pnl']:.2f} |")
    lines+=['','## Untouched 2025 validation','',json.dumps(s,indent=2),'',
            f"Doubled fee/slippage rerun net: {stress['summary']['net_pnl']:.2f}. Weekly bootstrap interval: [{ci['lower95']:.2f}, {ci['upper95']:.2f}] USDT.",
            '',f"Perpetual buy-and-hold benchmark: {result['benchmark']['return_pct']:.2f}%; cash: 0%. Benchmark exposure differs substantially from risk-sized strategy.",
            '', 'Exit counts: '+json.dumps(dict(Counter(t['exit_reason'] for t in valid['trades']))),
            '', 'Signals use completed hourly bars and complete UTC four-hour closes: EMA20/50 trend and EMA20 slope; hourly 20-bar breakout or EMA20 reclaim. ATR14 stop, floor 0.5%, reject above 6%. Trend invalidation exits at next hourly open. Stop-first intrabar ties, gap exits at available executable open. No five-minute exit.',
            '', f"Funding mark-price fallbacks to settlement-hour open: {raw['funding_mark_fallbacks']}. Settlements bucketed to hourly open, before exits/entries; precise intrahour funding order unmodeled.",
            '', 'Research only; no automatic promotion or exchange orders. Historical depth, lot sizes, liquidation mechanics, taxes, latency and intrahour drawdown are not modeled. Hourly drawdown can understate intrabar risk. Simultaneous symbol sizing uses deterministic symbol order. Eight candidate configurations were considered in a staged search; bootstrap is not adjusted for selection and serial dependence across weeks. This identifies the best candidate within this bounded search, not a global optimum.',
            '', 'Sources: [cryptocurrency momentum research](https://www.nber.org/papers/w24877), [Binance funding API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History). These rules are hypotheses rather than a replication.']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(selected=selected,passed=passed)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('download','run'))
    download() if p.parse_args().command=='download' else run()
