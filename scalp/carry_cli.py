"""Public carry scanner and persistent paper automation; no real order routing."""
import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import time

from .carry import CarrySettings, evaluate
from .carry_market import BinanceCarryData
from .carry_paper import CarryPaper
from .cli import AccountLock


def output(row):
    print(json.dumps(row, allow_nan=False), flush=True)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def load_config(path):
    cfg = CarrySettings(**json.loads(Path(path).read_text())) if path else CarrySettings()
    cfg.validate()
    return cfg


def paper(args, cfg):
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    market = BinanceCarryData()
    start = time.monotonic()
    observations = 0
    with AccountLock(args.db):
        account = CarryPaper(cfg, args.db, emit=output)
        output(dict(event='START', mode='CARRY_PAPER', settings=asdict(cfg),
                    db=str(Path(args.db).resolve()), close_on_stop=False))
        try:
            while True:
                cycle_start = time.monotonic()
                try:
                    snapshot = market.snapshot()
                    save(args.snapshot, snapshot)
                    account.cycle(snapshot, time.time(), allow_entries=args.command != 'close',
                                  force_close=args.command == 'close')
                    observations += 1
                except (OSError, ValueError, KeyError) as exc:
                    # Existing baskets remain persisted; missing prices never become fills.
                    account.commit(time.time(), [dict(event='DATA_HALT', error=str(exc),
                                                      remaining_open=len(account.s['positions']))])
                if args.once or args.command == 'close' or (args.minutes and time.monotonic()-start >= args.minutes*60):
                    break
                delay = cfg.poll_seconds-(time.monotonic()-cycle_start)
                if args.minutes:
                    delay = min(delay, args.minutes*60-(time.monotonic()-start))
                time.sleep(max(.1, delay))
        except KeyboardInterrupt:
            output(dict(event='INTERRUPT', mode='CARRY_PAPER'))
        finally:
            report = dict(mode='CARRY_PAPER', observations=observations, account=account.s,
                          note='Open paper baskets persist. Monitoring resumes only while the process runs.')
            save(args.output, report)
            output(dict(event='STOPPED', observations=observations, remaining_open=len(account.s['positions']),
                        report=str(Path(args.output).resolve()), monitoring_active=False))
            account.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', help='CarrySettings JSON; never reads .env')
    sub = parser.add_subparsers(dest='command', required=True)
    scan = sub.add_parser('scan', help='Discover dated futures and calculate net carry; no account mutations')
    scan.add_argument('--output', default='artifacts/carry/scan.json')
    scan.add_argument('--snapshot', default='artifacts/carry/current_snapshot.json')
    for name in ('paper', 'close'):
        run = sub.add_parser(name)
        run.add_argument('--db', default='carry_paper.db')
        run.add_argument('--once', action='store_true')
        run.add_argument('--minutes', type=float, default=0., help='0 monitors until Ctrl+C; stopping preserves baskets')
        run.add_argument('--snapshot', default='artifacts/carry/current_snapshot.json')
        run.add_argument('--output', default='artifacts/carry/paper_report.json')
    report = sub.add_parser('report', help='Read saved paper state; not a fresh valuation')
    report.add_argument('--db', default='carry_paper.db')
    report.add_argument('--output', default='artifacts/carry/paper_report.json')
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.command in ('paper', 'close'):
        if not math.isfinite(args.minutes) or args.minutes < 0:
            parser.error('--minutes must be finite and nonnegative')
        paper(args, cfg)
    elif args.command == 'scan':
        snapshot = BinanceCarryData().snapshot()
        now = time.time()
        evaluations = [evaluate(c, now, cfg) for c in snapshot['contracts']]
        result = dict(mode='CARRY_SCAN', ts=now, settings=asdict(cfg), evaluations=evaluations,
                      errors=snapshot['errors'], qualified=sum(e['decision'] == 'QUALIFIED' for e in evaluations),
                      assumptions=['Projected convergence before expiry is uncertain.',
                                   'Fees/slippage/capital-charge thresholds are explicit assumptions, not optimized parameters.',
                                   'Spot fees deducted in base; excess dust written off. Full initial futures collateral reserved.',
                                   'Entry bid/ask are observed; future exit quotes and basis are estimated.',
                                   'No private calls, exchange orders or settlement simulation.'])
        save(args.snapshot, snapshot)
        save(args.output, result)
        for estimate in evaluations:
            output(estimate)
        output(dict(event='SCAN_COMPLETE', contracts=len(evaluations), qualified=result['qualified'],
                    errors=result['errors'], report=str(Path(args.output).resolve())))
    else:
        if not Path(args.db).exists():
            parser.error('Carry account does not exist')
        with AccountLock(args.db):
            account = CarryPaper(cfg, args.db)
            try:
                save(args.output, dict(mode='CARRY_PAPER', account=account.s,
                                      note='Saved state only; last_mark may be stale.'))
                output(dict(event='REPORT', positions=len(account.s['positions']), trades=len(account.s['trades']),
                            balance=account.s['balance'], path=str(Path(args.output).resolve())))
            finally:
                account.close()


if __name__ == '__main__':
    main()
