"""Frozen 1:1 price-distance momentum experiment: download then run."""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from .experiments import stamp, choose, day_bootstrap, fixed_trade_cost_stress
from .market import BinanceData
from .momentum import momentum_signal
from .research import replay
from .settings import Settings
from .signals import Candle

ROOT = Path('artifacts/round5')


def save(name, value):
    ROOT.mkdir(parents=True,exist_ok=True)
    (ROOT/name).write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def configs():
    return {n:replace(Settings(),strategy=n,reward_risk=1,reward_risk_basis='price',
                      min_hold_minutes=5,min_stop_fraction=.004,atr_stop_multiple=2,
                      max_daily_entries=6,daily_fee_budget_fraction=.003)
            for n in ('momentum5','momentum15')}


def protocol():
    return dict(start=stamp('2026-06-29'),split=stamp('2026-07-06'),
                validation=stamp('2026-07-13'),end=stamp('2026-07-27'),
                configs={n:asdict(c) for n,c in configs().items()},
                source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in (Path(__file__),Path('scalp/momentum.py'),Path('scalp/engine.py'))},
                selection='Maximum worst development-week net, then name; no validation reselection',
                acceptance='Both dev weeks >=10 trades and positive net; validation >=30 trades, PF>=1.15, positive net, bootstrap lower95>0, doubled-cost net>0',
                ratio='Exactly equal price distances from slipped entry; net reward/risk is lower after costs',
                execution='5bps taker each side, 4bps spread, 2bps slip each side, historical funding; next-bar entry, stop wins intrabar ties')


def download():
    plan = protocol()
    save('protocol.json',plan)
    market = BinanceData(Settings().symbols)
    raw = dict(data={},funding={})
    start,end = plan['start']-400*60000,plan['end']
    for symbol in Settings().symbols:
        rows = market.history(symbol,start,end)
        if [r.ts for r in rows] != list(range(start,end,60000)):
            raise ValueError('Incomplete history')
        raw['data'][symbol] = [asdict(r) for r in rows]
        raw['funding'][symbol] = market.funding_history(symbol,start,end)
        if not raw['funding'][symbol]:
            raise ValueError('Missing funding')
        print(json.dumps(dict(download=symbol,bars=len(rows))),flush=True)
    save('market_data.json',raw)


def run():
    plan = json.loads((ROOT/'protocol.json').read_text())
    if plan != json.loads(json.dumps(protocol())):
        raise ValueError('Protocol/source changed')
    blob = (ROOT/'market_data.json').read_bytes()
    raw = json.loads(blob)
    data = {s:[Candle(**r) for r in rows] for s,rows in raw['data'].items()}
    expected = list(range(plan['start']-400*60000,plan['end'],60000))
    if any([r.ts for r in data[s]] != expected for s in Settings().symbols):
        raise ValueError('Unexpected chronology')
    def evaluate(cfg,start,end):
        restricted = {s:[r for r in rows if r.ts < end] for s,rows in data.items()}
        signals = {(s,c.ts):momentum_signal(rows[max(0,i-65):i],cfg)
                   for s,rows in restricted.items() for i,c in enumerate(rows) if start <= c.ts < end}
        result = replay(restricted,cfg,start,end,signals=signals,funding=raw['funding'])
        # A bad ratio must fail the study, not merely appear in a report.
        for t in result['trades']:
            assert abs(abs(t['target']-t['entry'])-abs(t['stop']-t['entry'])) < 1e-8
        print(json.dumps(dict(method=cfg.strategy,start=start,summary=result['summary'])),flush=True)
        return result
    development = {n:[evaluate(c,a,b) for a,b in ((plan['start'],plan['split']),(plan['split'],plan['validation']))]
                   for n,c in configs().items()}
    selected = choose(development)
    frozen = dict(protocol=plan,selected=selected,development=development,dataset_sha256=hashlib.sha256(blob).hexdigest())
    save('selection_before_validation.json',frozen)
    validation = {n:evaluate(c,plan['validation'],plan['end']) for n,c in configs().items()}
    diagnostics = {n:dict(bootstrap=day_bootstrap(r['trades'],plan['validation'],plan['end']),
                         stress=fixed_trade_cost_stress(r['trades'],configs()[n])) for n,r in validation.items()}
    s = validation[selected]['summary']
    passed = (all(r['summary']['trades'] >= 10 and r['summary']['net_pnl'] > 0 for r in development[selected])
              and s['trades'] >= 30 and s['net_pnl'] > 0 and (s['profit_factor'] is None or s['profit_factor'] >= 1.15)
              and diagnostics[selected]['bootstrap']['lower95'] > 0 and diagnostics[selected]['stress']['net_pnl'] > 0)
    result = dict(frozen,validation=validation,diagnostics=diagnostics,passed=passed)
    save('results.json',result)
    for n,c in configs().items():
        save(n+'_settings.json',asdict(c))
    lines = ['# Momentum with 1:1 price targets','',f'Selected before validation: {selected}. Acceptance passed: {passed}.',
             '', 'Development June 29–July 13, 2026; untouched validation July 13–27 UTC. Each fold begins with 1,000 USDT.',
             '', '| Method | Dev week 1 net | Dev week 2 net | Validation trades | Net | Fees | Win rate | PF |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for n,r in validation.items():
        s=r['summary']; d=development[n]
        lines.append(f"| {n} | {d[0]['summary']['net_pnl']:.2f} | {d[1]['summary']['net_pnl']:.2f} | {s['trades']} | {s['net_pnl']:.2f} | {s['fees']:.2f} | {s['win_rate']:.1%} | {s['profit_factor']} |")
    lines += ['', 'Targets/stops are exactly 1:1 from the actual simulated entry. Commissions and funding are deducted separately; this is not net 1:1. Minimum distance 0.4%, maximum 0.8%, otherwise 2 times minute ATR. At 0.4% executable fill-price distances and approximately 0.10% round-trip commission, binary-outcome break-even is approximately 62.5% wins, ignoring funding and early exits. Spread/slippage affect entry and barrier reachability; do not subtract them again from an already executable stop/target payoff.',
              '', 'Five-minute weak-follow-through exit, hard 30-minute deadline; targets/stops may exit sooner. Risk 0.25% per position, total 0.5%, gross exposure <=1x equity, six entries/day and 0.3% daily commission budget. No limit-fill assumptions.',
              '', 'Signals: 5- or 15-minute signed log return exceeding max(0.15%,1.5 ATR/price), relative volume >=1.2, EMA9/21 alignment and EMA21 slope. Fresh threshold crossing or five-bar breakout; no entry beyond three ATR from EMA9. All calculations use closed bars only. Two fixed hypotheses; no parameter search.',
              '', 'Research motivation: [Wen et al., intraday momentum and reversal](https://www.sciencedirect.com/science/article/pii/S1062940822000833). These rules are original hypotheses, not a replication or a profitability claim.',
              '', 'Limitations: retrospective validation, assumed spread/slippage, no historical depth/partial fills/lot filters/taxes; daily bootstrap assumes independent days. No live orders or automatic default changes. Raw trades, rejection reasons, loss diagnostics and per-method cost stress are in results.json.']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(selected=selected,passed=passed)),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('download','run'))
    download() if parser.parse_args().command=='download' else run()
