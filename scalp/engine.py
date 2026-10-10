"""Persistent simulated execution. This module cannot submit exchange orders."""
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import sqlite3

from .settings import Settings
from .signals import Signal, cost_filter
from .costs import FeeSchedule


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    ts: float
    bid_size: float = 1e9
    ask_size: float = 1e9

    def valid(self, now, stale_seconds):
        return (all(math.isfinite(v) for v in asdict(self).values())
                and 0 < self.bid <= self.ask and min(self.bid_size, self.ask_size) > 0
                and -2 <= now - self.ts <= stale_seconds)

    @property
    def spread_bps(self):
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2) * 10000


class Journal:
    def __init__(self, path, cfg):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL NOT NULL, body TEXT NOT NULL)")
        row = self.conn.execute("SELECT body FROM state WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else dict(
            settings=json.loads(json.dumps(asdict(cfg))), balance=cfg.initial_balance,
            positions={}, trades=[], seen={}, day=None, day_equity=cfg.initial_balance,
            day_halted=False, pause_until=0, loss_streak=0, cooldown={}, next_id=1)
        # Neutral new fields can be filled in for accounts from earlier versions.
        self.state["settings"].setdefault("min_move_cost_ratio", 0.0)
        self.state["settings"].setdefault("reward_risk_basis", "net")
        for name, default in (("min_target_cost_ratio", 0.0), ("min_net_reward_risk", 0.0),
                              ("max_daily_entries", 0), ("daily_fee_budget_fraction", 0.0)):
            self.state["settings"].setdefault(name, default)
        if self.state["settings"] != json.loads(json.dumps(asdict(cfg))):
            self.conn.close()
            raise ValueError("Settings differ from saved account. Use another --db for a new experiment.")

    def save(self, events):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO state VALUES (1, ?)",
                              (json.dumps(self.state, allow_nan=False),))
            self.conn.executemany("INSERT INTO events(ts,body) VALUES (?,?)",
                                  [(e["ts"], json.dumps(e, allow_nan=False)) for e in events])

    def close(self):
        self.conn.close()


class PaperEngine:
    def __init__(self, cfg: Settings, path=":memory:", emit=None, persist=True):
        self.cfg = cfg
        self.journal = Journal(path, cfg)
        self.s = self.journal.state
        self.emit = emit or (lambda event: None)
        self.persist = persist
        self.fees = FeeSchedule(taker_bps=cfg.fee_bps)

    def commit(self, events):
        if self.persist:
            self.journal.save(events)
        for event in events:
            self.emit(event)

    def event(self, now, kind, **fields):
        return dict(ts=now, mode="PAPER", event=kind, **fields)

    def _exit_price(self, p, q):
        slip = self.cfg.slippage_bps / 10000
        return q.bid * (1 - slip) if p["side"] == "BUY" else q.ask * (1 + slip)

    def _net(self, p, price):
        sign = 1 if p["side"] == "BUY" else -1
        gross = sign * (price - p["entry"]) * p["qty"]
        exit_fee = self.fees.commission(p["qty"], price)
        return gross, exit_fee, gross - p["entry_fee"] - exit_fee - p.get("funding_cost", 0)

    def apply_funding(self, symbol, timestamp, rate, mark_price):
        """Apply a known historical funding event once to a carried position."""
        if not all(math.isfinite(x) for x in (timestamp, rate, mark_price)) or mark_price <= 0:
            raise ValueError("Invalid funding event")
        p = self.s["positions"].get(symbol)
        if not p or p["entry_time"] >= timestamp or timestamp in p.get("funding_events", []):
            return
        cost = (1 if p["side"] == "BUY" else -1) * p["qty"] * mark_price * rate
        p["funding_cost"] = p.get("funding_cost", 0) + cost
        p.setdefault("funding_events", []).append(timestamp)
        self.s["balance"] -= cost
        self.commit([self.event(timestamp, "FUNDING", symbol=symbol, cost=cost)])

    def equity(self, quotes):
        value = self.s["balance"]
        for symbol, p in self.s["positions"].items():
            if symbol not in quotes:
                raise ValueError("Cannot calculate equity with missing open-position quotes")
            gross, exit_fee, _ = self._net(p, self._exit_price(p, quotes[symbol]))
            value += gross - exit_fee
        return value

    def _close(self, symbol, q, now, reason):
        p = self.s["positions"].pop(symbol)
        price = self._exit_price(p, q)
        move_r = (1 if p["side"] == "BUY" else -1) * (price - p["entry"]) / p["stop_distance"]
        p["mae_r"] = min(p["mae_r"], move_r)
        p["mfe_r"] = max(p["mfe_r"], move_r)
        gross, exit_fee, net = self._net(p, price)
        self.s["balance"] += gross - exit_fee
        trade = dict(p, symbol=symbol, exit=price, exit_time=now, exit_reason=reason,
                     gross_pnl=gross, fees=p["entry_fee"] + exit_fee, exit_fee=exit_fee, net_pnl=net,
                     exit_notional=price * p["qty"],
                     exit_reference=(q.bid + q.ask) / 2,
                     hold_minutes=(now - p["entry_time"]) / 60,
                     realized_r=net / p["risk_amount"])
        # Observable diagnostics, not claims that an indicator caused the loss.
        issues = []
        if gross > 0 and net <= 0:
            issues.append("costs_erased_gross_profit")
        if net < 0 and p["mfe_r"] < 0.25:
            issues.append("little_favorable_follow_through")
        if net < 0 and p["mfe_r"] >= 0.75:
            issues.append("favorable_move_reversed")
        if reason == "TIME_LIMIT" and net < 0:
            issues.append("time_budget_expired_at_loss")
        if trade["hold_minutes"] > self.cfg.max_hold_minutes + self.cfg.poll_seconds / 60:
            issues.append("exit_delayed_by_data_or_downtime")
        trade["diagnostics"] = issues
        if "entry_reference" in p:
            mid_gross = (1 if p["side"] == "BUY" else -1) * (trade["exit_reference"] - p["entry_reference"]) * p["qty"]
            trade["gross_before_execution_costs"] = mid_gross
            trade["spread_slippage_cost"] = mid_gross - gross
        self.s["trades"].append(trade)
        self.s["cooldown"][symbol] = now + self.cfg.cooldown_minutes * 60
        self.s["loss_streak"] = self.s["loss_streak"] + 1 if net < 0 else 0
        if self.s["loss_streak"] >= self.cfg.loss_streak_limit:
            self.s["pause_until"] = now + self.cfg.loss_pause_minutes * 60
            self.s["loss_streak"] = 0
        return self.event(now, "EXIT", order_side="SELL" if p["side"] == "BUY" else "BUY",
                          reduce_only=True, trade=trade)

    def monitor(self, quotes, now, force_reason=None):
        events = []
        valid = {s: q for s, q in quotes.items() if q.valid(now, self.cfg.stale_seconds)}
        for symbol, p in list(self.s["positions"].items()):
            q = valid.get(symbol)
            if q is None:
                events.append(self.event(now, "DATA_HALT", symbol=symbol,
                                         reason="Missing/stale quote; cannot simulate an executable exit",
                                         overdue=now >= p["deadline"]))
                continue
            price = self._exit_price(p, q)
            sign = 1 if p["side"] == "BUY" else -1
            move_r = sign * (price - p["entry"]) / p["stop_distance"]
            p["mfe_r"] = max(p["mfe_r"], move_r)
            p["mae_r"] = min(p["mae_r"], move_r)
            reason = force_reason
            if not reason:
                if sign * (price - p["stop"]) <= 0:
                    reason = "STOP"
                elif sign * (price - p["target"]) >= 0:
                    reason = "TARGET"
                elif now >= p["deadline"]:
                    reason = "TIME_LIMIT"
                elif now - p["entry_time"] >= self.cfg.min_hold_minutes * 60 and move_r < 0.15:
                    reason = "NO_FOLLOW_THROUGH"
            if reason:
                events.append(self._close(symbol, q, now, reason))
        if all(s in valid for s in self.s["positions"]):
            equity = self.equity(valid)
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            if self.s["day"] != day:
                self.s.update(day=day, day_equity=equity, day_halted=False)
            if equity <= self.s["day_equity"] * (1 - self.cfg.daily_loss_fraction):
                self.s["day_halted"] = True
                for symbol in list(self.s["positions"]):
                    events.append(self._close(symbol, valid[symbol], now, "DAILY_LOSS"))
                events.append(self.event(now, "RISK_HALT", reason="UTC daily equity loss limit"))
        self.commit(events)
        return events

    def enter(self, symbol, sig: Signal, candle_ts, quotes, now, round_qty=lambda s, q: q,
              min_notional=5.0, min_qty=0.0):
        if symbol not in self.cfg.symbols:
            raise ValueError("Symbol is not in configured universe")
        # Persist deduplication across restarts, including rejected decisions.
        if candle_ts <= self.s["seen"].get(symbol, -1):
            return None
        self.s["seen"][symbol] = candle_ts
        reason = None
        q = quotes.get(symbol)
        if q is not None and q.valid(now, self.cfg.stale_seconds):
            sig = cost_filter(sig, self.cfg, q.spread_bps)
        if sig.side == "HOLD":
            reason = sig.reason
        elif sig.side not in ("BUY", "SELL") or not math.isfinite(sig.stop_fraction) or not self.cfg.min_stop_fraction <= sig.stop_fraction <= self.cfg.max_stop_fraction:
            reason = "invalid signal"
        elif any(s not in quotes or not quotes[s].valid(now, self.cfg.stale_seconds) for s in self.cfg.symbols):
            reason = "incomplete or stale market snapshot"
        elif now - (candle_ts / 1000 + 60) > 90 or now < candle_ts / 1000 + 60:
            reason = "stale or unclosed signal candle"
        elif self.s["day"] != datetime.fromtimestamp(now, timezone.utc).date().isoformat():
            reason = "monitor account before entries"
        elif self.s["day_halted"] or now < self.s["pause_until"]:
            reason = "daily loss or losing-streak pause"
        elif self.cfg.max_daily_entries and sum(
                int(t["entry_time"] // 86400) == int(now // 86400)
                for t in [*self.s["trades"], *self.s["positions"].values()]) >= self.cfg.max_daily_entries:
            reason = "daily entry limit"
        elif symbol in self.s["positions"] or len(self.s["positions"]) >= self.cfg.max_positions:
            reason = "position limit"
        elif now < self.s["cooldown"].get(symbol, 0):
            reason = "symbol cooldown"
        elif q.spread_bps > self.cfg.max_spread_bps + 1e-8:
            reason = "spread too wide"
        if reason is not None:
            event = self.event(now, "SIGNAL", symbol=symbol, side="HOLD", reason=reason,
                               features=sig.features)
            self.commit([event])
            return event

        cfg = self.cfg
        equity = self.equity(quotes)
        sign = 1 if sig.side == "BUY" else -1
        entry = (q.ask if sign == 1 else q.bid) * (1 + sign * cfg.slippage_bps / 10000)
        distance = entry * sig.stop_fraction
        # Include estimated two-sided costs in budget. Gaps can still exceed it.
        cost_per_unit = entry * (2 * cfg.fee_bps + 2 * cfg.slippage_bps + q.spread_bps) / 10000
        target_distance = cfg.reward_risk * (distance + cost_per_unit) + cost_per_unit
        if cfg.reward_risk_basis == "price":
            target_distance = cfg.reward_risk * distance
        if sig.stop_price is not None or sig.target_price is not None:
            if (sig.stop_price is None or sig.target_price is None or
                    not all(math.isfinite(x) and x > 0 for x in (sig.stop_price, sig.target_price))):
                return self.reject(now, symbol, "invalid structural levels")
            distance = sign * (entry - sig.stop_price)
            target_distance = sign * (sig.target_price - entry)
            if not cfg.min_stop_fraction <= distance / entry <= cfg.max_stop_fraction or target_distance <= 0:
                return self.reject(now, symbol, "entry gap invalidated structural setup")
        net_rr = (target_distance - cost_per_unit) / (distance + cost_per_unit)
        if cost_per_unit and target_distance < cfg.min_target_cost_ratio * cost_per_unit:
            return self.reject(now, symbol, "target too small after round-trip costs")
        if net_rr + 1e-10 < cfg.min_net_reward_risk:
            return self.reject(now, symbol, "net reward/risk below minimum")
        recent = self.s["trades"][-cfg.feedback_sample:]
        multiplier = 0.5 if len(recent) >= cfg.feedback_sample and sum(t["net_pnl"] for t in recent) < 0 else 1.0
        risk_available = min(equity * cfg.risk_fraction * multiplier,
                             equity * cfg.portfolio_risk - sum(p["risk_amount"] for p in self.s["positions"].values()))
        used = sum(p["qty"] * quotes[s].ask for s, p in self.s["positions"].items())
        notional_available = max(0, equity * cfg.max_gross_exposure - used)
        raw_qty = min(max(0, risk_available) / (distance + cost_per_unit),
                      notional_available / entry, (q.ask_size if sign == 1 else q.bid_size) * 0.1)
        qty = float(round_qty(symbol, raw_qty))
        if not math.isfinite(qty) or qty > raw_qty * (1 + 1e-10) or qty < 0:
            raise ValueError("Quantity rounding must be finite, nonnegative and downward")
        if qty <= 0 or qty < min_qty or qty * entry < min_notional:
            event = self.event(now, "SIGNAL", symbol=symbol, side="HOLD", reason="size below exchange minimum or risk capacity")
            self.commit([event])
            return event
        if cfg.daily_fee_budget_fraction:
            day = int(now // 86400)
            spent = sum(t["entry_fee"] for t in [*self.s["trades"], *self.s["positions"].values()]
                        if int(t["entry_time"] // 86400) == day)
            spent += sum(t.get("exit_fee", t["fees"] - t["entry_fee"]) for t in self.s["trades"]
                         if int(t["exit_time"] // 86400) == day)
            reserved = sum(self.fees.commission(p["qty"], max(quotes[s].ask, p["stop"], p["target"]))
                           for s, p in self.s["positions"].items())
            planned_fees = self.fees.round_trip(qty, entry, max(entry, entry + sign * target_distance,
                                                               entry - sign * distance))
            if spent + reserved + planned_fees > self.s["day_equity"] * cfg.daily_fee_budget_fraction:
                return self.reject(now, symbol, "daily commission budget")
        p = dict(id=self.s["next_id"], side=sig.side, entry=entry, qty=qty,
                 entry_time=now, deadline=now + cfg.max_hold_minutes * 60,
                 stop=entry - sign * distance, target=entry + sign * target_distance,
                 stop_distance=distance, risk_amount=qty * (distance + cost_per_unit),
                 entry_fee=self.fees.commission(qty, entry), entry_notional=entry * qty,
                 entry_reference=(q.bid + q.ask) / 2,
                 fee_rate_bps=cfg.fee_bps, entry_role="taker", exit_role="taker",
                 planned_net_reward_risk=net_rr,
                 features=sig.features, entry_reason=sig.reason, risk_multiplier=multiplier,
                 mae_r=0.0, mfe_r=0.0, funding_cost=0.0, funding_events=[])
        self.s["next_id"] += 1
        self.s["positions"][symbol] = p
        self.s["balance"] -= p["entry_fee"]
        event = self.event(now, "ENTRY", symbol=symbol, order_side=sig.side, position=p)
        self.commit([event])
        return event

    def reject(self, now, symbol, reason):
        event = self.event(now, "SIGNAL", symbol=symbol, side="HOLD", reason=reason)
        self.commit([event])
        return event

    def status(self, quotes, now):
        valid = all(s in quotes and quotes[s].valid(now, self.cfg.stale_seconds) for s in self.s["positions"])
        return self.event(now, "STATUS", balance=self.s["balance"],
                          equity=self.equity(quotes) if valid else None,
                          open_positions=self.s["positions"], day_halted=self.s["day_halted"],
                          pause_until=self.s["pause_until"], closed_trades=len(self.s["trades"]))


def summarize(trades):
    pnls = [t["net_pnl"] for t in trades]
    gains = sum(max(p, 0) for p in pnls)
    losses = -sum(min(p, 0) for p in pnls)
    peak = cumulative = drawdown = 0.0
    groups, issues = defaultdict(list), defaultdict(int)
    for t in trades:
        cumulative += t["net_pnl"]
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
        groups[t["exit_reason"]].append(t["net_pnl"])
        for issue in t["diagnostics"]:
            issues[issue] += 1
    return dict(trades=len(trades), net_pnl=sum(pnls), gross_pnl=sum(t["gross_pnl"] for t in trades),
                executed_notional=sum((t["entry"] + t["exit"]) * t["qty"] for t in trades),
                spread_slippage_cost=sum(t.get("spread_slippage_cost", 0) for t in trades),
                funding_cost=sum(t.get("funding_cost", 0) for t in trades),
                fees=sum(t["fees"] for t in trades), win_rate=sum(p > 0 for p in pnls) / len(pnls) if pnls else 0,
                profit_factor=gains / losses if losses else None,
                expectancy=sum(pnls) / len(pnls) if pnls else 0,
                closed_trade_drawdown=drawdown,
                average_hold_minutes=sum(t["hold_minutes"] for t in trades) / len(trades) if trades else 0,
                exits={k: dict(count=len(v), net_pnl=sum(v)) for k, v in groups.items()},
                loss_diagnostics=dict(issues),
                evidence="insufficient sample" if len(trades) < 100 else "requires independent forward validation")
