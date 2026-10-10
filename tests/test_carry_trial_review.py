"""Independent bounded-trial failure and deadline checks; no network calls."""
from contextlib import ExitStack, closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scalp import carry_trial
from scalp.carry import CarrySettings
from scalp.carry_cli import save
from scalp.carry_paper import CarryPaper
from scalp.cli import AccountLock
from tests.test_carry_review import NOW, contract, fresh


class Clock:
    def __init__(self, now=NOW, sleep_jump=None):
        self.now = now
        self.sleep_jump = sleep_jump

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += self.sleep_jump if self.sleep_jump is not None else seconds


class CarryTrialIndependentTests(unittest.TestCase):
    def prepare(self, root):
        with patch.object(carry_trial, 'source_hashes', return_value={'frozen': 'test'}):
            return carry_trial.prepare(root, CarrySettings(), 1., NOW)

    def run_mock(self, root, clock, fetch):
        class Market:
            def snapshot(self):
                return fetch()
        with ExitStack() as stack:
            stack.enter_context(patch.object(carry_trial, 'source_hashes', return_value={'frozen': 'test'}))
            stack.enter_context(patch.object(carry_trial, 'BinanceCarryData', Market))
            stack.enter_context(patch.object(carry_trial.time, 'time', clock.time))
            stack.enter_context(patch.object(carry_trial.time, 'monotonic', clock.time))
            stack.enter_context(patch.object(carry_trial.time, 'sleep', clock.sleep))
            stack.enter_context(patch('builtins.print'))
            carry_trial.run(root)
        return json.loads((root/'status.json').read_text())

    def entries(self, root):
        with closing(sqlite3.connect(root/'account.db')) as conn:
            return conn.execute("SELECT count(*) FROM carry_events WHERE json_extract(body,'$.event')='ENTRY'").fetchone()[0]

    def test_phase_boundaries_are_inclusive(self):
        p = dict(entry_cutoff=10., end_at=20., drain_until=30.)
        self.assertEqual(carry_trial.phase(p, 9.999), 'RUNNING')
        self.assertEqual(carry_trial.phase(p, 10.), 'MONITOR_ONLY')
        self.assertEqual(carry_trial.phase(p, 20.), 'DRAINING')
        self.assertEqual(carry_trial.phase(p, 30.), 'EXPIRED')

    def test_prepare_cannot_replace_frozen_protocol(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'trial'; self.prepare(root)
            original = (root/'protocol.json').read_bytes()
            with self.assertRaises(ValueError):
                carry_trial.prepare(root, CarrySettings(), 2., NOW+1)
            self.assertEqual((root/'protocol.json').read_bytes(), original)

    def test_fetch_crossing_end_cannot_open_a_position(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'trial'; p = self.prepare(root); clock = Clock()
            def fetch():
                clock.now = p['end_at']+1
                return dict(contracts=[fresh(contract(), clock.now)], errors=[])
            state = self.run_mock(root, clock, fetch)
            self.assertEqual(self.entries(root), 0)
            self.assertEqual(state['phase'], 'COMPLETED')

    def test_fetch_crossing_entry_cutoff_remains_monitor_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'trial'; p = self.prepare(root)
            clock = Clock(p['entry_cutoff']-1, sleep_jump=1900.)
            def fetch():
                clock.now = p['entry_cutoff']+1
                return dict(contracts=[fresh(contract(), clock.now)], errors=[])
            state = self.run_mock(root, clock, fetch)
            self.assertEqual(self.entries(root), 0)
            self.assertEqual(state['phase'], 'COMPLETED')

    def test_stop_arriving_during_fetch_blocks_new_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'trial'; self.prepare(root); clock = Clock()
            def fetch():
                clock.now += 1
                save(root/'stop_requested.json', dict(requested_at=clock.now))
                return dict(contracts=[fresh(contract(), clock.now)], errors=[])
            state = self.run_mock(root, clock, fetch)
            self.assertEqual(self.entries(root), 0)
            self.assertEqual(state['phase'], 'STOPPED_EARLY')

    def test_unusable_idle_quotes_count_toward_failure_latch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'trial'; self.prepare(root); clock = Clock()
            def fetch():
                return dict(contracts=[fresh(contract(), clock.now-60)], errors=[])
            state = self.run_mock(root, clock, fetch)
            self.assertEqual(state['phase'], 'STOPPED_EARLY')
            self.assertEqual(state['errors'], 5)
            self.assertIsNone(state['last_success_at'])
            self.assertEqual(self.entries(root), 0)

    def test_grace_expiry_leaves_unresolved_without_inventing_fill(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'trial'; p = self.prepare(root)
            account = CarryPaper(CarrySettings(), root/'account.db')
            account.cycle(dict(contracts=[contract()], errors=[]), NOW)
            account.close()
            clock = Clock(p['end_at']+1, sleep_jump=1000.)
            def fetch():
                c = fresh(contract(), clock.now); c['spot']['bid_size'] = .001
                return dict(contracts=[c], errors=[])
            state = self.run_mock(root, clock, fetch)
            self.assertEqual(state['phase'], 'UNRESOLVED')
            self.assertEqual(state['open_positions'], 1)
            self.assertEqual(state['trades'], 0)
            self.assertGreaterEqual(clock.now, p['drain_until'])

    def test_status_reads_active_locked_account_and_flags_stale_heartbeat(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'trial'; self.prepare(root)
            with AccountLock(root/'account.db'):
                account = CarryPaper(CarrySettings(), root/'account.db')
                try:
                    account.cycle(dict(contracts=[contract()], errors=[]), NOW)
                    state = json.loads((root/'status.json').read_text())
                    state.update(phase='RUNNING', updated_at=NOW, last_success_at=NOW)
                    save(root/'status.json', state)
                    result = carry_trial.status(root, NOW+121)
                    self.assertTrue(result['heartbeat_stale'])
                    self.assertEqual(result['open_positions'], 1)
                    self.assertEqual(result['entries'], 1)
                    self.assertEqual(result['trades'], 0)
                finally:
                    account.close()


if __name__ == '__main__':
    unittest.main()
