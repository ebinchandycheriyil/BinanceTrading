"""Exchange commission is a percentage of executed notional on EACH fill."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class FeeSchedule:
    # Public Binance FAQ examples; user-specific rates remain unverified.
    maker_bps: float = 2.0
    taker_bps: float = 5.0

    def __post_init__(self):
        if any(not math.isfinite(x) or x < 0 for x in (self.maker_bps, self.taker_bps)):
            raise ValueError("Fee rates must be finite and nonnegative")

    def commission(self, quantity, price, role="taker"):
        if role not in ("maker", "taker"):
            raise ValueError("role must be maker or taker")
        if any(not math.isfinite(x) or x < 0 for x in (quantity, price)):
            raise ValueError("Invalid execution quantity/price")
        return quantity * price * getattr(self, role + "_bps") / 10000

    def round_trip(self, quantity, entry, exit_price, entry_role="taker", exit_role="taker"):
        return self.commission(quantity, entry, entry_role) + self.commission(quantity, exit_price, exit_role)


def cost_scenarios(trades, taker_bps=5.0, maker_bps=2.0):
    """Same fills/quantities/times; maker cases are hypothetical, NOT fill simulations."""
    scenarios = {}
    base = FeeSchedule(maker_bps, taker_bps)
    for name, entry_role, exit_role in (("taker_taker", "taker", "taker"),
                                       ("maker_taker_hypothetical", "maker", "taker"),
                                       ("maker_maker_hypothetical", "maker", "maker")):
        fees = sum(base.round_trip(t["qty"], t["entry"], t["exit"], entry_role, exit_role) for t in trades)
        gross = sum(t["gross_pnl"] for t in trades)
        funding = sum(t.get("funding_cost", 0) for t in trades)
        scenarios[name] = dict(trades=len(trades), commissions=fees, net=gross - fees - funding)
    return scenarios
