from dataclasses import replace
import unittest

from scalp.chart_setups import all_chart_signals, plans
from scalp.costs import FeeSchedule, cost_scenarios
from scalp.engine import PaperEngine, Quote
from scalp.settings import Settings
from scalp.signals import Candle, Signal, cost_filter


class CostTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000
        self.cfg = Settings()
        self.a, self.b = self.cfg.symbols

    def quotes(self, now=None):
        return {s: Quote(100, 100.01, now or self.now) for s in self.cfg.symbols}

    def account(self, **changes):
        e = PaperEngine(replace(self.cfg, **changes))
        self.addCleanup(e.journal.close)
        e.monitor(self.quotes(), self.now)
        return e

    def enter(self, engine, signal=None, symbol=None, now=None):
        now = now or self.now
        return engine.enter(symbol or self.a, signal or Signal("BUY", "test", .004),
                            (now - 60) * 1000, self.quotes(now), now)

    def test_commission_on_both_notionals_and_not_margin(self):
        fees = FeeSchedule()
        self.assertAlmostEqual(fees.round_trip(10, 100, 100), 1)
        self.assertAlmostEqual(fees.round_trip(10, 100, 100, "maker", "taker"), .7)
        self.assertAlmostEqual(fees.round_trip(10, 100, 100, "maker", "maker"), .4)
        self.assertAlmostEqual(fees.round_trip(10, 100, 110), 1.05)
        self.assertAlmostEqual(51 * fees.round_trip(10, 100, 100), 51)

    def test_invalid_fee_inputs_fail(self):
        with self.assertRaises(ValueError):
            FeeSchedule(taker_bps=float("nan"))
        with self.assertRaises(ValueError):
            FeeSchedule().commission(1, 100, "limit")

    def test_daily_entry_cap_applies_across_symbols(self):
        e = self.account(max_daily_entries=1)
        self.enter(e)
        e.monitor(self.quotes(self.now + 10), self.now + 10, "SHUTDOWN")
        event = self.enter(e, symbol=self.b, now=self.now + 60)
        self.assertEqual(event["reason"], "daily entry limit")

    def test_commission_budget_reserves_exit_before_entry(self):
        e = self.account(daily_fee_budget_fraction=.0003)  # $0.30: entry fits, round trip doesn't
        event = self.enter(e)
        self.assertEqual(event["reason"], "daily commission budget")
        self.assertFalse(e.s["positions"])

    def test_structural_objectives_survive_entry_without_target_inflation(self):
        e = self.account(min_target_cost_ratio=2, min_net_reward_risk=1)
        self.enter(e, Signal("BUY", "test", .004, {}, 99.6, 101))
        p = e.s["positions"][self.a]
        self.assertAlmostEqual(p["stop"], 99.6)
        self.assertAlmostEqual(p["target"], 101)
        self.assertGreaterEqual(p["planned_net_reward_risk"], 1)

    def test_small_target_rejected_after_costs(self):
        e = self.account(min_target_cost_ratio=2)
        event = self.enter(e, Signal("BUY", "test", .004, {}, 99.6, 100.2))
        self.assertEqual(event["reason"], "target too small after round-trip costs")
        self.assertFalse(e.s["positions"])

    def test_low_net_reward_risk_rejected_even_with_positive_target(self):
        e = self.account(min_net_reward_risk=1)
        event = self.enter(e, Signal("BUY", "test", .004, {}, 99.6, 100.5))
        self.assertEqual(event["reason"], "net reward/risk below minimum")

    def test_gap_beyond_planned_target_rejected(self):
        e = self.account()
        event = self.enter(e, Signal("BUY", "test", .004, {}, 99.6, 100.02))
        self.assertEqual(event["reason"], "entry gap invalidated structural setup")

    def test_exact_fee_execution_funding_ledger(self):
        e = self.account()
        self.enter(e)
        e.apply_funding(self.a, self.now + 5, .0001, 100)
        e.monitor(self.quotes(self.now + 10), self.now + 10, "SHUTDOWN")
        t = e.s["trades"][0]
        self.assertAlmostEqual(t["fees"], t["entry_fee"] + t["exit_fee"])
        self.assertAlmostEqual(t["gross_before_execution_costs"] - t["spread_slippage_cost"] - t["fees"] - t["funding_cost"], t["net_pnl"])
        self.assertAlmostEqual(cost_scenarios([t])["taker_taker"]["net"], t["net_pnl"])

    def test_protective_exit_is_not_blocked_by_commission_or_entry_caps(self):
        e = self.account(max_daily_entries=1, daily_fee_budget_fraction=.001)
        self.enter(e)
        self.assertTrue(e.s["positions"])
        # A gap requires closure even though costs/realized loss can overshoot
        # the planned account budget. Caps must apply only to new exposure.
        q = {s: Quote(90, 90.01, self.now + 10) for s in self.cfg.symbols}
        e.monitor(q, self.now + 10)
        self.assertFalse(e.s["positions"])
        self.assertEqual(e.s["trades"][0]["exit_reason"], "STOP")

    def test_daily_entry_counter_resets_on_new_utc_day(self):
        e = self.account(max_daily_entries=1)
        self.enter(e)
        e.monitor(self.quotes(self.now + 10), self.now + 10, "SHUTDOWN")
        tomorrow = self.now + 86400
        e.monitor(self.quotes(tomorrow), tomorrow)
        event = self.enter(e, now=tomorrow)
        self.assertEqual(event["event"], "ENTRY")

    def test_cost_filter_preserves_structural_stop_and_target(self):
        signal = Signal("BUY", "test", .004, {"atr_fraction": .01}, 99.6, 101)
        filtered = cost_filter(signal, replace(self.cfg, min_move_cost_ratio=2), 4)
        self.assertEqual(filtered.stop_price, 99.6)
        self.assertEqual(filtered.target_price, 101)


class ChartTests(unittest.TestCase):
    def context(self):
        bars = [Candle(i * 300000, 100, 101, 99, 100, 10) for i in range(60)]
        return dict(bars=bars, atr=1, adx=15, ef=[100] * 60, es=[100] * 60,
                    center=100, sd=1, prev_center=100, prev_sd=1,
                    volume_ratio=1, prev_volume_ratio=2)

    def test_band_touch_alone_is_not_an_entry(self):
        ctx = self.context()
        ctx["bars"][-2] = Candle(ctx["bars"][-2].ts, 99, 100, 97, 99, 10)
        self.assertNotIn("band_reentry", plans(ctx))

    def test_confirmed_range_reentry_uses_midband_and_structural_stop(self):
        ctx = self.context()
        ctx["bars"][-2] = Candle(ctx["bars"][-2].ts, 99, 100, 97, 97.5, 10)
        ctx["bars"][-1] = Candle(ctx["bars"][-1].ts, 98, 99, 97.5, 98.5, 10)
        side, stop, target = plans(ctx)["band_reentry"]
        self.assertEqual(side, "BUY")
        self.assertLess(stop, 97)
        self.assertEqual(target, 100)
        ctx["adx"] = 30
        self.assertNotIn("band_reentry", plans(ctx))

    def test_retest_level_excludes_breakout_and_retest_candles(self):
        ctx = self.context()
        ctx.update(adx=30, ef=[100] * 58 + [100.5, 101], es=[99] * 60)
        ctx["bars"][-2] = Candle(ctx["bars"][-2].ts, 100, 103, 100, 102, 20)
        ctx["bars"][-1] = Candle(ctx["bars"][-1].ts, 101.2, 103, 101, 102, 10)
        side, stop, target = plans(ctx)["retest"]
        self.assertEqual(side, "BUY")
        self.assertLess(stop, 101)
        self.assertGreater(target, 102)

    def test_no_five_minute_signal_from_partial_bar(self):
        rows = [Candle(i * 60000, 100, 101, 99, 100, 10) for i in range(301)]
        self.assertTrue(all(s.side == "HOLD" for s in all_chart_signals(rows, Settings()).values()))


if __name__ == "__main__":
    unittest.main()
