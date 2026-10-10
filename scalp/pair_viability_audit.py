"""Accounting audit and decision report for expanded-pair viability study."""
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from .pair_viability import ROOT


def audit_minute_report(raw, ledgers):
    report=json.loads((ROOT/'minute_execution.json').read_text())
    for name,digest in report['hashes'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    assert hashlib.sha256(Path('scalp/pair_minute_check.py').read_bytes()).hexdigest()==report['source_hash']
    count=0
    for label,run in report['runs'].items():
        originals=ledgers[label]['trades'];assert len(originals)==len(run['trades'])
        for original,minute in zip(originals,run['trades']):
            assert minute['opened']==original['opened']
            assert minute['original_exit_time']==round(original['exit_time']*1000)
            assert minute['opened']<=minute['minute_exit_time']<=minute['original_exit_time']
            gross=sum(leg['sign']*(minute['exit_fills'][symbol]-leg['entry'])*leg['qty']
                      for symbol,leg in original['legs'].items())
            exit_fee=sum(minute['exit_fills'][symbol]*leg['qty']*.0005
                         for symbol,leg in original['legs'].items())
            funding=sum(leg['sign']*leg['qty']*event['mark']*event['rate']
                        for symbol,leg in original['legs'].items() for event in raw['funding'][symbol]
                        if original['opened']<event['ts']<=minute['minute_exit_time'])
            assert abs(gross-minute['gross_pnl'])<1e-7
            assert abs(exit_fee-minute['exit_fee'])<1e-7
            assert abs(funding-minute['funding_cost'])<1e-7
            assert abs(gross-original['entry_fee']-exit_fee-funding-minute['minute_net'])<1e-7
            assert abs(minute['original_net']-original['net_pnl'])<1e-7
            count+=1
        assert abs(sum(t['minute_net'] for t in run['trades'])-run['summary']['minute_net'])<1e-7
        assert abs(run['summary']['original_net']-ledgers[label]['summary']['net_pnl'])<1e-7
    return report,count


def main():
    result=json.loads((ROOT/'results.json').read_text());plan=result['protocol']
    raw=json.loads((ROOT/'market_data.json').read_text());ledgers=json.loads((ROOT/'ledgers.json').read_text())
    for file,digest in plan['hashes'].items():assert hashlib.sha256(Path(file).read_bytes()).hexdigest()==digest
    assert hashlib.sha256((ROOT/'market_data.json').read_bytes()).hexdigest()==plan['data_sha256']
    count=0;exports=[]
    for label,run in ledgers.items():
        net=0.
        for t in run['trades']:
            gross=sum(l['sign']*(l['exit']-l['entry'])*l['qty'] for l in t['legs'].values())
            fees=sum(l['entry_fee']+l['exit_fee'] for l in t['legs'].values())
            assert abs(gross-fees-t['funding_cost']-t['net_pnl'])<1e-7
            assert t['hold_hours']<=72
            # Independent interval-based funding verification, not simulator buckets.
            actual=sum(l['sign']*l['qty']*e['mark']*e['rate'] for s,l in t['legs'].items()
                       for e in raw['funding'][s] if t['opened']<e['ts']<=round(t['exit_time']*1000))
            assert abs(actual-t['funding_cost'])<1e-7
            count+=1;net+=t['net_pnl']
            if '_base_' in label:
                exports.append(dict(run=label,pair=t['model']['x']+'/'+t['model']['y'],entry_time=t['entry_time'],exit_time=t['exit_time'],
                                    hold_hours=t['hold_hours'],exit_reason=t['exit_reason'],gross=gross,fees=fees,funding=t['funding_cost'],net=t['net_pnl']))
        assert abs(net-run['summary']['net_pnl'])<1e-7
        assert abs(run['equity'][-1]['equity']-1000-net)<1e-7
    with (ROOT/'evaluation_trades.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(exports[0]));writer.writeheader();writer.writerows(exports)
    minute,minute_count=audit_minute_report(raw,ledgers)
    checks=[];test_count=None
    for args in (['-m','unittest','discover','-s','tests','-q'],['-m','compileall','-q','scalp','tests'],['-m','pip','check']):
        run=subprocess.run([sys.executable,*args],capture_output=True,text=True)
        output=run.stdout+run.stderr;checks.append(dict(args=args,exit_code=run.returncode,output=output));assert run.returncode==0
        match=re.search(r'Ran (\d+) tests',output)
        if match:test_count=int(match.group(1))
    audit=dict(checks=checks,baskets_verified=count,minute_baskets_verified=minute_count,
               funding_checked_by_actual_holding_intervals=True,tests=test_count)
    (ROOT/'verification.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    lines=['# Trading research: viability decision','',
           '**Recommendation: stop expanding the current profit-seeking parameter search and do not deploy real capital. Preserve the code as a research tool. A different, explicitly bounded data or carry-screening project remains possible, but there is no demonstrated income-producing edge here.**','',
           '## Latest expanded-pair experiment','',
           'Eight coins (BTC, ETH, BNB, XRP, ADA, LTC, LINK, BCH), 28 pair combinations; daily liquidity checks, fixed regression orientation, Engle–Granger tests, 5% Benjamini–Hochberg correction per day/window, price-series ADF diagnostics, and residual half-life limits. Only one active basket. This is still a fixed survivor universe, not a historical listing universe.',
           '', 'Six fixed configurations developed on July 2020–December 2021. Frozen selected model: six-day formation window, 1.75-standard-deviation entry, expected gross convergence >=0.4% of gross notional, 72-hour deadline. Equal gross basket profit/loss guard; 0.25% planned stop-plus-cost budget. None reached the preferred 30 development trades, so selection was diagnostic.',
           '', '2022 and 2023 were evaluated as separate 1,000-USDT accounts with the frozen configuration. These years were previously examined for BTC/ETH directional strategies; they are new expanded-pair evaluations, not globally unseen market data.',
           '', '| Evaluation | Baskets | Net USDT | Return | Hourly marked DD |','|---|---:|---:|---:|---:|']
    for row in result['evaluations']:
        s=row['summary'];lines.append(f"| {row['id']} | {s['trades']} | {s['net_pnl']:.2f} | {s['return_pct']:.3f}% | {s['drawdown_pct']:.3f}% |")
    lines+=['', 'The delayed-entry variant shifts both entries by one hour; it is not a test of a failed or delayed second leg. Higher fees can change position sizes and exit timing. Small numerical improvements under stress do not prove execution robustness.',
            '', '## Finer execution check: actual one-minute candles','',
            'The original entries, quantities and latest exit deadlines were frozen. Gross 1:1 basket guards were checked at synchronized minute opens/closes, with commissions, spread, slippage and funding retained. This is an execution diagnostic on the same baskets, not a full strategy replay: no additional entries or portfolio resizing are simulated.',
            '', '| Evaluation | Baskets | Original hourly net USDT | Minute diagnostic net USDT | Earlier exits | Ambiguous baskets |',
            '|---|---:|---:|---:|---:|---:|']
    for label,run in minute['runs'].items():
        s=run['summary'];lines.append(f"| {label[:4]} | {s['baskets']} | {s['original_net']:.2f} | {s['minute_net']:.2f} | {s['earlier_exits']} | {s['ambiguous_baskets']} |")
    original_total=sum(r['summary']['original_net'] for r in minute['runs'].values())
    minute_total=sum(r['summary']['minute_net'] for r in minute['runs'].values())
    lines+=['',f'Total estimated profit across these separate yearly accounts fell from {original_total:.2f} to {minute_total:.2f} USDT. This is a sum of two diagnostics, not a compounded two-year portfolio return.',
            '', 'The main change was a 2022 ADA/LINK basket: the hourly simulation credited a target exit of +1.96 USDT, but minute observations reached its stop earlier, producing -2.43 USDT. Other earlier exits also reduced some apparent target overshoots.',
            '', 'Four baskets had possible adverse-path stop breaches within minute OHLC ranges. The two legs may hit their extremes at different times, so these are recorded as ambiguity, not invented fills. Minute endpoints still cannot establish actual synchronized fills or rule out subminute stops. The tiny remaining profit is not evidence of a deployable edge.',
            '', '## Evidence limits','',
            '- Nine base-case evaluation baskets across two years are insufficient to estimate a durable edge. Both weekly bootstrap intervals cross zero.',
            '- Daily multiple-testing correction does not remove all study-wide selection bias or guarantee future mean reversion.',
            '- Hourly observations demonstrably missed an earlier stop in the fixed-basket diagnostic. Full details are in minute_execution.json; even minute OHLC cannot reconstruct synchronized quote paths or actual fills.',
            f"- {raw['funding_mark_fallbacks']} of {sum(len(v) for v in raw['funding'].values())} funding marks are estimated with the settlement-hour futures open because the API did not supply the historical mark. Funding rates and timestamps are actual; exact funding amounts retain this approximation.",
            '- Historical order-book depth, exchange lot filters, partial fills, legging losses, liquidation and taxes are not modeled. Nominal five-USDT minimums are not a historical exchange-filter reconstruction.',
            '', '## What changed after the independent audit','',
            'The earlier basket simulator bucketed funding to hourly opens. Events a few milliseconds later could be assigned to the wrong position. The new pair simulator processes actual timestamps before/after entries and exits and has dedicated boundary tests. Recalculation of the earlier alternatives evaluation changed funding costs by less than 0.09 USDT per strategy; it did not change the conclusions.',
            '', 'The earlier three BTC/ETH evaluation trades also had hourly adverse-price envelopes that could cross their stops. Those bounds combine nonsynchronous leg extremes: they establish execution ambiguity, not proof that actual stops occurred. The prior small profit should not be treated as validated execution evidence.',
            '', '## Is this viable?','',
            'Building an automated research/trading system is technically viable. Repeatable net profitability for these strategies has not been established. Optimizing hundreds of nearby settings can find positive samples while leaving the economic edge absent.',
            '', 'As an income project, the present evidence does not justify more engineering or capital. For scale only, a hypothetical 10% annual net return on the simulated 1,000-USDT account would be 100 USDT per year before operating costs and taxes; this is arithmetic, not a forecast or an assumption about the user\'s actual capital. The observed pair returns are much smaller and statistically uncertain.',
            '', 'The constraints interact: low costs, adequate movement, reliable fills and sufficiently frequent opportunities all need to coexist. A 1:1 gross ratio does not create positive expectancy and yields unequal net payoffs after costs. Increasing leverage or loosening statistical filters to force trades would not resolve the missing evidence.',
            '', '## Outside-the-box alternatives worth considering','',
            '1. **An opportunity scanner rather than an always-trading bot.** Calculate net fixed-expiry spot/futures basis after four commissions, spread, financing, collateral reserve and a capital-return hurdle. Show an opportunity only when the margin remains worthwhile under stress. Fixed-expiry basis has a convergence mechanism; perpetual funding is variable and cannot lock in a return. Margin and venue risks remain.',
            '2. **Execution data research.** Record public book/trade events, estimate achievable fills and adverse selection, and test whether any signal survives those measured costs. Maker fees alone are not a strategy; unfilled orders and adverse selection must be counted. Do this only as a learning/data project unless an economically meaningful signal first emerges.',
            '3. **A genuinely different statistical model only after the above.** Copula-based relative value has published positive and negative results across variants. It is a possible hypothesis, not a reason to assume a more complex model will solve our losses. Keep model selection bounded and preserve a separate evaluation.',
            '', '## Research basis','',
            '- [Tadi and Kortchmeski: dynamic cointegration pairs](https://arxiv.org/abs/2109.10662): supports testing, with execution availability and delay assumptions, not a guarantee for this implementation.',
            '- [Fil and Kristoufek: cryptocurrency pairs](https://ieeexplore.ieee.org/document/9200323/): reported performance depends strongly on costs and execution windows.',
            '- [BIS: Crypto carry](https://www.bis.org/publications/working-paper-1087-crypto-carry): an economic source of returns, constrained by capital, margins and liquidation risk.',
            '- [Albers et al.: market microstructure and execution](https://arxiv.org/abs/2108.09750): passive execution requires empirical fill analysis.',
            '- [Copula-based crypto statistical arbitrage](https://link.springer.com/article/10.1186/s40854-024-00702-7): mixed variant outcomes; selected positive backtests do not establish our own edge.',
            '', 'Practical stop rule: no real deployment without positive results across separate regimes, realistic execution stress, adequate opportunity counts, and a frozen prospective paper trial. Given the completed negative/fragile tests, do not authorize another broad parameter sweep as the default next step.',
            '',f'Verification: {test_count} tests passed, compilation/dependency checks passed, {count} basket ledgers and {minute_count} minute diagnostics reconciled including independent funding holding-interval checks. Source and input hashes matched.',
            '', '[Evaluation trades](evaluation_trades.csv) | [Full results](results.json) | [Minute diagnostic](minute_execution.json) | [Verification](verification.json). No exchange orders or persistent trading process were started.']
    (ROOT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(audit))


if __name__=='__main__':main()
