"""Minute execution diagnostic on frozen historical baskets, not a strategy replay."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from .signals import Candle


MINUTE = 60_000
ROOT = Path('artifacts/pair_viability')


def exit_mark(legs, prices, fee=.0005, slip=.0002, spread=.0004):
    exits = {s: prices[s]*(1-leg['sign']*spread/2)*(1-leg['sign']*slip)
             for s, leg in legs.items()}
    gross = sum(leg['sign']*(exits[s]-leg['entry'])*leg['qty'] for s, leg in legs.items())
    fees = sum(exits[s]*leg['qty']*fee for s, leg in legs.items())
    return gross, fees, exits


def evaluate_basket(trade, minute_data, funding):
    """Use fixed fills/size, minute observations and no later than original exit.

    Original basket gross +/-risk guards take precedence at sampled timestamps.
    Original relationship/time/end exit remains a deadline. Funding is counted
    only while actually held. No re-entry, new signal, resizing or fill queue is
    simulated. High/low extrema are only an ambiguity diagnostic because prices
    in the two legs need not hit their extrema simultaneously.
    """
    opened = int(trade['opened'])
    deadline = round(trade['exit_time']*1000)
    if opened % MINUTE or deadline % MINUTE not in (0, MINUTE-1) or deadline < opened:
        raise ValueError('Basket endpoints must be minute open/close observations')
    expected = list(range(opened, deadline//MINUTE*MINUTE+1, MINUTE))
    indexed = {}
    for symbol in trade['legs']:
        rows = [row for row in minute_data[symbol] if opened <= row.ts <= expected[-1]]
        if [row.ts for row in rows] != expected:
            raise ValueError(f'Missing, duplicate or unordered minute data for {symbol}')
        indexed[symbol] = {row.ts: row for row in rows}
    ambiguity = []
    exit_time = None
    exit_reason = None
    exit_prices = None

    def observed(prices, ts):
        gross, _, _ = exit_mark(trade['legs'], prices)
        if gross <= -trade['risk']:
            return 'STOP'
        if gross >= trade['risk']:
            return 'TARGET'
        return trade['exit_reason'] if ts == deadline else None

    for ts in expected:
        prices = {s: rows[ts].open for s, rows in indexed.items()}
        reason = observed(prices, ts)
        if reason:
            exit_time, exit_reason, exit_prices = ts, reason, prices
            break
        close_time = ts+MINUTE-1
        if close_time > deadline:
            raise AssertionError('Expected original deadline to close at minute open')
        adverse_prices = {s: rows[ts].low if trade['legs'][s]['sign'] > 0 else rows[ts].high
                          for s, rows in indexed.items()}
        adverse_gross, _, _ = exit_mark(trade['legs'], adverse_prices)
        if adverse_gross <= -trade['risk']:
            ambiguity.append(dict(minute_start=ts, adverse_gross=adverse_gross,
                                  note='Nonsynchronous leg extrema; not an observed stop fill.'))
        prices = {s: rows[ts].close for s, rows in indexed.items()}
        reason = observed(prices, close_time)
        if reason:
            exit_time, exit_reason, exit_prices = close_time, reason, prices
            break
    if exit_time is None:
        raise AssertionError('Basket did not close by original deadline')
    gross, exit_fee, fills = exit_mark(trade['legs'], exit_prices)
    settlements = []
    for symbol, leg in trade['legs'].items():
        events = funding.get(symbol)
        if events is None:
            raise ValueError(f'Missing funding data for {symbol}')
        timestamps = [event['ts'] for event in events]
        if timestamps != sorted(set(timestamps)):
            raise ValueError('Funding events must have unique ordered timestamps')
        for event in events:
            if opened < event['ts'] <= exit_time:
                cost = leg['sign']*leg['qty']*event['mark']*event['rate']
                settlements.append(dict(symbol=symbol, ts=event['ts'], rate=event['rate'],
                                        mark=event['mark'], cost=cost))
    funding_cost = sum(row['cost'] for row in settlements)
    net = gross-trade['entry_fee']-exit_fee-funding_cost
    return dict(symbols=list(trade['legs']), opened=opened, original_exit_time=deadline,
                minute_exit_time=exit_time, original_exit_reason=trade['exit_reason'],
                minute_exit_reason=exit_reason, original_net=trade['net_pnl'], minute_net=net,
                difference=net-trade['net_pnl'], gross_pnl=gross, entry_fee=trade['entry_fee'],
                exit_fee=exit_fee, funding_cost=funding_cost, funding_events=settlements,
                exit_fills=fills, hold_hours=(exit_time-opened)/3_600_000,
                original_hold_hours=(deadline-opened)/3_600_000,
                original_risk=trade['risk'], loss_budget=trade['loss_budget'],
                realized_loss_over_budget=net < -trade['loss_budget']-1e-8,
                earlier_exit=exit_time < deadline, adverse_envelope_breaches=ambiguity,
                adverse_envelope_ambiguous=bool(ambiguity), verified_minutes=len(expected))


def load_baskets(root=ROOT):
    results = json.loads((root/'results.json').read_text())
    ledgers = json.loads((root/'ledgers.json').read_text())
    names = [row['id'] for row in results['evaluations'] if '_base_' in row['id']]
    return {name: ledgers[name]['trades'] for name in names}


def download(baskets, root=ROOT):
    """Public OHLCV only; cache each completed interval, and never fill gaps."""
    import ccxt
    path = root/'minute_data.json'
    stored = json.loads(path.read_text()) if path.exists() else dict(data={})
    indexed = {s: {row['ts']: row for row in rows} for s, rows in stored['data'].items()}
    market = None
    for name, trades in baskets.items():
        for number, trade in enumerate(trades):
            start = int(trade['opened'])
            end = round(trade['exit_time']*1000)//MINUTE*MINUTE+MINUTE
            for symbol in trade['legs']:
                rows = indexed.setdefault(symbol, {})
                cursor = start
                while cursor < end:
                    if cursor in rows:
                        cursor += MINUTE
                        continue
                    if market is None:
                        market = ccxt.binanceusdm({'enableRateLimit': True, 'timeout': 15000})
                        market.load_markets()
                    api_symbol = symbol.removesuffix('-P')+'/USDT:USDT'
                    for attempt in range(3):
                        try:
                            page = market.fetch_ohlcv(api_symbol, '1m', since=cursor,
                                                      limit=min(1000, (end-cursor)//MINUTE))
                            break
                        except (ccxt.NetworkError, ccxt.RequestTimeout):
                            if attempt == 2:
                                raise
                            time.sleep(attempt+1)
                    if not page or int(page[0][0]) != cursor:
                        raise ValueError(f'Missing public minute data: {symbol} {cursor}')
                    for row in page:
                        ts = int(row[0])
                        if start <= ts < end:
                            rows[ts] = asdict(Candle(ts, *map(float, row[1:6])))
                    next_cursor = int(page[-1][0])+MINUTE
                    if next_cursor <= cursor:
                        raise ValueError('Minute pagination stalled')
                    cursor = next_cursor
                if any(ts not in rows for ts in range(start, end, MINUTE)):
                    raise ValueError(f'Incomplete interval for {symbol}')
                stored['data'] = {s: [r[t] for t in sorted(r)] for s, r in indexed.items()}
                stored['source'] = 'Binance USD-M public 1-minute OHLCV, fixed held intervals'
                path.write_text(json.dumps(stored, separators=(',', ':'), allow_nan=False), encoding='utf-8')
            print(json.dumps(dict(downloaded=name, basket=number+1)), flush=True)
    return {s: [Candle(**rows[t]) for t in sorted(rows)] for s, rows in indexed.items()}


def run(root=ROOT):
    baskets = load_baskets(root)
    data = download(baskets, root)
    source = json.loads((root/'market_data.json').read_text())
    reports = {}
    for name, trades in baskets.items():
        rows = [dict(evaluate_basket(trade, data, source['funding']), basket=i+1)
                for i, trade in enumerate(trades)]
        summary = dict(baskets=len(rows), original_net=sum(r['original_net'] for r in rows),
                       minute_net=sum(r['minute_net'] for r in rows),
                       difference=sum(r['difference'] for r in rows),
                       earlier_exits=sum(r['earlier_exit'] for r in rows),
                       ambiguous_baskets=sum(r['adverse_envelope_ambiguous'] for r in rows),
                       losses_over_budget=sum(r['realized_loss_over_budget'] for r in rows))
        reports[name] = dict(summary=summary, trades=rows)
        print(json.dumps(dict(run=name, **summary)), flush=True)
    files = ['results.json', 'ledgers.json', 'market_data.json', 'minute_data.json']
    result = dict(kind='Fixed-basket minute execution diagnostic, NOT full strategy replay',
                  methodology='Original entries, sizes and original exit deadlines frozen; gross +/-risk guards on synchronized 1m open/close. 5bps exit commission, 2bps slippage and 2bps half-spread per leg. No new entries, resizing or changed relationship signals.',
                  ambiguity='Adverse per-leg OHLC extrema are nonsynchronous possible-path bounds, never actual observed stop fills.',
                  funding='Original settlement marks used, including historical mark fallbacks already reported in the source dataset; entry boundary excluded, revised exit boundary included.',
                  source_funding_mark_fallbacks=source.get('funding_mark_fallbacks'),
                  limitations=['Fixed survivor universe remains biased', 'Minute endpoints can still miss subminute stops',
                               'Simultaneous two-leg fills remain assumed', 'Original closed baskets only; not portfolio counterfactual performance'],
                  hashes={name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files},
                  source_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), runs=reports)
    (root/'minute_execution.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    run(parser.parse_args().root)
