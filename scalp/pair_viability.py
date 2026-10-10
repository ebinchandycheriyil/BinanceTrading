"""Bounded 28-pair viability check; freeze before 2022/2023 evaluation."""
import argparse
from dataclasses import asdict
import hashlib
import itertools
import json
from pathlib import Path

from .signals import Candle
from .experiments import stamp
from .pair_portfolio import HOUR,simulate
from .swing_sweep import period_changes
from .swing_study import bootstrap

ROOT=Path('artifacts/pair_viability');COINS=('BTC','ETH','BNB','XRP','ADA','LTC','LINK','BCH')
START=stamp('2020-07-01');SPLIT=stamp('2022-01-01');END=stamp('2024-01-01')


def save(name,value):
    ROOT.mkdir(parents=True,exist_ok=True);(ROOT/name).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def download():
    import ccxt
    old=json.loads(Path('artifacts/alternatives1/market_data.json').read_text())
    raw=dict(data={s:r for s,r in old['data'].items() if s.endswith('-P')},funding=old['funding'],funding_mark_fallbacks=old['funding_mark_fallbacks'])
    if (ROOT/'market_data.json').exists():raw=json.loads((ROOT/'market_data.json').read_text())
    market=ccxt.binanceusdm({'enableRateLimit':True,'timeout':15000});market.load_markets()
    for coin in COINS:
        key=coin+'-P';symbol=coin+'/USDT:USDT';cursor=raw['data'][key][-1]['ts']+HOUR
        if cursor>=END:continue
        rows={r['ts']:r for r in raw['data'][key]}
        while cursor<END:
            page=market.fetch_ohlcv(symbol,'1h',since=cursor,limit=1000)
            if not page:raise ValueError('Missing prices')
            for r in page:
                if SPLIT<=r[0]<END:rows[int(r[0])]=asdict(Candle(int(r[0]),*map(float,r[1:6])))
            next_cursor=int(page[-1][0])+HOUR
            if next_cursor<=cursor:raise ValueError('No pagination progress')
            cursor=next_cursor
        if sorted(rows)!=list(range(START-800*HOUR,END,HOUR)):raise ValueError('Hourly gap')
        events={e['ts']:e for e in raw['funding'][key]};cursor=SPLIT
        while cursor<END:
            page=market.fapiPublicGetFundingRate(dict(symbol=market.market(symbol)['id'],startTime=cursor,endTime=END-1,limit=1000))
            if not page:break
            for e in page:
                ts=int(e['fundingTime']);mark=e.get('markPrice')
                if not mark:mark=rows[ts//HOUR*HOUR]['open'];raw['funding_mark_fallbacks']+=1
                events[ts]=dict(ts=ts,mark=float(mark),rate=float(e['fundingRate']))
            next_cursor=int(page[-1]['fundingTime'])+1
            if next_cursor<=cursor:raise ValueError('Funding pagination stalled')
            cursor=next_cursor
            if len(page)<1000:break
        if max(events)<END-12*HOUR or any(b-a>12*HOUR for a,b in zip(sorted(events),sorted(events)[1:])):raise ValueError('Funding gap')
        raw['data'][key]=[rows[t] for t in sorted(rows)];raw['funding'][key]=[events[t] for t in sorted(events)]
        save('market_data.json',raw);print(json.dumps(dict(download=key,bars=len(rows))),flush=True)


def run():
    from .pair_screen import build_pair_features
    grid=[dict(window=w,z=z) for w,z in itertools.product((144,168,336),(1.75,2.25))]
    blob=(ROOT/'market_data.json').read_bytes();raw=json.loads(blob)
    data={s:[Candle(**r) for r in rows] for s,rows in raw['data'].items()}
    if any([r.ts for r in rows]!=list(range(START-800*HOUR,END,HOUR)) for rows in data.values()):raise ValueError('Timeline mismatch')
    plan=dict(grid=grid,development=[START,SPLIT],evaluation_years=[2022,2023],
              selection='Priority >=30 baskets; maximize weakest marked quarter, then total net, then name',
              evidence='New expanded-pair model; 2022/23 BTC/ETH years were previously used by directional models, so this is not globally untouched market data.',
              universe='Fixed eight surviving coins, 28 combinations; not full point-in-time universe',
              execution='Hourly observations, 5bps taker, 4bps spread, 2bps slippage/side, actual funding timestamp ordering; gross basket target/stop 1:1',
              hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),Path('scalp/pair_screen.py'),Path('scalp/pair_portfolio.py'))},data_sha256=hashlib.sha256(blob).hexdigest())
    save('protocol.json',plan)
    before={s:[r for r in rows if r.ts<SPLIT] for s,rows in data.items()}
    dev_features,dev_stats=build_pair_features(before,windows=(144,168,336))
    save('development_screen_stats.json',dev_stats)
    dev=[];ledgers={}
    def evaluate(cfg,start,end,features,label,**costs):
        r=simulate(data,raw['funding'],features,cfg['window'],cfg['z'],start,end,**costs)
        identity=f"{label}_w{cfg['window']}_z{cfg['z']}";ledgers[identity]=r
        row=dict(id=identity,config=cfg,summary=r['summary'],quarters=period_changes(r['equity'],'quarter'))
        print(json.dumps(row),flush=True);return row
    for cfg in grid:dev.append(evaluate(cfg,START,SPLIT,dev_features,'development'))
    selected=sorted(dev,key=lambda r:(r['summary']['trades']<30,-min(r['quarters'].values()),-r['summary']['net_pnl'],r['id']))[0]
    save('selection_before_evaluation.json',dict(selected=selected,development=dev,protocol=plan))
    all_features,stats=build_pair_features(data,windows=(selected['config']['window'],))
    save('evaluation_screen_stats.json',stats)
    evaluations=[]
    for year in (2022,2023):
        a,b=stamp(f'{year}-01-01'),stamp(f'{year+1}-01-01')
        for label,costs in (('base',{}),('double_costs',dict(fee=.001,slip=.0004)),('delayed_entry',dict(entry_delay=1))):
            evaluations.append(evaluate(selected['config'],a,b,all_features,f'{year}_{label}',**costs))
    uncertainty={row['id']:bootstrap(ledgers[row['id']]['equity']) for row in evaluations if '_base_' in row['id']}
    save('results.json',dict(protocol=plan,development=dev,selected=selected,evaluations=evaluations,uncertainty=uncertainty,
                             conclusion='Research-only; no automatic promotion',funding_mark_fallbacks=raw['funding_mark_fallbacks']))
    save('ledgers.json',ledgers)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('download','run'))
    download() if parser.parse_args().command=='download' else run()
