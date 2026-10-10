"""Bounded prospective PAPER trial with frozen rules and read-only status."""
import argparse
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from .carry import CarrySettings, quote_errors
from .carry_cli import load_config, save
from .carry_market import BinanceCarryData
from .carry_paper import CarryPaper
from .cli import AccountLock


def source_hashes():
    package = Path(__file__).resolve().parent
    files = sorted(package.glob('*.py'))
    if not files:
        raise ValueError('Cannot fingerprint the strategy package')
    return {'scalp/'+p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def stop_reason(root, protocol):
    if (root/'stop_requested.json').exists():
        return 'operator_request'
    try:
        if source_hashes() != protocol['source_hashes']:
            return 'source_changed'
        if json.loads((root/'settings.json').read_text()) != protocol['settings']:
            return 'settings_changed'
    except (OSError, ValueError):
        return 'frozen_files_unreadable'
    return None


def phase(protocol, now):
    if now >= protocol['drain_until']:
        return 'EXPIRED'
    if now >= protocol['end_at']:
        return 'DRAINING'
    if now >= protocol['entry_cutoff']:
        return 'MONITOR_ONLY'
    return 'RUNNING'


def prepare(root, cfg, hours, now=None):
    if not math.isfinite(hours) or not .5 <= hours <= 168:
        raise ValueError('Trial duration must be 0.5 to 168 hours')
    if root.exists():
        raise ValueError('Trial directory already exists; resume it instead of replacing its protocol')
    cfg.validate()
    now = time.time() if now is None else now
    root.mkdir(parents=True)
    protocol = dict(kind='PROSPECTIVE_CARRY_PAPER_OPERATIONAL_TRIAL', created_at=now,
                    start_at=now, entry_cutoff=now+hours*3600-1800,
                    end_at=now+hours*3600, drain_until=now+hours*3600+3600,
                    settings=asdict(cfg), source_hashes=source_hashes(),
                    purpose='Frozen operational trial, not full convergence return validation. End-of-trial exits are forced.',
                    policy='Stop entries for final 30 minutes. Close at deadline; retry pending exits up to one hour. No live orders.')
    save(root/'protocol.json', protocol)
    save(root/'settings.json', asdict(cfg))
    save(root/'status.json', dict(phase='PREPARED', updated_at=now, observations=0, errors=0,
                                  last_success_at=None, open_positions=0, entries=0, trades=0))
    return protocol


def status(root, now=None):
    now = time.time() if now is None else now
    state = json.loads((root/'status.json').read_text())
    protocol = json.loads((root/'protocol.json').read_text())
    age = max(0, now-state['updated_at'])
    active = state['phase'] in ('STARTING', 'RUNNING', 'MONITOR_ONLY', 'DRAINING')
    state.update(heartbeat_age_seconds=age, heartbeat_stale=active and age > 120,
                 data_age_seconds=None if state.get('last_success_at') is None else max(0, now-state['last_success_at']),
                 planned_end_utc=datetime.fromtimestamp(protocol['end_at'], timezone.utc).isoformat())
    database = root/'account.db'
    if database.exists():
        with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            conn.execute('BEGIN')
            row = conn.execute('SELECT body FROM carry_state WHERE id=1').fetchone()
            if row:
                account = json.loads(row[0])
                state.update(balance=account['balance'], open_positions=len(account['positions']),
                             trades=len(account['trades']), cash_pnl=sum(t['cash_net'] for t in account['trades']),
                             economic_pnl=sum(t['economic_net'] for t in account['trades']))
                state['entries'] = conn.execute("SELECT COUNT(*) FROM carry_events WHERE json_extract(body,'$.event')='ENTRY'").fetchone()[0]
    return state


def run(root):
    protocol = json.loads((root/'protocol.json').read_text())
    cfg = CarrySettings(**protocol['settings']); cfg.validate()
    if source_hashes() != protocol['source_hashes']:
        raise ValueError('Frozen source differs: do not restart this trial with changed code')
    if json.loads((root/'settings.json').read_text()) != protocol['settings']:
        raise ValueError('Frozen settings file differs from protocol')
    with AccountLock(root/'account.db'):
        state = json.loads((root/'status.json').read_text())
        if state['phase'] in ('COMPLETED', 'STOPPED_EARLY', 'UNRESOLVED'):
            raise ValueError('Trial already ended; inspect its report instead of restarting')
        account = CarryPaper(cfg, root/'account.db')
        market = BinanceCarryData()
        consecutive_errors = 0
        state.update(phase='STARTING', updated_at=time.time())
        save(root/'status.json', state)
        try:
            while True:
                cycle_start = time.monotonic()
                now = time.time()
                reason = stop_reason(root, protocol)
                if reason and not state.get('stop_at'):
                    state.update(stop_at=now, stop_reason=reason)
                phase_now = phase(protocol, now)
                drain_deadline = min(protocol['drain_until'], state.get('stop_at', protocol['end_at'])+3600)
                if now >= drain_deadline:
                    state['phase'] = 'UNRESOLVED' if account.s['positions'] else 'COMPLETED'
                    break
                # Stop immediately once an end/stop request has no remaining exposure.
                if (phase_now == 'DRAINING' or state.get('stop_at')) and not account.s['positions']:
                    state['phase'] = 'STOPPED_EARLY' if state.get('stop_at') else 'COMPLETED'
                    break
                try:
                    snapshot = market.snapshot()
                    now = time.time()  # Request latency must not extend the entry window.
                    reason = stop_reason(root, protocol)
                    if reason and not state.get('stop_at'):
                        state.update(stop_at=now, stop_reason=reason)
                    phase_now = phase(protocol, now)
                    closing = state.get('stop_at') is not None or phase_now in ('DRAINING', 'EXPIRED')
                    if now >= drain_deadline:
                        continue
                    events = account.cycle(snapshot, now, allow_entries=phase_now == 'RUNNING' and not closing,
                                           force_close=closing)
                    state['observations'] += 1
                    state['usable_contracts'] = sum(not quote_errors(c, now, cfg) for c in snapshot['contracts'])
                    if (not state['usable_contracts'] or snapshot.get('errors')
                            or any(e['event'] in ('DATA_HALT', 'SETTLEMENT_REQUIRED') for e in events)):
                        raise ValueError('Partial/stale market data or unresolved paper exit; inspect account event journal')
                    state['last_success_at'] = now
                    state['last_error'] = None
                    consecutive_errors = 0
                    save(root/'snapshot.json', snapshot)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    state['errors'] += 1; consecutive_errors += 1
                    state['last_error'] = str(exc)
                    account.commit(time.time(), [dict(event='TRIAL_DATA_ERROR', error=str(exc))])
                    if consecutive_errors >= 5 and not state.get('stop_at'):
                        state.update(stop_at=time.time(), stop_reason='five_consecutive_data_failures')
                state.update(phase='DRAINING' if state.get('stop_at') else phase(protocol, time.time()),
                             updated_at=time.time(), open_positions=len(account.s['positions']),
                             trades=len(account.s['trades']), balance=account.s['balance'],
                             consecutive_errors=consecutive_errors)
                save(root/'status.json', state)
                print(json.dumps(state, allow_nan=False), flush=True)
                time.sleep(max(.1, min(cfg.poll_seconds-(time.monotonic()-cycle_start),
                                      max(.1, protocol['drain_until']-time.time()))))
        except KeyboardInterrupt:
            state['phase'] = 'INTERRUPTED'
            raise
        except BaseException as exc:
            state.update(phase='FAILED', last_error=str(exc))
            raise
        finally:
            state.update(updated_at=time.time(), open_positions=len(account.s['positions']),
                         trades=len(account.s['trades']), balance=account.s['balance'])
            save(root/'status.json', state)
            save(root/'account_report.json', dict(account=account.s, trial=protocol, status=state))
            account.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run', 'status', 'stop'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--config', default='carry_settings.json')
    parser.add_argument('--hours', type=float, default=24.)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.root, load_config(args.config), args.hours)
    elif args.command == 'run':
        run(args.root); return
    elif args.command == 'stop':
        if not (args.root/'protocol.json').exists():
            parser.error('Trial does not exist')
        save(args.root/'stop_requested.json', dict(requested_at=time.time()))
        result = dict(stop_requested=True, note='Running worker will close paper positions using fresh data.')
    else:
        result = status(args.root)
    print(json.dumps(result, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
