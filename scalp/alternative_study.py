"""Staged research of pairs, perpetual funding carry, and relative momentum."""
import argparse
from collections import defaultdict
from dataclasses import asdict
import hashlib
import itertools
import json
from pathlib import Path

from .alternatives import HOUR,build_features,simulate
from .signals import Candle
from .experiments import stamp
from .swing_sweep import period_changes
from .swing_study import bootstrap

ROOT=Path('artifacts/alternatives1')
COINS=('BTC','ETH','BNB','XRP','ADA','LTC','LINK','BCH')


def save(file,value):
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/file).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def grid():
    return ([dict(family='pairs',window=w,z=z) for w,z in itertools.product((168,336,672),(1.5,2,2.5))]
           +[dict(family='carry',threshold=t,hold=h) for t,h in itertools.product((.05,.1,.15),(168,336))]
           +[dict(family='momentum',window=w,hold=h) for w,h in itertools.product((168,336,672),(24,72))])


def neighbors(c):
    if c['family']=='pairs':return [dict(c,window=c['window']+w,z=c['z']+z) for w,z in itertools.product((-24,0,24),(-.25,0,.25))]
    if c['family']=='carry':return [dict(c,threshold=round(c['threshold']+t,3),hold=c['hold']+h) for t,h in itertools.product((-.02,0,.02),(-24,0,24))]
    return [dict(c,window=c['window']+w,hold=c['hold']+h) for w,h in itertools.product((-24,0,24),(-12,0,12)) if c['hold']+h>=12]


def identity(c):return '_'.join(f'{k}-{v}' for k,v in sorted(c.items()))


def protocol():
    return dict(start=stamp('2020-07-01'),refine=stamp('2021-01-01'),validate=stamp('2021-07-01'),end=stamp('2022-01-01'),
                coins=COINS,base_grid=grid(),refinement='Nine small neighbors of each family development winner, tested on Jan-Jun 2021 only',
                ranking='Priority >=10 baskets, then weakest marked quarter net, then total net, then name',
                acceptance='Both refinement quarters positive, >=10 refinement baskets; final >=20 baskets, net positive, PF>=1.15, DD<=10%, bootstrap lower>0, doubled-cost net>0',
                risk='1:1 combined executable gross basket P&L target/stop, estimated 0.25% loss budget including friction, one basket, <=0.8x gross, daily1.5% halt; hourly monitoring can overrun stops',
                fees='Futures 5bps each fill, spot 10bps each fill, 4bps spread and 2bps adverse slippage each side; funding on all carried futures legs',
                limitations='Fixed eight surviving coins selected today, not historical universe; carry is variable perpetual funding, not locked dated-futures arbitrage',
                spot_gaps='No carry executions in missing spot hours; last observed price for valuation only; funding still settles; deadlines can overrun until quotes resume',
                source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path('scalp/alternatives.py'))})


def download():
    import ccxt
    plan=protocol();save('protocol.json',plan)
    futures=ccxt.binanceusdm({'enableRateLimit':True,'timeout':15000});futures.load_markets()
    spot=ccxt.binance({'enableRateLimit':True,'timeout':15000});spot.load_markets()
    start=plan['start']-800*HOUR;end=plan['end'];raw=dict(data={},funding={},funding_mark_fallbacks=0,unavailable_spot=[])
    cached=ROOT/'market_data.json'
    if cached.exists():
        previous=json.loads(cached.read_text())
        if all([r['ts'] for r in rows]==list(range(start,end,HOUR)) for rows in previous['data'].values()):
            raw=previous;raw.setdefault('unavailable_spot',[])
    for coin,kind in [(c,'P') for c in COINS]+[('BTC','S'),('ETH','S')]:
        market=futures if kind=='P' else spot;symbol=f'{coin}/USDT:USDT' if kind=='P' else f'{coin}/USDT';key=f'{coin}-{kind}'
        if key in raw['data'] and (kind=='S' or key in raw['funding']):
            print(json.dumps(dict(cached=key)),flush=True);continue
        rows={};cursor=start
        while cursor<end:
            page=market.fetch_ohlcv(symbol,'1h',since=cursor,limit=1000)
            if not page:raise ValueError('Empty OHLCV page '+key)
            for r in page:
                if start<=r[0]<end:rows[int(r[0])]=asdict(Candle(int(r[0]),*map(float,r[1:6])))
            next_cursor=int(page[-1][0])+HOUR
            if next_cursor<=cursor:raise ValueError('Pagination stuck')
            cursor=next_cursor
        missing=sorted(set(range(start,end,HOUR))-set(rows))
        if missing and kind=='P':raise ValueError('Missing hourly futures data '+key)
        if missing:
            if start not in rows:raise ValueError('No initial spot valuation')
            last=None
            for ts in range(start,end,HOUR):
                if ts not in rows:
                    rows[ts]=asdict(Candle(ts,last,last,last,last,0));raw['unavailable_spot'].append(ts)
                last=rows[ts]['close']
            print(json.dumps(dict(spot_gap_hours=key,count=len(missing),timestamps=missing)),flush=True)
        raw['data'][key]=[rows[t] for t in sorted(rows)]
        if kind=='P':
            events={};cursor=start
            while cursor<end:
                page=futures.fapiPublicGetFundingRate(dict(symbol=futures.market(symbol)['id'],startTime=cursor,endTime=end-1,limit=1000))
                if not page:break
                for r in page:
                    ts=int(r['fundingTime']);mark=r.get('markPrice')
                    if not mark:mark=rows[ts//HOUR*HOUR]['open'];raw['funding_mark_fallbacks']+=1
                    events[ts]=dict(ts=ts,mark=float(mark),rate=float(r['fundingRate']))
                next_cursor=int(page[-1]['fundingTime'])+1
                if next_cursor<=cursor:raise ValueError('Funding pagination stuck')
                cursor=next_cursor
                if len(page)<1000:break
            times=sorted(events)
            if not times or times[0]>start+12*HOUR or times[-1]<end-12*HOUR or any(b-a>12*HOUR for a,b in zip(times,times[1:])):raise ValueError('Missing funding '+key)
            raw['funding'][key]=[events[t] for t in times]
        print(json.dumps(dict(download=key,bars=len(rows))),flush=True)
        save('market_data.json',raw) # resume evidence if a later market fails


def select(rows):
    return sorted(rows,key=lambda r:(r['summary']['trades']<10,-min(r['quarters'].values()),-r['summary']['net_pnl'],r['id']))[0]


def run():
    plan=json.loads((ROOT/'protocol.json').read_text())
    if plan!=json.loads(json.dumps(protocol())):raise ValueError('Frozen protocol changed')
    blob=(ROOT/'market_data.json').read_bytes();raw=json.loads(blob)
    data={s:[Candle(**c) for c in rows] for s,rows in raw['data'].items()}
    assert set(data)=={c+'-P' for c in COINS}|{'BTC-S','ETH-S'}
    expected=list(range(plan['start']-800*HOUR,plan['end'],HOUR))
    if any([c.ts for c in rows]!=expected for rows in data.values()):raise ValueError('Incomplete data')
    stages={};ledgers={};checked=0;feature_counts={}
    def stage(candidates,start,end,label):
        nonlocal checked
        subset={s:[r for r in rows if r.ts<end] for s,rows in data.items()}
        funding={s:[r for r in rows if r['ts']<end] for s,rows in raw['funding'].items()}
        windows=tuple(sorted({c['window'] for c in candidates if 'window' in c}))
        prepared,accepted=build_features(subset,funding,windows)
        feature_counts[label]=accepted
        results=[]
        for cfg in candidates:
            r=simulate(subset,funding,prepared,cfg,start,end,stress=label=='stress',unavailable_spot=raw.get('unavailable_spot',[]))
            for t in r['trades']:
                fees=sum(l['entry_fee']+l['exit_fee'] for l in t['legs'].values())
                gross=sum(l['sign']*(l['exit']-l['entry'])*l['qty'] for l in t['legs'].values())
                assert abs(gross-t['gross_pnl'])<1e-7
                assert abs(gross-fees-t['funding_cost']-t['net_pnl'])<1e-7
                checked+=1
            row=dict(id=identity(cfg),config=cfg,summary=r['summary'],quarters=period_changes(r['equity'],'quarter'),months=period_changes(r['equity'],'month'))
            results.append(row);ledgers[label+'|'+row['id']]=r
            print(json.dumps(dict(stage=label,**row)),flush=True)
        stages[label]=results
        return results
    base=stage(grid(),plan['start'],plan['refine'],'development')
    seeds={f:select([r for r in base if r['config']['family']==f]) for f in ('pairs','carry','momentum')}
    save('refinement_seeds.json',seeds)
    refined=stage([c for seed in seeds.values() for c in neighbors(seed['config'])],plan['refine'],plan['validate'],'refinement')
    winners={f:select([r for r in refined if r['config']['family']==f]) for f in seeds}
    save('selection_before_evaluation.json',dict(winners=winners,stages=stages,protocol=plan,dataset_sha256=hashlib.sha256(blob).hexdigest()))
    final=stage([r['config'] for r in winners.values()],plan['validate'],plan['end'],'evaluation')
    stress=stage([r['config'] for r in winners.values()],plan['validate'],plan['end'],'stress')
    diagnostics={}
    for row in final:
        f=row['config']['family'];dev=winners[f];s=row['summary']
        ci=bootstrap(ledgers['evaluation|'+row['id']]['equity'])
        stressed=next(r for r in stress if r['config']['family']==f)
        passed=(dev['summary']['trades']>=10 and min(dev['quarters'].values())>0 and s['trades']>=20 and s['net_pnl']>0
                and (s['profit_factor'] is None or s['profit_factor']>=1.15) and s['drawdown_pct']<=10 and ci['lower95']>0 and stressed['summary']['net_pnl']>0)
        diagnostics[f]=dict(bootstrap=ci,passed=passed,stress_net=stressed['summary']['net_pnl'])
    result=dict(protocol=plan,stages=stages,winners=winners,diagnostics=diagnostics,accounting_baskets_checked=checked,
                dataset_sha256=hashlib.sha256(blob).hexdigest(),accepted_cointegration_models=feature_counts,funding_mark_fallbacks=raw['funding_mark_fallbacks'])
    result['unavailable_spot_hours']=len(set(raw.get('unavailable_spot',[])))
    save('results.json',result);save('ledgers.json',ledgers)
    lines=['# Alternative strategy iterations','', 'Previously untested July 2020–December 2021 interval. Development: Jul–Dec 2020; small refinements: Jan–Jun 2021; frozen evaluation: Jul–Dec 2021. Every run begins with 1,000 USDT.',
           '', '| Family | Selected configuration | Refinement net | Evaluation trades | Evaluation net | DD | Double-cost net | Passed |','|---|---|---:|---:|---:|---:|---:|---|']
    for row in final:
        f=row['config']['family'];s=row['summary'];d=diagnostics[f]
        lines.append(f"| {f} | {row['id']} | {winners[f]['summary']['net_pnl']:.2f} | {s['trades']} | {s['net_pnl']:.2f} | {s['drawdown_pct']:.2f}% | {d['stress_net']:.2f} | {d['passed']} |")
    lines+=['','## Iterations','', '21 initial configurations: nine pair models, six funding-carry rules and six cross-sectional momentum rules. Each family winner receives nine nearby refinements. Selection uses worst marked quarter with a ten-basket activity preference. Each frozen family winner is evaluated once, plus a doubled-fee/slippage rerun. No evaluation-based reselection.',
            '', 'Pairs: daily rolling log-price regression ETH on BTC; augmented Engle-Granger p<0.05 with one fixed lag; beta 0.3–3 and estimated residual half-life 2–72h. Entry on 1.5–2.5 residual standard deviations, reject above four; small refinements vary window by one day and entry by 0.25. Beta-weighted opposite legs, frozen hedge until exit. Daily cointegration failure exits. This is a statistical screen, not a guarantee of convergence.',
            '', 'Carry: long spot and short the same base-asset quantity in BTC or ETH; choose the higher trailing-seven-day annualized realized funding rate. Threshold 5/10/15% and hold 7/14 days, then small refinements. Uses past realized funding, not future funding forecasts. Spot principal and remaining futures collateral are accounted for; exit before collateral falls below 10% of short notional. This buffer is not a full liquidation simulator. Strategy can lose when funding or basis changes.',
            '', 'Momentum: fixed universe BTC/ETH/BNB/XRP/ADA/LTC/LINK/BCH; require prior-day estimated turnover >=10m USDT. Long the two strongest positive-return coins and short the two weakest negative-return coins, inverse-volatility weights within equal long/short notional. Initial lookbacks 7/14/28 days, holds 24/72h. A fixed surviving universe creates survivorship/selection bias; these results are not a historical-universe replication.',
            '', 'All families: combined executable gross basket target and stop are equal distances in USDT; net outcomes differ after fees/funding. Size uses 0.25% equity stop-plus-friction budget and gross <=0.8x. One basket at a time. Futures 5bps/fill, spot 10bps/fill; 4bps total spread and 2bps per-side slip. Stress doubles commissions and slippage. All futures funding settled for carried legs.',
            '', 'Execution is simultaneous at hourly opens/closes; this ignores legging delays and can miss intrahour stops or liquidation. Realized losses may exceed planned risk. No order-book depth, partial fills, historical lot constraints, taxes or financing of idle capital. Dollar P&L treats spot principal as reserved capital, not an extra profit. Daily1.5% equity halt. No real orders.',
            '',f"Funding mark-price fallbacks: {raw['funding_mark_fallbacks']}. Missing spot hours (union): {result['unavailable_spot_hours']}. Carry cannot enter/exit during those hours; last known spot closes are used only for provisional valuation while actual futures funding still settles. Deadlines may overrun until quotes resume, and drawdown during unavailable quotes is uncertain. {checked} basket ledgers reconciled. Detailed stage results and equity curves are saved in results.json and ledgers.json.",
            '', 'Sources: [pairs research](https://arxiv.org/abs/2109.10662), [BIS crypto carry](https://www.bis.org/publications/working-paper-1087-crypto-carry), [NBER crypto factors](https://www.nber.org/papers/w25882), [Engle-Granger API](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html), [spot commission example](https://www.binance.com/en-ZA/support/faq/detail/e85d6e703b874674840122196b89780a). These are adaptations, not published-strategy replications.']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(complete=True,diagnostics=diagnostics)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('download','run'))
    download() if p.parse_args().command=='download' else run()
