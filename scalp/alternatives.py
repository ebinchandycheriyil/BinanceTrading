"""Research-only basket execution for pairs, funding carry and relative momentum."""
from dataclasses import dataclass
import math
import statistics
import numpy as np
from statsmodels.tsa.stattools import coint

HOUR=3600000


def pair_model(btc,eth):
    x=np.log(np.asarray(btc));y=np.log(np.asarray(eth))
    if len(x)<100 or np.std(x)<1e-8:return None
    beta=float(np.cov(x,y,ddof=0)[0,1]/np.var(x));intercept=float(np.mean(y)-beta*np.mean(x))
    residual=y-intercept-beta*x;sd=float(np.std(residual))
    if not .3<=beta<=3 or sd<1e-5:return None
    _,p,_=coint(y,x,trend='c',maxlag=1,autolag=None)
    lag=residual[:-1];change=np.diff(residual)
    slope=float(np.cov(lag,change,ddof=0)[0,1]/np.var(lag))
    phi=1+slope
    half=-math.log(2)/math.log(phi) if 0<phi<1 else float('inf')
    if p>=.05 or not 2<=half<=72:return None
    return dict(beta=beta,intercept=intercept,sd=sd,p=float(p),half=half)


def build_features(data,funding,windows=(168,336,672)):
    """All decisions use earlier closes/funding, daily at 00:00 UTC."""
    times=[c.ts for c in data['BTC-P']];closes={s:[c.close for c in rows] for s,rows in data.items()}
    prepared={};accepted=0
    for i,ts in enumerate(times):
        if ts%(24*HOUR) or i<max(windows)+1:continue
        daily=dict(pairs={},momentum={},carry={})
        for w in windows:
            m=pair_model(closes['BTC-P'][i-w:i],closes['ETH-P'][i-w:i])
            if m:
                m['z']=(math.log(closes['ETH-P'][i-1])-m['intercept']-m['beta']*math.log(closes['BTC-P'][i-1]))/m['sd']
                daily['pairs'][w]=m;accepted+=1
            ranks=[]
            for s,values in closes.items():
                if not s.endswith('-P'):continue
                # Approximate past quote turnover from OHLCV; no current-bar input.
                turnover=sum(c.close*c.volume for c in data[s][i-24:i])
                if turnover<10_000_000:continue
                moves=np.diff(np.log(values[i-w-1:i]))
                vol=float(np.std(moves))
                if vol>0:ranks.append((s,math.log(values[i-1]/values[i-w-1]),vol))
            daily['momentum'][w]=sorted(ranks,key=lambda r:(r[1],r[0]))
        for s in ('BTC-P','ETH-P'):
            daily['carry'][s]=sum(e['rate'] for e in funding[s] if ts-168*HOUR<=e['ts']<ts)/168*8760
        prepared[ts]=daily
    return prepared,accepted


def proposal(cfg,daily):
    family=cfg['family']
    if family=='pairs':
        m=daily['pairs'].get(cfg['window'])
        if not m or not cfg['z']<=abs(m['z'])<=4:return None
        direction=-1 if m['z']>0 else 1
        fraction=abs(m['z'])*m['sd']/(1+m['beta'])
        if fraction<.004:return None
        return dict(weights={'ETH-P':direction/(1+m['beta']),'BTC-P':-direction*m['beta']/(1+m['beta'])},
                    risk_fraction=fraction,model=m,max_hours=72)
    if family=='carry':
        s=max(daily['carry'],key=daily['carry'].get)
        if daily['carry'][s]<cfg['threshold']:return None
        return dict(weights={s:-.5,s.replace('-P','-S'):.5},risk_fraction=.005,
                    carry_symbol=s,max_hours=cfg['hold'],equal_quantity=True)
    ranks=daily['momentum'][cfg['window']]
    longs=[r for r in ranks if r[1]>0][-2:];shorts=[r for r in ranks if r[1]<0][:2]
    if len(longs)<2 or len(shorts)<2:return None
    weights={}
    for group,sign in ((longs,1),(shorts,-1)):
        total=sum(1/r[2] for r in group)
        weights.update({r[0]:sign*.5/r[2]/total for r in group})
    return dict(weights=weights,risk_fraction=.02,max_hours=cfg['hold'])


def price(mid,sign,stress=False):
    slip=.0004 if stress else .0002
    return mid*(1+sign*.0002)*(1+sign*slip)


def fee_rate(symbol,stress=False):return (.001 if symbol.endswith('-S') else .0005)*(2 if stress else 1)


def simulate(data,funding,prepared,cfg,start,end,stress=False,unavailable_spot=()):
    index={s:{c.ts:c for c in rows if start<=c.ts<end} for s,rows in data.items()}
    times=sorted(index['BTC-P'])
    if not times or any(sorted(v)!=times for v in index.values()):raise ValueError('Missing synchronized market bars')
    cash=1000.;position=None;trades=[];curve=[];settlements={};cooldown=0;day=None;day_equity=1000.;blocked=False
    unavailable_spot=set(unavailable_spot)
    for s,events in funding.items():
        for e in events:
            if start<=e['ts']<end:settlements.setdefault(e['ts']//HOUR*HOUR,[]).append((s,e))
    def liquidation(mid):
        if not position:return 0.,0.,{}
        exits={s:price(mid[s],-leg['sign'],stress) for s,leg in position['legs'].items()}
        gross=sum(leg['sign']*(exits[s]-leg['entry'])*leg['qty'] for s,leg in position['legs'].items())
        fees=sum(exits[s]*leg['qty']*fee_rate(s,stress) for s,leg in position['legs'].items())
        return gross,fees,exits
    def close(mid,ts,reason):
        nonlocal cash,position,cooldown
        gross,fees,exits=liquidation(mid);cash+=gross-fees
        for s,leg in position['legs'].items():leg.update(exit=exits[s],exit_fee=exits[s]*leg['qty']*fee_rate(s,stress))
        trades.append(dict(position,exit_time=ts/1000,hold_hours=(ts-position['opened'])/HOUR,
                           exit_reason=reason,gross_pnl=gross,exit_fee=fees,
                           net_pnl=gross-position['entry_fee']-fees-position['funding_cost']))
        position=None;cooldown=ts+HOUR
    for ts in times:
        mid={s:r[ts].open for s,r in index.items()}
        gross,exitfees,_=liquidation(mid)
        if day!=ts//(24*HOUR):day=ts//(24*HOUR);day_equity=cash+gross-exitfees;blocked=False
        for s,e in settlements.get(ts,[]):
            if position and position['opened']<e['ts'] and s in position['legs']:
                leg=position['legs'][s];cost=leg['sign']*leg['qty']*e['mark']*e['rate']
                cash-=cost;position['funding_cost']+=cost
        gross,exitfees,_=liquidation(mid)
        if cash+gross-exitfees<day_equity*.985:blocked=True
        if cfg['family']=='carry' and ts in unavailable_spot:
            # Last observed spot price is valuation-only. Never invent a fill.
            curve.append(dict(ts=ts+HOUR,equity=cash+gross-exitfees,stale_spot=True))
            continue
        if position:
            reason='STOP' if gross<=-position['risk'] else 'TARGET' if gross>=position['risk'] else 'DAILY_LOSS' if blocked else 'TIME_LIMIT' if ts-position['opened']>=position['max_hours']*HOUR else None
            daily=prepared.get(ts)
            if not reason and daily:
                if cfg['family']=='pairs':
                    if cfg['window'] not in daily['pairs']:reason='RELATIONSHIP_INVALID'
                elif cfg['family']=='carry':
                    if daily['carry'][position['carry_symbol']]<cfg['threshold']/2:reason='FUNDING_WEAK'
            if cfg['family']=='carry':
                leg=position['legs'][position['carry_symbol']]
                collateral=position['futures_collateral']+leg['sign']*(mid[position['carry_symbol']]-leg['entry'])*leg['qty']-position['funding_cost']
                if collateral<.1*mid[position['carry_symbol']]*leg['qty']:reason='MARGIN_BUFFER'
            if reason:close(mid,ts,reason)
        if not position and not blocked and ts>=cooldown and ts in prepared:
            signal=proposal(cfg,prepared[ts])
            if signal and cash>0:
                risk=cash*.0025
                # Reserve estimated roundtrip friction in position size, not just stop distance.
                costs=sum(abs(w)*(2*fee_rate(s,stress)+.0004+(.0008 if stress else .0004)) for s,w in signal['weights'].items())
                gross_budget=min(cash*.8,risk/(signal['risk_fraction']+costs))
                legs={s:dict(sign=1 if w>0 else -1,entry=price(mid[s],1 if w>0 else -1,stress),qty=gross_budget*abs(w)/mid[s]) for s,w in signal['weights'].items()}
                if signal.get('equal_quantity'):
                    qty=gross_budget/sum(mid[s] for s in legs)
                    for leg in legs.values():leg['qty']=qty
                if all(leg['qty']*leg['entry']>=5 for leg in legs.values()):
                    fees=sum(leg['qty']*leg['entry']*fee_rate(s,stress) for s,leg in legs.items())
                    for s,leg in legs.items():leg['entry_fee']=leg['qty']*leg['entry']*fee_rate(s,stress)
                    position=dict(opened=ts,entry_time=ts/1000,legs=legs,risk=gross_budget*signal['risk_fraction'],
                                  entry_fee=fees,funding_cost=0.,max_hours=signal['max_hours'],
                                  carry_symbol=signal.get('carry_symbol'),model=signal.get('model'))
                    if cfg['family']=='carry':
                        spot=sum(leg['entry']*leg['qty'] for s,leg in legs.items() if s.endswith('-S'))
                        position['futures_collateral']=cash-spot-fees
                    cash-=fees
        mids={s:r[ts].close for s,r in index.items()}
        gross,fees,_=liquidation(mids)
        if cash+gross-fees<day_equity*.985:blocked=True
        if position and (abs(gross)>=position['risk'] or blocked):close(mids,ts+HOUR-1,'DAILY_LOSS' if blocked else 'TARGET' if gross>0 else 'STOP')
        gross,fees,_=liquidation(mids);curve.append(dict(ts=ts+HOUR,equity=cash+gross-fees))
    if position:close({s:r[times[-1]].close for s,r in index.items()},end-1,'END_OF_SAMPLE')
    curve[-1]['equity']=cash;net=sum(t['net_pnl'] for t in trades)
    assert abs(net-(cash-1000))<1e-7
    peak=1000.;dd=0
    for r in curve:peak=max(peak,r['equity']);dd=max(dd,(peak-r['equity'])/peak)
    gains=sum(max(t['net_pnl'],0) for t in trades);losses=-sum(min(t['net_pnl'],0) for t in trades)
    return dict(summary=dict(trades=len(trades),net_pnl=net,return_pct=net/10,drawdown_pct=dd*100,
                profit_factor=gains/losses if losses else None,win_rate=sum(t['net_pnl']>0 for t in trades)/len(trades) if trades else 0,
                fees=sum(t['entry_fee']+t['exit_fee'] for t in trades),funding_cost=sum(t['funding_cost'] for t in trades)),
                trades=trades,equity=curve)
