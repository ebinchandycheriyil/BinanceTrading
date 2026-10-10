from dataclasses import asdict, dataclass
import json
import math


@dataclass(frozen=True)
class Settings:
    symbols: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT")
    initial_balance: float = 1000.0
    risk_fraction: float = 0.0025
    portfolio_risk: float = 0.005
    max_positions: int = 2
    max_gross_exposure: float = 1.0
    daily_loss_fraction: float = 0.015
    min_hold_minutes: int = 5
    max_hold_minutes: int = 30
    poll_seconds: float = 5.0
    stale_seconds: float = 15.0
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    max_spread_bps: float = 4.0
    atr_stop_multiple: float = 1.5
    min_stop_fraction: float = 0.002
    max_stop_fraction: float = 0.008
    reward_risk: float = 1.5
    reward_risk_basis: str = "net"
    adx_min: float = 20.0
    volume_ratio_min: float = 0.8
    cooldown_minutes: int = 5
    loss_streak_limit: int = 3
    loss_pause_minutes: int = 30
    feedback_sample: int = 20
    strategy: str = "confluence"
    min_move_cost_ratio: float = 0.0
    min_target_cost_ratio: float = 0.0
    min_net_reward_risk: float = 0.0
    max_daily_entries: int = 0  # 0 preserves the original unlimited paper setting
    daily_fee_budget_fraction: float = 0.0

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be nonempty and unique")
        if any(not s.endswith("/USDT:USDT") for s in self.symbols):
            raise ValueError("Only linear USDT perpetual symbols are supported")
        if not 5 <= self.min_hold_minutes <= self.max_hold_minutes <= 30:
            raise ValueError("Holding window must be between 5 and 30 minutes")
        if not 0 < self.risk_fraction <= self.portfolio_risk <= 0.02:
            raise ValueError("Invalid trade/portfolio risk limits")
        if not 0 < self.daily_loss_fraction < 1 or not 0 < self.max_gross_exposure <= 1:
            raise ValueError("Invalid daily loss or gross exposure limit")
        if not 0 < self.min_stop_fraction <= self.max_stop_fraction < 1:
            raise ValueError("Invalid stop bounds")
        for key in ("initial_balance", "max_positions", "reward_risk", "atr_stop_multiple",
                    "poll_seconds", "stale_seconds", "feedback_sample", "loss_streak_limit",
                    "loss_pause_minutes", "cooldown_minutes"):
            if getattr(self, key) <= 0:
                raise ValueError(f"{key} must be positive")
        for key in ("max_positions", "feedback_sample", "loss_streak_limit", "min_hold_minutes",
                    "max_hold_minutes", "cooldown_minutes", "loss_pause_minutes"):
            if type(getattr(self, key)) is not int:
                raise ValueError(f"{key} must be an integer")
        if self.poll_seconds > self.stale_seconds:
            raise ValueError("poll_seconds cannot exceed stale_seconds")
        if min(self.fee_bps, self.slippage_bps, self.max_spread_bps,
               self.adx_min, self.volume_ratio_min, self.min_move_cost_ratio,
               self.min_target_cost_ratio, self.min_net_reward_risk) < 0:
            raise ValueError("Costs and filters cannot be negative")
        if type(self.max_daily_entries) is not int or self.max_daily_entries < 0:
            raise ValueError("max_daily_entries must be a nonnegative integer")
        if not 0 <= self.daily_fee_budget_fraction <= .02:
            raise ValueError("Invalid daily fee budget")
        if self.reward_risk_basis not in ("net", "price"):
            raise ValueError("Unknown reward/risk basis")
        if self.strategy not in ("confluence", "ema_only", "no_volume", "breakout", "momentum5", "momentum15",
                                 "band_reentry", "retest", "trend_pullback"):
            raise ValueError("Unknown strategy")


def load_settings(path=None):
    if path is None:
        return Settings()
    with open(path, encoding="utf-8") as handle:
        values = json.load(handle)
    if "symbols" in values:
        values["symbols"] = tuple(values["symbols"])
    return Settings(**values)
