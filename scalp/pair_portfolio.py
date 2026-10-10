"""Conservative one-basket pairs portfolio with timestamp-ordered funding."""
import math
from collections import Counter

HOUR=3600000


def select_pair(models,z_threshold):
    eligible=[]
    for m in models:
        movement=abs(m['z'])*m['sd']/(1+m['beta'])
        if z_threshold<=abs(m['z'])<=4 and movement>=.004:
            eligible.append((movement/max(m['half'],2),m['x'],m['y'],m))
    return sorted(eligible,key=lambda r:(-r[0],r[1],r[2]))[0][3] if eligible else None


def simulate(data,funding,features,window,z_threshold,start,end,fee=.0005,slip=.0002,spread=.0004,entry_delay=0):
    indexed={s:{c.ts:c for c in rows if start<=c.ts<end} for s,rows in data.items()}
    times=sorted(next(iter(indexed.values())))
    if not times or any(sorted(rows)!=times for rows in indexed.values()):raise ValueError('Unsynchronized prices')
    settlements={}
    for s,events in funding.items():
        for e in events:
            if start<=e['ts']<end:settlements.setdefault(e['ts']//HOUR*HOUR,[]).append((s,e))
    cash=1000.;p=None;trades=[];curve=[];pending=None;cooldown=0;day=None;blocked=False;day_equity=1000.
    def fill(mid,sign):return mid*(1+sign*spread/2)*(1+sign*slip)
    def mark(prices):
        if p is None:return 0.,0.,{}
        exits={s:fill(prices[s],-l['sign']) for s,l in p['legs'].items()}
        gross=sum(l['sign']*(exits[s]-l['entry'])*l['qty'] for s,l in p['legs'].items())
        fees=sum(exits[s]*l['qty']*fee for s,l in p['legs'].items())
        return gross,fees,exits
    def settle(events):
        nonlocal cash
        for s,e in sorted(events,key=lambda row:row[1]['ts']):
            if p is not None and p['opened']<e['ts'] and s in p['legs']:
                l=p['legs'][s];amount=l['sign']*l['qty']*e['mark']*e['rate']
                cash-=amount;p['funding_cost']+=amount
                p['funding_events'].append(dict(symbol=s,ts=e['ts'],cost=amount))
    def close(prices,ts,reason):
        nonlocal p,cash,cooldown
        gross,fees,exits=mark(prices);cash+=gross-fees
        for s,l in p['legs'].items():l.update(exit=exits[s],exit_fee=exits[s]*l['qty']*fee)
        net=gross-p['entry_fee']-fees-p['funding_cost']
        trades.append(dict(p,exit_time=ts/1000,exit_reason=reason,hold_hours=(ts-p['opened'])/HOUR,gross_pnl=gross,exit_fee=fees,net_pnl=net,
                           realized_loss_over_budget=net < -p['loss_budget']-1e-8))
        p=None;cooldown=ts+HOUR
    for ts in times:
        opens={s:rows[ts].open for s,rows in indexed.items()}
        gross,fees,_=mark(opens)
        if day!=ts//(24*HOUR):day=ts//(24*HOUR);day_equity=cash+gross-fees;blocked=False
        events=settlements.get(ts,[])
        settle([(s,e) for s,e in events if e['ts']==ts])
        gross,fees,_=mark(opens)
        if cash+gross-fees<=.985*day_equity:blocked=True
        models=features.get(ts,{}).get(window,[])
        if p:
            valid=any((m['x'],m['y'])==(p['model']['x'],p['model']['y']) for m in models)
            reason=('STOP' if gross<=-p['risk'] else 'TARGET' if gross>=p['risk'] else
                    'DAILY_LOSS' if blocked else 'TIME_LIMIT' if ts-p['opened']>=72*HOUR else
                    'RELATIONSHIP_INVALID' if ts in features and not valid else None)
            if reason:close(opens,ts,reason)
        if p is None and not blocked and ts>=cooldown:
            if pending is None and models:
                model=select_pair(models,z_threshold)
                if model:pending=dict(model=model,due=ts+entry_delay*HOUR,signal_time=ts)
            if pending and ts>=pending['due']:
                m=pending['model'];direction=-1 if m['z']>0 else 1
                weights={m['x']:-direction*m['beta']/(1+m['beta']),m['y']:direction/(1+m['beta'])}
                distance=abs(m['z'])*m['sd']/(1+m['beta']);budget=cash*.0025
                costs=2*fee+2*slip+spread;gross_budget=min(cash*.8,budget/(distance+costs))
                legs={s:dict(sign=1 if w>0 else -1,entry=fill(opens[s],1 if w>0 else -1),qty=gross_budget*abs(w)/opens[s]) for s,w in weights.items()}
                if all(l['entry']*l['qty']>=5 for l in legs.values()):
                    for l in legs.values():l['entry_fee']=l['entry']*l['qty']*fee
                    entry_fee=sum(l['entry_fee'] for l in legs.values());cash-=entry_fee
                    p=dict(legs=legs,model=m,opened=ts,entry_time=ts/1000,signal_time=pending['signal_time'],entry_fee=entry_fee,
                           risk=gross_budget*distance,loss_budget=budget,funding_cost=0.,funding_events=[])
                pending=None
        # Funding after entry / open exit belongs to the position actually held then.
        settle([(s,e) for s,e in events if ts<e['ts']<ts+HOUR])
        closes={s:rows[ts].close for s,rows in indexed.items()};gross,fees,_=mark(closes)
        if cash+gross-fees<=.985*day_equity:blocked=True
        if p and (abs(gross)>=p['risk'] or blocked):close(closes,ts+HOUR-1,'DAILY_LOSS' if blocked else 'TARGET' if gross>0 else 'STOP')
        gross,fees,_=mark(closes);curve.append(dict(ts=ts+HOUR,equity=cash+gross-fees))
    if p:close({s:rows[times[-1]].close for s,rows in indexed.items()},end-1,'END_OF_SAMPLE')
    curve[-1]['equity']=cash;net=sum(t['net_pnl'] for t in trades)
    assert abs(cash-1000-net)<1e-7
    peak=1000.;dd=0.
    for row in curve:peak=max(peak,row['equity']);dd=max(dd,(peak-row['equity'])/peak)
    gains=sum(max(t['net_pnl'],0) for t in trades);losses=-sum(min(t['net_pnl'],0) for t in trades)
    return dict(summary=dict(trades=len(trades),net_pnl=net,return_pct=net/10,drawdown_pct=dd*100,
        profit_factor=gains/losses if losses else None,fees=sum(t['entry_fee']+t['exit_fee'] for t in trades),funding_cost=sum(t['funding_cost'] for t in trades),
        losses_over_budget=sum(t['realized_loss_over_budget'] for t in trades),average_hold_hours=sum(t['hold_hours'] for t in trades)/len(trades) if trades else 0,
        exits=dict(Counter(t['exit_reason'] for t in trades))),trades=trades,equity=curve)
