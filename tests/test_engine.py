from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scalp.cli import AccountLock
from scalp.engine import PaperEngine, Quote, summarize
from scalp.settings import Settings
from scalp.signals import Candle, Signal, aggregate_five, analyze
from scalp.research import replay


class TradingTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Settings()
        self.now = 1800000000.0
        self.a, self.b = self.cfg.symbols
        self.engine = PaperEngine(self.cfg)
        self.addCleanup(self.engine.journal.close)

    def quotes(self, now=None, price=100):
        return {s: Quote(price, price + 0.01, self.now if now is None else now) for s in self.cfg.symbols}

    def enter(self, side="BUY", symbol=None, now=None, engine=None):
        now = self.now if now is None else now
        e = engine or self.engine
        q = self.quotes(now)
        e.monitor(q, now)
        return e.enter(symbol or self.a, Signal(side, "test", .004), int(now - 60) * 1000, q, now)

    def test_multiple_positions_and_portfolio_caps(self):
        self.enter()
        self.enter("SELL", self.b)
        positions = self.engine.s["positions"]
        self.assertEqual(len(positions), 2)
        self.assertLessEqual(sum(p["risk_amount"] for p in positions.values()), 5)
        self.assertLessEqual(sum(p["qty"] * p["entry"] for p in positions.values()), 1000)

    def test_stop_exits_early_and_accounts_for_both_fees(self):
        self.enter()
        p = dict(self.engine.s["positions"][self.a])
        self.engine.monitor(self.quotes(self.now + 10, 99), self.now + 10)
        t = self.engine.s["trades"][0]
        self.assertEqual(t["exit_reason"], "STOP")
        self.assertLess(t["hold_minutes"], 5)
        self.assertAlmostEqual(t["net_pnl"], (t["exit"] - p["entry"]) * p["qty"] - t["fees"])
        self.assertAlmostEqual(self.engine.s["balance"], 1000 + t["net_pnl"])

    def test_short_target_and_closing_order_side(self):
        self.enter("SELL")
        events = self.engine.monitor(self.quotes(self.now + 10, 98), self.now + 10)
        self.assertEqual(events[0]["order_side"], "BUY")
        self.assertEqual(events[0]["trade"]["exit_reason"], "TARGET")
        self.assertGreater(events[0]["trade"]["net_pnl"], 0)

    def test_no_progress_exit_at_five_minutes(self):
        self.enter()
        self.engine.monitor(self.quotes(self.now + 299), self.now + 299)
        self.assertTrue(self.engine.s["positions"])
        self.engine.monitor(self.quotes(self.now + 300), self.now + 300)
        self.assertEqual(self.engine.s["trades"][0]["exit_reason"], "NO_FOLLOW_THROUGH")

    def test_hard_time_limit(self):
        self.enter()
        self.engine.monitor(self.quotes(self.now + 1799, 100.3), self.now + 1799)
        self.assertTrue(self.engine.s["positions"])
        self.engine.monitor(self.quotes(self.now + 1800, 100.3), self.now + 1800)
        self.assertEqual(self.engine.s["trades"][0]["exit_reason"], "TIME_LIMIT")

    def test_stale_data_blocks_entry_and_preserves_overdue_position(self):
        self.enter()
        events = self.engine.monitor(self.quotes(), self.now + 1900)
        self.assertTrue(events[0]["overdue"])
        self.assertTrue(self.engine.s["positions"])
        result = self.engine.enter(self.b, Signal("BUY", "test", .004), int(self.now + 1840) * 1000,
                                   self.quotes(), self.now + 1900)
        self.assertEqual(result["side"], "HOLD")
        self.engine.monitor(self.quotes(self.now + 1901, 100.3), self.now + 1901)
        self.assertIn("exit_delayed_by_data_or_downtime", self.engine.s["trades"][0]["diagnostics"])

    def test_restart_restores_deadline_and_deduplicates_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "paper.db")
            e = PaperEngine(self.cfg, path)
            self.enter(engine=e)
            deadline = e.s["positions"][self.a]["deadline"]
            e.journal.close()
            e = PaperEngine(self.cfg, path)
            self.assertEqual(e.s["positions"][self.a]["deadline"], deadline)
            self.assertIsNone(self.enter(engine=e))
            e.monitor(self.quotes(self.now + 1800, 100.3), self.now + 1800)
            self.assertFalse(e.s["positions"])
            e.journal.close()

    def test_rejects_upward_quantity_rounding(self):
        q = self.quotes()
        self.engine.monitor(q, self.now)
        with self.assertRaises(ValueError):
            self.engine.enter(self.a, Signal("BUY", "test", .004), int(self.now - 60) * 1000,
                              q, self.now, lambda s, size: size + 1)

    def test_exchange_minimum_does_not_inflate_risk(self):
        q = self.quotes()
        self.engine.monitor(q, self.now)
        r = self.engine.enter(self.a, Signal("BUY", "test", .004), int(self.now - 60) * 1000,
                              q, self.now, min_qty=10000)
        self.assertEqual(r["side"], "HOLD")
        self.assertFalse(self.engine.s["positions"])

    def test_daily_limit_latches_until_utc_rollover(self):
        e = PaperEngine(replace(self.cfg, daily_loss_fraction=.001))
        self.addCleanup(e.journal.close)
        self.enter(engine=e)
        e.monitor(self.quotes(self.now + 10, 99), self.now + 10)
        self.assertTrue(e.s["day_halted"])
        e.monitor(self.quotes(self.now + 86400), self.now + 86400)
        self.assertFalse(e.s["day_halted"])

    def test_loss_pause(self):
        for i in range(3):
            now = self.now + i * 360
            self.enter(now=now)
            self.engine.monitor(self.quotes(now + 10, 99.5), now + 10)
        self.assertGreater(self.engine.s["pause_until"], self.now + 720)

    def test_cost_feedback_halves_future_risk(self):
        e = PaperEngine(replace(self.cfg, feedback_sample=1))
        self.addCleanup(e.journal.close)
        self.enter(engine=e)
        e.monitor(self.quotes(self.now + 10, 99.5), self.now + 10)
        self.enter(now=self.now + 360, engine=e)
        self.assertEqual(e.s["positions"][self.a]["risk_multiplier"], .5)

    def test_profit_factor_uses_gross_sums_not_average_win_loss(self):
        self.enter()
        self.engine.monitor(self.quotes(self.now + 10, 99), self.now + 10)
        template = self.engine.s["trades"][0]
        trades = [dict(template, net_pnl=p) for p in (2, 2, -2)]
        self.assertEqual(summarize(trades)["profit_factor"], 2)

    def test_configuration_validation(self):
        for values in ({"max_hold_minutes": 31}, {"fee_bps": -1}, {"initial_balance": float("nan")},
                       {"max_positions": 1.2}, {"risk_fraction": .5}):
            with self.assertRaises(ValueError):
                Settings(**values)

    def test_nonfinite_crossed_and_future_quotes_are_rejected(self):
        for q in (Quote(float("nan"), 100, self.now), Quote(101, 100, self.now),
                  Quote(99, 100, self.now + 10), Quote(99, 100, self.now - 100)):
            self.assertFalse(q.valid(self.now, self.cfg.stale_seconds))

    def test_unclosed_candle_cannot_enter(self):
        q = self.quotes()
        self.engine.monitor(q, self.now)
        result = self.engine.enter(self.a, Signal("BUY", "test", .004), int(self.now) * 1000, q, self.now)
        self.assertEqual(result["side"], "HOLD")
        self.assertFalse(self.engine.s["positions"])

    def test_saved_account_rejects_settings_change(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "test.db")
            e = PaperEngine(self.cfg, path)
            e.monitor(self.quotes(), self.now)
            e.journal.close()
            with self.assertRaises(ValueError):
                PaperEngine(replace(self.cfg, initial_balance=2000), path)

    def test_shutdown_reduces_all_available_positions(self):
        self.enter()
        self.enter("SELL", self.b)
        events = self.engine.monitor(self.quotes(self.now + 1), self.now + 1, "SHUTDOWN")
        self.assertFalse(self.engine.s["positions"])
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e["reduce_only"] for e in events))

    def test_replay_without_checkpoints_has_identical_accounting(self):
        volatile = PaperEngine(self.cfg, persist=False)
        self.addCleanup(volatile.journal.close)
        for e in (self.engine, volatile):
            self.enter(engine=e)
            self.enter("SELL", self.b, engine=e)
            e.monitor(self.quotes(self.now + 10, 99), self.now + 10)
            e.monitor(self.quotes(self.now + 60), self.now + 60, "SHUTDOWN")
        self.assertEqual(self.engine.s, volatile.s)

    def test_account_lock_blocks_second_writer(self):
        with tempfile.TemporaryDirectory() as d:
            with AccountLock(str(Path(d) / "x.db")):
                with self.assertRaises(RuntimeError):
                    with AccountLock(str(Path(d) / "x.db")):
                        pass

    def test_only_complete_higher_timeframes(self):
        rows = [Candle(i * 60000, 100, 101, 99, 100, 10) for i in range(9)]
        self.assertEqual(len(aggregate_five(rows)), 1)

    def test_missing_candles_never_generate_trade(self):
        rows = [Candle(i * 60000, 100, 101, 99, 100, 10) for i in range(401) if i != 200]
        sig = analyze(rows, self.cfg)
        self.assertEqual(sig.side, "HOLD")
        self.assertIn("missing", sig.reason)

    def test_replay_next_bar_and_stop_wins_ambiguous_bar(self):
        rows = [Candle(int(self.now + i * 60) * 1000, 100, 100.1, 99.9, 100, 10) for i in range(4)]
        rows[1] = Candle(rows[1].ts, 100, 103, 97, 100, 10)
        cfg = replace(self.cfg, symbols=(self.a,))
        def fake_signal(history, settings):
            self.assertLess(history[-1].ts, rows[1].ts if len(history) == 1 else rows[len(history)].ts)
            return Signal("BUY", "test", .004)
        with patch("scalp.research.analyze", side_effect=fake_signal):
            result = replay({self.a: rows}, cfg)
        first = result["trades"][0]
        self.assertEqual(first["entry_time"], rows[1].ts / 1000)
        self.assertEqual(first["exit_reason"], "STOP")


if __name__ == "__main__":
    unittest.main()
