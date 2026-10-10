"""Fixed trade-flow hypotheses, research only. python -m scalp.flow_study download|run"""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import math
from pathlib import Path
import statistics

from . import fee_study
from .experiments import stamp
from .market import BinanceData
from .settings import Settings
from .signals import Candle, Signal

ROOT = Path('artifacts/round4')
NAMES = ('absorption_reversal', 'quiet_shock_reversal', 'flow_continuation')


def candidates():
    return {name: replace(Settings(), min_hold_minutes=15, min_stop_fraction=.001,
                          min_target_cost_ratio=2, min_net_reward_risk=1,
                          max_daily_entries=6, daily_fee_budget_fraction=.003) for name in NAMES}


def protocol():
    return dict(version=4, start=stamp('2026-05-31'), split=stamp('2026-06-07'),
                validation=stamp('2026-06-14'), end=stamp('2026-06-28'),
                candidates={k: asdict(v) for k, v in candidates().items()},
                signal_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                selection='Maximum worst-development-week net; fixed before validation; no parameter search',
                gate='Each dev fold >=10 trades and positive net; validation >=30 trades, positive net, PF>=1.15, daily bootstrap lower95>0, doubled-cost same-trade net>0',
                fill_model='5bps taker per fill, 4bps spread, 2bps slippage per side; actual funding; next-minute entry; stop-first ties',
                evidence='Retrospective untouched interval; hypotheses inspired by literature, not replications or profit forecasts')


def parse_row(row):
    candle = Candle(int(row[0]), *map(float, row[1:6]))
    buy = float(row[9])
    if not math.isfinite(buy) or buy < 0 or buy > candle.volume:
        raise ValueError('Invalid taker-buy base volume')
    return dict(candle=asdict(candle), buy=buy)


def download():
    ROOT.mkdir(parents=True, exist_ok=True)
    fee_study.save('protocol.json', protocol())
    plan = protocol()
    start, end = plan['start'] - 400 * 60000, plan['end']
    market = BinanceData(Settings().symbols)
    raw = dict(source='Binance public USD-M raw klines; taker-buy BASE volume column 9', data={}, flow={}, funding={})
    for symbol in Settings().symbols:
        rows, cursor = {}, start
        while cursor < end:
            page = market.exchange.fapiPublicGetKlines(dict(symbol=market.exchange.market(symbol)['id'],
                interval='1m', startTime=cursor, endTime=end - 1, limit=1000))
            if not page:
                raise ValueError('Missing kline page')
            for row in page:
                parsed = parse_row(row)
                if start <= parsed['candle']['ts'] < end:
                    rows[parsed['candle']['ts']] = parsed
            next_cursor = int(page[-1][0]) + 60000
            if next_cursor <= cursor:
                raise ValueError('Pagination failed to advance')
            cursor = next_cursor
        if sorted(rows) != list(range(start, end, 60000)):
            raise ValueError('Incomplete minute history')
        raw['data'][symbol] = [rows[t]['candle'] for t in sorted(rows)]
        raw['flow'][symbol] = {str(t): rows[t]['buy'] for t in sorted(rows)}
        raw['funding'][symbol] = market.funding_history(symbol, start, end)
        if not raw['funding'][symbol]:
            raise ValueError('Missing funding')
        fee_study.output(event='DOWNLOAD', symbol=symbol, bars=len(rows))
    fee_study.save('market_data.json', raw)


def signals(history, buys, cfg):
    result = {n: Signal(reason='no flow setup') for n in NAMES}
    if len(history) < 65:
        return result
    rows = history[-65:]
    if any(b.ts-a.ts != 60000 for a,b in zip(rows, rows[1:])):
        raise ValueError('Noncontiguous flow history')
    c, p = rows[-1], rows[-2]
    base = rows[-62:-2]
    volume = statistics.mean(b.volume for b in base)
    atr = statistics.mean(max(b.high-b.low, abs(b.high-a.close), abs(b.low-a.close))
                          for a,b in zip(rows[-16:-2], rows[-15:-1]))
    if not volume or not atr or not c.volume or not p.volume:
        return result
    flow = 2 * buys[c.ts] / c.volume - 1
    prior_flow = 2 * buys[p.ts] / p.volume - 1
    high, low = max(b.high for b in rows[-22:-2]), min(b.low for b in rows[-22:-2])
    vwap = sum(b.close*b.volume for b in base) / sum(b.volume for b in base)
    plans = []
    # Aggressive sales/buys fail to sustain a fresh extreme; next bar confirms.
    if p.volume >= 1.5*volume:
        if p.low < low and p.close > low and prior_flow < -.2 and c.close > p.close and flow > 0:
            plans.append(('absorption_reversal', 1, p.low-.25*atr, vwap))
        if p.high > high and p.close < high and prior_flow > .2 and c.close < p.close and flow < 0:
            plans.append(('absorption_reversal', -1, p.high+.25*atr, vwap))
    # Three-bar displacement in thin recent volume, then opposing aggressor flow.
    returns = [math.log(b.close/a.close) for a,b in zip(base, base[1:])]
    sigma = statistics.pstdev(returns)
    shock = math.log(p.close/rows[-5].close)
    thin = statistics.mean(b.volume for b in rows[-4:-1]) < .8*volume
    if thin and sigma and abs(shock) > 3*sigma*math.sqrt(3):
        if shock < 0 and c.close > p.close and flow > .1:
            plans.append(('quiet_shock_reversal', 1, min(b.low for b in rows[-4:])-.25*atr, rows[-5].close))
        if shock > 0 and c.close < p.close and flow < -.1:
            plans.append(('quiet_shock_reversal', -1, max(b.high for b in rows[-4:])+.25*atr, rows[-5].close))
    # Participation persists after a break; continuation instead of fading shocks.
    if p.volume > 1.5*volume and c.volume > volume:
        if p.close > high and c.close > p.close and prior_flow > .2 and flow > .2:
            stop = min(p.low,c.low)-.25*atr
            plans.append(('flow_continuation', 1, stop, c.close+2*(c.close-stop)))
        if p.close < low and c.close < p.close and prior_flow < -.2 and flow < -.2:
            stop = max(p.high,c.high)+.25*atr
            plans.append(('flow_continuation', -1, stop, c.close-2*(stop-c.close)))
    for name, sign, stop, target in plans:
        risk = sign*(c.close-stop)/c.close
        if not cfg.min_stop_fraction <= risk <= cfg.max_stop_fraction or sign*(target-c.close) <= 0:
            result[name] = Signal(reason='invalid flow structural levels')
        else:
            result[name] = Signal('BUY' if sign == 1 else 'SELL', name, risk,
                dict(taker_imbalance=flow, prior_taker_imbalance=prior_flow), stop, target)
    return result


def cache(data, cfg, start, end):
    flow = json.loads((ROOT/'market_data.json').read_text())['flow']
    caches = {n: {} for n in NAMES}
    for symbol, rows in data.items():
        buys = {int(k): v for k,v in flow[symbol].items()}
        for i, bar in enumerate(rows):
            if start <= bar.ts < end:
                found = signals(rows[max(0,i-65):i], buys, cfg)
                for n in NAMES:
                    caches[n][symbol,bar.ts] = found[n]
    return caches


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('download','run'))
    command = parser.parse_args().command
    # Reuse the established chronological executor/report, keeping new protocol isolated.
    fee_study.ROOT, fee_study.NAMES = ROOT, NAMES
    fee_study.candidates, fee_study.protocol, fee_study.cache = candidates, protocol, cache
    original_report = fee_study.report
    def report(result):
        original_report(result)
        path = ROOT/'REPORT.md'
        content = path.read_text(encoding='utf-8').replace('Fee-aware chart studies', 'Trade-flow hypothesis study')
        content = content.replace('July 31–August 14, 2026 development; August 14–28 validation',
                                  'May 31–June 14, 2026 development; June 14–28 validation')
        content = content.replace('Five-minute studies use only completed bars', 'Flow studies use only completed one-minute bars')
        path.write_text(content+'\n\nResearch only: these flow signals are not connected to the live paper CLI. Trade-volume imbalance is not full order-book imbalance.\n', encoding='utf-8')
    fee_study.report = report
    download() if command == 'download' else fee_study.run()


if __name__ == '__main__':
    main()
