"""Post-study accounting verification; does not change hypotheses or selection."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from .experiments import day_bootstrap, fixed_trade_cost_stress
from .flow_study import ROOT, candidates


def main():
    results = json.loads((ROOT/'results.json').read_text())
    plan = results['protocol']
    metrics = {}
    for name, result in results['validation'].items():
        trades, summary = result['trades'], result['summary']
        assert abs(sum(t['net_pnl'] for t in trades)-summary['net_pnl']) < 1e-8
        for t in trades:
            assert abs(t['gross_pnl']-t['entry_fee']-t['exit_fee']-t['funding_cost']-t['net_pnl']) < 1e-8
            assert t['hold_minutes'] <= 30
        metrics[name] = dict(
            same_trade_stress=fixed_trade_cost_stress(trades,candidates()[name]),
            daily_bootstrap=day_bootstrap(trades,plan['validation'],plan['end']),
            interpretation='No observations: zero bootstrap interval is not evidence' if not trades else 'Short retrospective sample')
    checks = []
    for args in (['-m','unittest','discover','-s','tests','-q'], ['-m','compileall','-q','scalp','tests'], ['-m','pip','check']):
        run = subprocess.run([sys.executable,*args],capture_output=True,text=True)
        checks.append(dict(args=args,exit_code=run.returncode,output=run.stdout+run.stderr))
        if run.returncode:
            raise RuntimeError(checks[-1])
    files = list(Path('scalp').glob('*.py')) + list(Path('tests').glob('*.py'))
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert hashes[str(Path('scalp/flow_study.py'))] == plan['signal_source_sha256']
    assert hashlib.sha256((ROOT/'market_data.json').read_bytes()).hexdigest() == results['dataset_sha256']
    audit = dict(checks=checks,source_sha256=hashes,metrics=metrics,accounting_verified=True,
                 default_changed=False,real_orders_sent=False)
    (ROOT/'verification.json').write_text(json.dumps(audit,indent=2),encoding='utf-8')
    lines = ['\n\n## Audit and interpretation', '',
             'All 59 functional tests, compilation and dependency checks passed. Each validation trade reconciles gross minus both commissions and funding to net; no hold exceeds 30 minutes. Source and input hashes match the frozen protocol.', '',
             'The ranked selection made zero trades, so its zero net and zero-width bootstrap interval provide no evidence of an edge. It failed minimum participation. No candidate is eligible.', '',
             '| Candidate | Doubled fee/slippage net | 95% interval for mean daily net |',
             '|---|---:|---|']
    for name, values in metrics.items():
        ci = values['daily_bootstrap']
        lines.append(f"| {name} | {values['same_trade_stress']['net_pnl']:.2f} | [{ci['lower95']:.2f}, {ci['upper95']:.2f}] |")
    lines += ['', 'Continuation produced -11.96 USDT before modeled spread/slippage, commissions and funding. Its 34 weak-follow-through exits lost 20.98 USDT net; 12 stops lost 17.68. Fees worsened a signal failure. The failed-breakout reversal had only one validation trade: +0.13 after execution drag, erased by 0.52 commission. Quiet-shock conditions never coincided in validation.', '',
              'Next distinct research direction: joint BTC/ETH residual mean reversion, using formal stationarity checks and a two-leg execution simulator with four charged fills. This was researched but not implemented or validated in this round. Full order-book imbalance would require collecting event data; it cannot be reconstructed from taker-volume bars.', '',
              'No post-validation parameter tuning, automated promotion, or real exchange orders.']
    path = ROOT/'REPORT.md'
    original = path.read_text(encoding='utf-8').split('\n\n## Audit and interpretation')[0]
    path.write_text(original+'\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(accounting_verified=True,checks_passed=len(checks),metrics={n:m['same_trade_stress']['net_pnl'] for n,m in metrics.items()})))


if __name__ == '__main__':
    main()
