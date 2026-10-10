"""Conservative fixed-expiry cash-and-carry estimates, never trade instructions."""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math

from .carry_market import common_step


@dataclass(frozen=True)
class CarrySettings:
    initial_balance: float = 1000.
    spot_fee: float = .001
    future_fee: float = .0005
    slippage: float = .0002
    stress_multiplier: float = 2.
    collateral_fraction: float = 1.
    basket_fraction: float = .4
    portfolio_fraction: float = .8
    max_positions: int = 2
    depth_fraction: float = .1
    max_spread_bps: float = 20.
    stale_seconds: float = 8.
    max_request_seconds: float = 3.
    max_quote_skew_seconds: float = 3.
    min_days: float = 7.
    max_days: float = 200.
    unwind_hours: float = 1.
    residual_basis_bps: float = 10.
    exit_spread_floor_bps: float = 2.
    capital_charge_apr: float = .04
    min_net_apr: float = .10
    min_net_usdt: float = 2.
    take_profit_fraction: float = .8
    stop_fraction: float = .005
    daily_loss_fraction: float = .015
    collateral_buffer_fraction: float = .25
    poll_seconds: float = 30.

    def validate(self):
        if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError('All carry settings must be finite and positive')
        for name in ('spot_fee', 'future_fee', 'slippage', 'depth_fraction', 'take_profit_fraction',
                     'stop_fraction', 'daily_loss_fraction', 'basket_fraction', 'portfolio_fraction', 'collateral_buffer_fraction'):
            if getattr(self, name) >= 1:
                raise ValueError(name+' must be below one')
        if not (self.basket_fraction <= self.portfolio_fraction <= .95 and self.collateral_fraction >= 1
                and self.max_days <= 365 and self.min_days < self.max_days
                and self.unwind_hours < self.min_days*24 and self.stress_multiplier >= 1
                and isinstance(self.max_positions, int) and self.max_positions <= 10
                and self.spot_fee*self.stress_multiplier < .1
                and self.future_fee*self.stress_multiplier < .1
                and self.slippage*self.stress_multiplier < .1):
            raise ValueError('Invalid carry limits')


def round_step(value, step, up=False):
    d, s = Decimal(str(value)), Decimal(str(step))
    if not d.is_finite() or not s.is_finite() or s <= 0 or d < 0:
        raise ValueError('Invalid quantity or step')
    return float((d/s).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*s)


def quote_errors(contract, now, cfg):
    reasons = []
    for leg in ('spot', 'future'):
        q = contract.get(leg)
        if not q:
            reasons.append(leg+'_missing_book'); continue
        fields = ('bid', 'ask', 'bid_size', 'ask_size', 'ts', 'requested_at')
        if any(not isinstance(q.get(k), (int, float)) or not math.isfinite(q[k]) or q[k] <= 0 for k in fields):
            reasons.append(leg+'_invalid_book'); continue
        if q['bid'] > q['ask']:
            reasons.append(leg+'_crossed_book')
        if not (-1 <= now-q['requested_at'] <= cfg.stale_seconds
                and 0 <= q['ts']-q['requested_at'] <= cfg.max_request_seconds and q['ts'] <= now+1):
            reasons.append(leg+'_stale_or_slow_book')
        ts = q.get('exchange_ts')
        if leg == 'future' and ts is None:
            reasons.append('future_missing_exchange_timestamp')
        elif ts is not None and (not isinstance(ts, (float, int)) or not math.isfinite(ts)
                                 or not -2 <= now-ts <= cfg.stale_seconds):
            reasons.append(leg+'_stale_exchange_book')
    if not reasons and abs(contract['spot']['requested_at']-contract['future']['requested_at']) > cfg.max_quote_skew_seconds:
        reasons.append('unsynchronized_books')
    mark = contract.get('mark')
    if not mark or not all(isinstance(mark.get(k), (float, int)) and math.isfinite(mark[k])
                           and mark[k] > 0 for k in ('price', 'ts')):
        reasons.append('missing_or_invalid_mark')
    elif not -2 <= now-mark['ts'] <= cfg.stale_seconds:
        reasons.append('stale_mark')
    return reasons


def scenario(contract, qty, now, cfg, multiplier=1.):
    """Full convergence estimate with explicit pre-expiry residual/cost reserves.

    Spot BUY commission is paid in base. Buy enough rounded gross quantity to
    retain the hedge quantity after that fee. Any extra base dust is written off.
    Exit-price reserves are estimates, not executable forward quotes.
    """
    sf, ff, slip = cfg.spot_fee*multiplier, cfg.future_fee*multiplier, cfg.slippage*multiplier
    spot, future = contract['spot'], contract['future']
    spot_entry, future_entry = spot['ask']*(1+slip), future['bid']*(1-slip)
    gross_spot_qty = round_step(Decimal(str(qty))/(1-Decimal(str(sf))), contract['spot_limits']['step'], up=True)
    receipt = gross_spot_qty*(1-sf)
    dust = max(0., receipt-qty)
    spot_fee = gross_spot_qty*sf*spot_entry
    dust_cost = dust*spot_entry
    future_entry_fee = qty*future_entry*ff
    spot_cash = gross_spot_qty*spot_entry
    collateral = qty*future_entry*cfg.collateral_fraction
    capital = spot_cash+collateral+future_entry_fee
    days = max(0., (contract['expiry']-cfg.unwind_hours*3600-now)/86400)
    ref = (spot['ask']+spot['bid'])/2
    exit_fees = qty*ref*(sf+ff)
    exit_slip = qty*ref*2*slip
    exit_spread = qty*max((spot['ask']-spot['bid']+future['ask']-future['bid'])/2,
                          ref*cfg.exit_spread_floor_bps/10000)*multiplier
    residual = qty*ref*cfg.residual_basis_bps/10000*multiplier
    capital_charge = capital*cfg.capital_charge_apr*days/365
    gross = qty*(future_entry-spot_entry)
    net = gross-spot_fee-dust_cost-future_entry_fee-exit_fees-exit_slip-exit_spread-residual-capital_charge
    return dict(qty=qty, gross_spot_qty=gross_spot_qty, spot_entry=spot_entry, future_entry=future_entry,
                spot_fee=spot_fee, dust_qty=dust, dust_cost=dust_cost, future_entry_fee=future_entry_fee,
                entry_cost=spot_fee+dust_cost+future_entry_fee, spot_cash=spot_cash, collateral=collateral,
                capital=capital, days=days, gross_basis_pnl=gross, exit_fees=exit_fees,
                exit_slippage=exit_slip, exit_spread=exit_spread, residual_basis_reserve=residual,
                capital_charge=capital_charge, estimated_net=net,
                estimated_apr=net/capital*365/days if capital > 0 and days > 0 else 0.)


def evaluate(contract, now, cfg, budget=None):
    cfg.validate()
    budget = cfg.initial_balance*cfg.basket_fraction if budget is None else budget
    reasons = quote_errors(contract, now, cfg)
    result = dict(contract=contract['id'], expiry=contract['expiry'], decision='REJECT', reasons=reasons)
    if contract.get('contract_type') not in ('CURRENT_QUARTER', 'NEXT_QUARTER'):
        reasons.append('unsupported_contract')
    days = (contract['expiry']-now)/86400
    if not cfg.min_days <= days <= cfg.max_days:
        reasons.append('expiry_outside_window')
    if not math.isfinite(budget) or budget <= 0:
        reasons.append('no_capital')
    if reasons:
        return result
    spot, future = contract['spot'], contract['future']
    result.update(observed_basis_bps=(future['bid']/spot['ask']-1)*10000,
                  days_to_expiry=days,
                  entry_quotes=dict(spot_ask=spot['ask'], future_bid=future['bid']))
    for leg, q in (('spot', spot), ('future', future)):
        if (q['ask']-q['bid'])/((q['ask']+q['bid'])/2)*10000 > cfg.max_spread_bps:
            reasons.append(leg+'_spread_too_wide')
    sl, fl = contract['spot_limits'], contract['future_limits']
    step = common_step(sl['step'], fl['step'])
    # Size for stressed purchase fee/price and whole-notional futures collateral.
    sf = cfg.spot_fee*cfg.stress_multiplier
    unit_capital = spot['ask']*(1+cfg.slippage*cfg.stress_multiplier)/(1-sf)
    unit_capital += future['bid']*cfg.collateral_fraction + future['bid']*cfg.future_fee*cfg.stress_multiplier
    cap = min(budget/unit_capital, spot['ask_size']*cfg.depth_fraction*(1-sf),
              future['bid_size']*cfg.depth_fraction, sl['max_qty']*(1-sf), fl['max_qty'])
    qty = round_step(cap, step)
    result['sizing'] = dict(max_unrounded_hedge_qty=cap, common_step=step,
                           spot_displayed_ask_qty=spot['ask_size'], future_displayed_bid_qty=future['bid_size'])
    base = stress = None
    # At most a few increments need removing for upward spot-quantity rounding.
    for _ in range(4):
        if qty <= 0:
            break
        base, stress = scenario(contract, qty, now, cfg), scenario(contract, qty, now, cfg, cfg.stress_multiplier)
        if (max(base['capital'], stress['capital']) <= budget+1e-8
                and stress['gross_spot_qty'] <= min(sl['max_qty'], spot['ask_size']*cfg.depth_fraction)+1e-12):
            break
        qty = round_step(max(0, Decimal(str(qty))-Decimal(step)), step)
    if qty <= 0 or base is None or base['qty'] != qty:
        reasons.append('quantity_below_step'); return result
    for name, lim, quantity, price in (('spot', sl, base['gross_spot_qty'], base['spot_entry']),
                                      ('future', fl, qty, base['future_entry'])):
        if quantity < lim['min_qty'] or quantity*price < lim['min_notional']:
            reasons.append(name+'_below_minimum')
        if quantity > lim['max_qty'] or (lim.get('max_notional') and quantity*price > lim['max_notional']):
            reasons.append(name+'_above_maximum')
    # Net spot exit quantity must itself satisfy spot lot/minimum rules.
    if qty < sl['min_qty'] or qty*spot['bid'] < sl['min_notional']:
        reasons.append('spot_exit_below_minimum')
    if max(base['capital'], stress['capital']) > budget+1e-8:
        reasons.append('capital_budget')
    if base['estimated_net'] < cfg.min_net_usdt:
        reasons.append('net_profit_below_hurdle')
    if base['estimated_apr'] < cfg.min_net_apr:
        reasons.append('annual_return_below_hurdle')
    if stress['estimated_net'] <= 0:
        reasons.append('negative_under_cost_stress')
    result.update(decision='QUALIFIED' if not reasons else 'REJECT', base=base, stress=stress,
                  budget=budget, planned_exit=contract['expiry']-cfg.unwind_hours*3600)
    return result
