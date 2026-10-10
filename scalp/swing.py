"""Hourly swing research engine. No exchange execution or private API calls."""
from dataclasses import dataclass
import math
from .signals import ema

HOUR = 3600000


@dataclass(frozen=True)
class SwingConfig:
    method: str = 'breakout'
    atr_multiple: float = 2
    max_hours: int = 72
    fee: float = .0005
    slip: float = .0002
    half_spread: float = .0002

    def __post_init__(self):
        if self.method not in ('breakout','pullback') or self.atr_multiple <= 0 or self.max_hours not in (24,72):
            raise ValueError('Invalid swing configuration')
        if any(not math.isfinite(v) or v < 0 for v in (self.fee,self.slip,self.half_spread)):
            raise ValueError('Invalid costs')


def fill(mid, direction, cfg):
    return mid*(1+direction*cfg.half_spread)*(1+direction*cfg.slip)


def features(rows):
    """Feature at index i uses bars strictly before i, including complete 4h bars."""
    close=[r.close for r in rows]
    hourly=ema(close,20)
    four=[]; output={}
    for i,c in enumerate(rows):
        if i >= 4 and c.ts % (4*HOUR)==0:
            group=rows[i-4:i]
            if [r.ts for r in group]==list(range(c.ts-4*HOUR,c.ts,HOUR)):
                four.append(group[-1].close)
        if i < 200 or len(four)<50:
            continue
        fast=ema(four[-100:],20); slow=ema(four[-100:],50)
        trend=1 if fast[-1]>slow[-1] and fast[-1]>fast[-2] else -1 if fast[-1]<slow[-1] and fast[-1]<fast[-2] else 0
        prev=rows[i-1]
        atr=sum(max(b.high-b.low,abs(b.high-a.close),abs(b.low-a.close)) for a,b in zip(rows[i-15:i-1],rows[i-14:i]))/14
        upper=max(r.high for r in rows[i-21:i-1]); lower=min(r.low for r in rows[i-21:i-1])
        breakout=1 if prev.close>upper else -1 if prev.close<lower else 0
        pullback=1 if close[i-2]<=hourly[i-2] and close[i-1]>hourly[i-1] else -1 if close[i-2]>=hourly[i-2] and close[i-1]<hourly[i-1] else 0
        output[c.ts]=dict(trend=trend,breakout=breakout,pullback=pullback,atr_fraction=atr/prev.close)
    return output


def simulate(data, prepared, funding, cfg, start, end):
    symbols=sorted(data)
    indexed={s:{c.ts:c for c in rows if start<=c.ts<end} for s,rows in data.items()}
    times=sorted(indexed[symbols[0]])
    if not times or any(sorted(indexed[s])!=times for s in symbols):
        raise ValueError('Missing common hourly data')
    cash=1000.; positions={}; trades=[]; curve=[]; skipped={}; cooldown={}
    settlements={}
    for s,events in funding.items():
        for event in events:
            settlements.setdefault(event['ts']//HOUR*HOUR,[]).append((s,event))
    def equity(prices):
        return cash+sum(p['sign']*(fill(prices[s],-p['sign'],cfg)-p['entry'])*p['qty']
                        -fill(prices[s],-p['sign'],cfg)*p['qty']*cfg.fee for s,p in positions.items())
    def close(s,price,ts,reason):
        nonlocal cash
        p=positions.pop(s); fee=price*p['qty']*cfg.fee
        gross=p['sign']*(price-p['entry'])*p['qty']
        cash+=gross-fee
        trades.append(dict(p,symbol=s,exit=price,exit_time=ts/1000,exit_reason=reason,
                           exit_fee=fee,gross_pnl=gross,net_pnl=gross-p['entry_fee']-fee-p['funding_cost'],
                           hold_hours=(ts-p['opened'])/HOUR))
        cooldown[s]=ts+HOUR
    day=None; day_equity=1000.; blocked=False
    for ts in times:
        bars={s:indexed[s][ts] for s in symbols}; opens={s:c.open for s,c in bars.items()}
        if ts//86400000!=day:
            day=ts//86400000;day_equity=equity(opens);blocked=False
        for s,event in settlements.get(ts,[]):
            if s in positions and positions[s]['opened']<event['ts']:
                p=positions[s];cost=p['sign']*p['qty']*event['mark']*event['rate']
                cash-=cost;p['funding_cost']+=cost
        if equity(opens)<=day_equity*.985:
            blocked=True
        for s,p in list(positions.items()):
            f=prepared[s].get(ts,{})
            price=fill(opens[s],-p['sign'],cfg)
            reason=('STOP_GAP' if p['sign']*(price-p['stop'])<=0 else
                    'TARGET_GAP' if p['sign']*(price-p['target'])>=0 else
                    'DAILY_LOSS' if blocked else 'TIME_LIMIT' if ts-p['opened']>=cfg.max_hours*HOUR else
                    'TREND_INVALIDATION' if f.get('trend',p['sign'])!=p['sign'] else None)
            if reason: close(s,price,ts,reason)
        for s in symbols:
            f=prepared[s].get(ts)
            if blocked or s in positions or ts<cooldown.get(s,0) or not f:
                continue
            sign=f[cfg.method]
            if not sign or sign!=f['trend']:
                continue
            distance_fraction=max(.005,cfg.atr_multiple*f['atr_fraction'])
            if distance_fraction>.06:
                skipped['volatility']=skipped.get('volatility',0)+1;continue
            e=equity(opens)
            risk=max(0,min(e*.0025,e*.005-sum(p['risk'] for p in positions.values())))
            entry=fill(opens[s],sign,cfg);distance=entry*distance_fraction
            unit_risk=distance+entry*(2*cfg.fee+2*cfg.slip+2*cfg.half_spread)
            available=max(0,e-sum(p['qty']*opens[k] for k,p in positions.items()))
            qty=min(risk/unit_risk,available/entry)
            if qty*entry<5: continue
            fee=qty*entry*cfg.fee;cash-=fee
            positions[s]=dict(sign=sign,side='BUY' if sign==1 else 'SELL',entry=entry,qty=qty,
                              stop=entry-sign*distance,target=entry+sign*distance,risk=qty*unit_risk,
                              opened=ts,entry_time=ts/1000,entry_fee=fee,funding_cost=0.)
        # Executable barriers; adverse stop wins unknown intrabar ordering.
        for s,p in list(positions.items()):
            c=bars[s]; low=fill(c.low,-p['sign'],cfg);high=fill(c.high,-p['sign'],cfg)
            stop=low<=p['stop'] if p['sign']==1 else high>=p['stop']
            target=high>=p['target'] if p['sign']==1 else low<=p['target']
            if stop or target: close(s,p['stop'] if stop else p['target'],ts+HOUR//2,'STOP' if stop else 'TARGET')
        closes={s:c.close for s,c in bars.items()}
        marked=equity(closes)
        if marked<=day_equity*.985:
            blocked=True
            for s,p in list(positions.items()): close(s,fill(closes[s],-p['sign'],cfg),ts+HOUR-1,'DAILY_LOSS')
            marked=cash
        curve.append(dict(ts=ts+HOUR,equity=marked))
    for s,p in list(positions.items()):close(s,fill(indexed[s][times[-1]].close,-p['sign'],cfg),end-1,'END_OF_SAMPLE')
    curve[-1]['equity']=cash
    gains=sum(max(0,t['net_pnl']) for t in trades);losses=-sum(min(0,t['net_pnl']) for t in trades)
    peak=1000.;dd=0.
    for r in curve:peak=max(peak,r['equity']);dd=max(dd,(peak-r['equity'])/peak)
    net=sum(t['net_pnl'] for t in trades)
    assert abs(cash-1000-net)<1e-7
    return dict(summary=dict(trades=len(trades),net_pnl=net,return_pct=net/10,drawdown_pct=dd*100,
        profit_factor=gains/losses if losses else None,win_rate=sum(t['net_pnl']>0 for t in trades)/len(trades) if trades else 0,
        fees=sum(t['entry_fee']+t['exit_fee'] for t in trades),funding=sum(t['funding_cost'] for t in trades),
        average_hold_hours=sum(t['hold_hours'] for t in trades)/len(trades) if trades else 0),trades=trades,equity=curve,skipped=skipped)
