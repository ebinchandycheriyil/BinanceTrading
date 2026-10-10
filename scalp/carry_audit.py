"""Reconcile this carry paper run and produce an auditable capability report."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='artifacts/carry/forward_paper.db')
    parser.add_argument('--output', default='artifacts/carry')
    args = parser.parse_args()
    root = Path(args.output); root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(args.db).resolve().as_uri()+'?mode=ro', uri=True)) as conn:
        state = json.loads(conn.execute('SELECT body FROM carry_state WHERE id=1').fetchone()[0])
        events = [json.loads(row[0]) for row in conn.execute('SELECT body FROM carry_events ORDER BY id')]
    cfg = state['settings']
    for trade in state['trades']:
        q = trade['qty']
        direct = q*trade['spot_exit']*(1-cfg['spot_fee'])-trade['gross_spot_qty']*trade['spot_entry']
        direct += q*(trade['future_entry']-trade['future_exit'])-trade['future_entry_fee']
        direct -= q*trade['future_exit']*cfg['future_fee']
        assert abs(direct-trade['cash_net']) < 1e-7
        assert abs(trade['economic_net']-direct+trade['capital_charge']) < 1e-7
    balance = cfg['initial_balance']+sum(t['cash_net'] for t in state['trades'])
    balance -= sum(p['entry_cost'] for p in state['positions'].values())
    assert abs(balance-state['balance']) < 1e-7
    checks = []; count = None
    for command in (['-m','unittest','discover','-s','tests','-q'], ['-m','compileall','-q','scalp','tests'], ['-m','pip','check']):
        run = subprocess.run([sys.executable, *command], capture_output=True, text=True)
        text = run.stdout+run.stderr
        checks.append(dict(command=command, exit_code=run.returncode, output=text))
        assert run.returncode == 0, text
        match = re.search(r'Ran (\d+) tests', text)
        if match:
            count = int(match.group(1))
    observations = [e for e in events if e['event'] == 'OBSERVATION']
    rejected = [e for e in events if e['event'] == 'REJECT']
    latest = {}
    for event in rejected:
        latest[event['contract']] = event
    stats = dict(observations=len(observations), paper_entries=sum(e['event']=='ENTRY' for e in events),
                 closed_baskets=len(state['trades']), open_baskets=len(state['positions']), balance=state['balance'],
                 rejection_counts=dict(Counter(reason for e in rejected for reason in e['reasons'])),
                 tests=count, checks=checks, accounting_reconciled=True,
                 sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('scalp').glob('carry*.py')})
    (root/'verification.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')
    (root/'forward_events.json').write_text(json.dumps(events, indent=2), encoding='utf-8')
    lines = ['# Automated carry paper workflow', '',
        'Implemented: public opportunity scanning, cost/depth/return gates, persistent paired paper entries, monitoring, automatic paper exits and loss diagnostics. No private API calls or real order routing.', '',
        f"Bounded public-market check: {len(observations)} snapshots, {stats['paper_entries']} paper entries, {len(state['trades'])} closed baskets, {len(state['positions'])} remaining open. Cash balance {state['balance']:.2f} USDT. The process stopped after the bounded test; no background monitor is running.", '',
        'This short run tests connectivity and decision plumbing. It does not measure future opportunity frequency, attainable real fills or profitability.', '']
    if observations:
        a, b = observations[0]['ts'], observations[-1]['ts']
        lines += ['Observation interval: '+datetime.fromtimestamp(a, timezone.utc).isoformat()+' to '+datetime.fromtimestamp(b, timezone.utc).isoformat()+'.', '']
    lines += ['| Contract | Observed basis | Estimated net surplus | Decision / reasons |', '|---|---:|---:|---|']
    for identity, row in sorted(latest.items()):
        estimate = row['estimate']; base = estimate.get('base')
        basis = f"{estimate['observed_basis_bps']/100:.3f}%" if 'observed_basis_bps' in estimate else 'unavailable'
        net = f"{base['estimated_net']:.2f} USDT" if base else 'no feasible size'
        lines.append(f"| {identity} | {basis} | {net} | {'; '.join(row['reasons'])} |")
    lines += ['', 'The basis is the futures bid premium over the spot ask, before costs and before reserving both spot cash and futures collateral. It is not an account return. Projected convergence APR is a screening scenario, not achieved strategy APR: early profit/loss exits can capture only part of the estimated convergence. A positive basis can still fail all economic gates.', '',
        '## Implemented rules', '',
        '- Public BTC/ETH spot and linear USDT quarterly futures only; contract discovery and exchange quantity filters are refreshed. Up to two baskets, at most one per underlying asset. A completed expiry contract is not re-entered.',
        '- 1,000 USDT paper capital; at most 40% reserved per basket and 80% in total, checked against projected post-entry liquidating equity. Reserve spot purchase cash plus 100% initial futures notional collateral. No borrowing or automatic collateral transfers.',
        '- Assumed spot commission 0.10%, futures commission 0.05%, slippage 0.02% per fill. Buy enough spot to cover its base-asset fee; hedge only retained tradable inventory, and conservatively write off rounding dust.',
        '- Four commissions, estimated exit spread/slippage, 0.10% residual-basis reserve, and a 4% annual opportunity charge on committed capital. Require at least 2 USDT estimated surplus and 10% annualized surplus after that charge, plus positive surplus under doubled fees/slippage/reserves. These are declared research hurdles, not optimized returns or promises.',
        '- Entry size is capped at 10% of displayed best-level liquidity. Fresh books, bounded capture skew and a fresh futures mark are required. Spot depth uses local request age because the endpoint has no exchange timestamp.',
        '- Planned exit one hour before expiry, with earlier profit/loss, collateral-buffer and daily-loss exits. Paper profit/loss thresholds are equal (1:1 economic P&L), capped at 0.5% of equity and 80% of projected surplus. Other exit rules and gaps mean realized outcomes differ.',
        '- Persist state, raw observations and all entry/rejection/exit events in SQLite. Failed exits remain pending across restart. Missing data never becomes a simulated fill. Expired unresolved positions require settlement reconciliation.',
        '- Show cash P&L separately from imputed capital cost and economic P&L. Loss tags describe cost drag, basis/execution loss and collateral exits; they do not automatically optimize strategy parameters.', '',
        '## Run', '', '```powershell',
        '.\\.venv\\Scripts\\python.exe -m scalp.carry_cli scan',
        '.\\.venv\\Scripts\\python.exe -m scalp.carry_cli paper --db carry_paper.db',
        '.\\.venv\\Scripts\\python.exe -m scalp.carry_cli report --db carry_paper.db',
        '.\\.venv\\Scripts\\python.exe -m scalp.carry_cli close --db carry_paper.db', '```', '',
        'The paper process runs until Ctrl+C unless --minutes is supplied. Stopping preserves open baskets; it does not keep monitoring after shutdown. Use the same config/account when resuming. close requests paper liquidation at fresh executable quotes and retains any unfilled exit as pending.', '',
        '## What is still required for real automation', '',
        'A repeatable positive forward sample, measured execution costs, partial-fill and one-leg recovery, authenticated fee/filter/account checks, exchange order reconciliation, native protection and explicit capital/loss limits. The current paired fills are atomic paper assumptions. Full initial collateral does not eliminate liquidation risk; the buffer trigger is not an exchange liquidation model. Exit basis and prices remain uncertain.', '',
        'This is a longer-horizon carry experiment, not a five-to-thirty-minute scalper. Continue collecting prospective observations without lowering hurdles to force trades. If meaningful net opportunities do not emerge, stop this branch.', '',
        f'Verification: {count} tests passed, compilation and dependency checks passed, saved cash ledger reconciled. Synthetic tests verify entry/exit/restart paths; the public run does not fabricate a trade when no opportunity qualifies.', '',
        '[Official sources and model limits](SOURCES.md) | [Raw forward events](forward_events.json) | [Verification](verification.json)']
    (root/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
