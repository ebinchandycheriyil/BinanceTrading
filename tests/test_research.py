from dataclasses import replace
import unittest
from unittest.mock import patch

from scalp.engine import PaperEngine, Quote
from scalp.experiments import cache_signals, choose, day_bootstrap, fixed_trade_cost_stress
from scalp.research import replay
from scalp.settings import Settings
from scalp.signals import Candle, Signal, cost_filter


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.cfg = replace(Settings(), symbols=("BTC/USDT:USDT",))
        self.symbol = self.cfg.symbols[0]
        self.now = 1800000000

    def engine(self, side):
        e = PaperEngine(self.cfg)
        self.addCleanup(e.journal.close)
        q = {self.symbol: Quote(100, 100.01, self.now)}
        e.monitor(q, self.now)
        e.enter(self.symbol, Signal(side, "test", .004), (self.now - 60) * 1000, q, self.now)
        return e

    def test_funding_direction_idempotency_and_net_accounting(self):
        for side, sign in (("BUY", 1), ("SELL", -1)):
            with self.subTest(side=side):
                e = self.engine(side)
                qty = e.s["positions"][self.symbol]["qty"]
                e.apply_funding(self.symbol, self.now + 10, .0001, 100)
                e.apply_funding(self.symbol, self.now + 10, .0001, 100)
                cost = qty * 100 * .0001 * sign
                self.assertAlmostEqual(e.s["positions"][self.symbol]["funding_cost"], cost)
                e.monitor({self.symbol: Quote(100, 100.01, self.now + 20)}, self.now + 20, "SHUTDOWN")
                t = e.s["trades"][0]
                self.assertAlmostEqual(t["net_pnl"], t["gross_pnl"] - t["fees"] - cost)
                self.assertAlmostEqual(e.s["balance"], 1000 + t["net_pnl"])

    def test_entry_at_settlement_boundary_does_not_pay_prior_funding(self):
        e = self.engine("BUY")
        e.apply_funding(self.symbol, self.now, .0001, 100)
        self.assertEqual(e.s["positions"][self.symbol]["funding_cost"], 0)

    def test_cost_screen_rejects_low_volatility_and_preserves_valid_signal(self):
        cfg = replace(self.cfg, min_hold_minutes=15, min_move_cost_ratio=2)
        small = Signal("BUY", "test", .004, {"atr_fraction": .0001})
        large = Signal("BUY", "test", .004, {"atr_fraction": .002})
        self.assertEqual(cost_filter(small, cfg, 4).side, "HOLD")
        self.assertEqual(cost_filter(large, cfg, 4).side, "BUY")
        self.assertEqual(cost_filter(small, self.cfg, 4).side, "BUY")

    def test_patience_does_not_exit_before_its_configured_window(self):
        e = PaperEngine(replace(self.cfg, min_hold_minutes=15))
        self.addCleanup(e.journal.close)
        q = {self.symbol: Quote(100, 100.01, self.now)}
        e.monitor(q, self.now)
        e.enter(self.symbol, Signal("BUY", "test", .004), (self.now - 60) * 1000, q, self.now)
        e.monitor({self.symbol: Quote(100, 100.01, self.now + 300)}, self.now + 300)
        self.assertTrue(e.s["positions"])
        e.monitor({self.symbol: Quote(100, 100.01, self.now + 900)}, self.now + 900)
        self.assertEqual(e.s["trades"][0]["exit_reason"], "NO_FOLLOW_THROUGH")

    def test_intrabar_stops_fill_at_executable_threshold_not_double_cost(self):
        rows = [Candle((self.now + i * 60) * 1000, 100, 103 if i == 1 else 100.1,
                       97 if i == 1 else 99.9, 100, 10) for i in range(3)]
        for side in ("BUY", "SELL"):
            with self.subTest(side=side), patch("scalp.research.analyze", return_value=Signal(side, "test", .004)):
                result = replay({self.symbol: rows}, self.cfg)
                trade = result["trades"][0]
                self.assertEqual(trade["exit_reason"], "STOP")
                self.assertAlmostEqual(trade["exit"], trade["stop"])
                self.assertAlmostEqual(trade["mae_r"], -1)

    def test_gap_stop_uses_open_price_not_ideal_stop(self):
        rows = [Candle((self.now + i * 60) * 1000, 100, 100.1, 99.9, 100, 10) for i in range(3)]
        rows[2] = Candle(rows[2].ts, 98, 98.1, 97.9, 98, 10)
        with patch("scalp.research.analyze", return_value=Signal("BUY", "test", .004)):
            trade = replay({self.symbol: rows}, self.cfg)["trades"][0]
        self.assertLess(trade["exit"], trade["stop"])

    def test_selection_uses_worst_development_fold(self):
        def folds(a, b):
            return [{"summary": {"net_pnl": a}}, {"summary": {"net_pnl": b}}]
        self.assertEqual(choose({"unstable": folds(100, -5), "stable": folds(1, 2)}), "stable")

    def test_day_bootstrap_includes_zero_trade_days(self):
        start = 0
        trade = {"exit_time": 100, "net_pnl": 10}
        result = day_bootstrap([trade], start, 2 * 86400000, samples=1000)
        self.assertEqual(result["mean_daily_net"], 5)
        self.assertEqual(len(result["daily_pnl"]), 2)

    def test_cached_signals_match_uncached_replay(self):
        rows = [Candle((self.now + i * 60) * 1000, 100, 100.1, 99.9, 100, 10) for i in range(4)]
        sig = Signal("BUY", "test", .004)
        cache = {(self.symbol, c.ts): sig for c in rows[1:]}
        with patch("scalp.research.analyze", return_value=sig):
            standard = replay({self.symbol: rows}, self.cfg)
        cached = replay({self.symbol: rows}, self.cfg, signals=cache)
        self.assertEqual(standard, cached)

    def test_future_bar_cannot_change_cached_current_signal(self):
        rows = [Candle((self.now + i * 60) * 1000, 100, 100.1, 99.9, 100, 10) for i in range(305)]
        current = rows[300].ts
        original = cache_signals({self.symbol: rows}, self.cfg, current, current + 60000)
        # A wildly different current (not closed) candle and all future candles
        # must have no effect on the signal available at its open.
        rows[300:] = [Candle(c.ts, 150, 160, 140, 155, 9999) for c in rows[300:]]
        modified = cache_signals({self.symbol: rows}, self.cfg, current, current + 60000)
        self.assertEqual(original, modified)

    def test_funding_replay_charges_carried_position_only(self):
        rows = [Candle((self.now + i * 60) * 1000, 100, 100.1, 99.9, 100, 10) for i in range(4)]
        funding = {self.symbol: [dict(ts=rows[2].ts, rate=.001, mark=100)]}
        with patch("scalp.research.analyze", return_value=Signal("BUY", "test", .004)):
            trade = replay({self.symbol: rows}, self.cfg, funding=funding)["trades"][0]
        self.assertAlmostEqual(trade["funding_cost"], trade["qty"] * .1)
        self.assertAlmostEqual(trade["net_pnl"], trade["gross_pnl"] - trade["fees"] - trade["funding_cost"])

    def test_fixed_trade_stress_cannot_profit_by_changing_trade_selection(self):
        for side in ("BUY", "SELL"):
            e = self.engine(side)
            e.monitor({self.symbol: Quote(100, 100.01, self.now + 20)}, self.now + 20, "SHUTDOWN")
            original = e.s["trades"]
            stress = fixed_trade_cost_stress(original, self.cfg)
            self.assertEqual(stress["trades"], len(original))
            self.assertLess(stress["net_pnl"], original[0]["net_pnl"])


if __name__ == "__main__":
    unittest.main()
