"""Public Binance spot/dated USD-M books. No authenticated endpoints or orders."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import math
import time
from urllib.parse import urlencode
from urllib.request import urlopen


SPOT = 'https://api.binance.com'
FUTURES = 'https://fapi.binance.com'


def public_get(host, path, params=None):
    url = host + path + ('?' + urlencode(params) if params else '')
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def common_step(*values):
    steps = [Decimal(str(v)) for v in values if Decimal(str(v)) > 0]
    if not steps:
        raise ValueError('Missing positive quantity step')
    digits = max(0, *[-s.as_tuple().exponent for s in steps])
    scale = 10**digits
    return str(Decimal(math.lcm(*(int(s*scale) for s in steps)))/scale)


def limits(symbol):
    filters = {f['filterType']: f for f in symbol['filters']}
    lots = [filters[k] for k in ('LOT_SIZE', 'MARKET_LOT_SIZE') if k in filters]
    if not lots:
        raise ValueError('Missing quantity filters')
    step = common_step(*(f['stepSize'] for f in lots))
    minimum = max(float(f['minQty']) for f in lots)
    maximum = min(float(f['maxQty']) for f in lots if float(f['maxQty']) > 0)
    notional = max([0.] + [float(f.get('minNotional', f.get('notional', 0)))
                           for k, f in filters.items() if k in ('MIN_NOTIONAL', 'NOTIONAL')])
    max_notional = min([float('inf')] + [float(f['maxNotional']) for k, f in filters.items()
                                       if k == 'NOTIONAL' and float(f.get('maxNotional', 0)) > 0])
    return dict(step=step, min_qty=minimum, max_qty=maximum,
                min_notional=notional, max_notional=None if math.isinf(max_notional) else max_notional)


def discover(spot_info, future_info, now):
    spots = {s['symbol']: s for s in spot_info['symbols'] if s['status'] == 'TRADING'
             and s.get('isSpotTradingAllowed', True)}
    contracts = []
    for future in future_info['symbols']:
        if not (future.get('contractType') in ('CURRENT_QUARTER', 'NEXT_QUARTER')
                and future.get('status') == 'TRADING' and future.get('baseAsset') in ('BTC', 'ETH')
                and future.get('marginAsset') == future.get('quoteAsset') == 'USDT'
                and now < future['deliveryDate']/1000 <= now+370*86400):
            continue
        spot = spots.get(future['baseAsset']+'USDT')
        if not spot:
            continue
        contracts.append(dict(id=future['symbol'], base=future['baseAsset'],
                              spot_symbol=spot['symbol'], expiry=future['deliveryDate']/1000,
                              contract_type=future['contractType'],
                              spot_limits=limits(spot), future_limits=limits(future)))
    return sorted(contracts, key=lambda row: row['id'])


def book(host, path, symbol):
    requested = time.time()
    raw = public_get(host, path, dict(symbol=symbol, limit=5))
    received = time.time()
    if not raw.get('bids') or not raw.get('asks'):
        raise ValueError('Missing bids/asks: '+symbol)
    return dict(bid=float(raw['bids'][0][0]), ask=float(raw['asks'][0][0]),
                bid_size=float(raw['bids'][0][1]), ask_size=float(raw['asks'][0][1]),
                ts=received, requested_at=requested,
                exchange_ts=raw.get('E', raw.get('T', 0))/1000 or None,
                timestamp_basis='exchange_and_local' if raw.get('E', raw.get('T')) else 'local_request_only')


def mark(symbol):
    raw = public_get(FUTURES, '/fapi/v1/premiumIndex', dict(symbol=symbol))
    return dict(price=float(raw['markPrice']), ts=float(raw['time'])/1000)


class BinanceCarryData:
    def __init__(self):
        self.contracts = []
        self.discovered_at = 0

    def snapshot(self):
        now = time.time()
        errors = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            if now-self.discovered_at >= 300:
                spot = pool.submit(public_get, SPOT, '/api/v3/exchangeInfo', {'symbols': '["BTCUSDT","ETHUSDT"]'})
                future = pool.submit(public_get, FUTURES, '/fapi/v1/exchangeInfo')
                self.contracts = discover(spot.result(), future.result(), now)
                self.discovered_at = time.time()
            tasks = {}
            for contract in self.contracts:
                tasks.setdefault(('spot', contract['spot_symbol']), None)
                tasks.setdefault(('future', contract['id']), None)
                tasks.setdefault(('mark', contract['id']), None)
            for kind, symbol in tasks:
                if kind == 'mark':
                    tasks[kind, symbol] = pool.submit(mark, symbol)
                    continue
                host, path = (SPOT, '/api/v3/depth') if kind == 'spot' else (FUTURES, '/fapi/v1/depth')
                tasks[kind, symbol] = pool.submit(book, host, path, symbol)
            quotes = {}
            for key, task in tasks.items():
                try:
                    quotes[key] = task.result()
                except Exception as exc:
                    errors.append(dict(market=key[0], symbol=key[1], error=str(exc)))
        rows = []
        for contract in self.contracts:
            rows.append(dict(contract, spot=quotes.get(('spot', contract['spot_symbol'])),
                             future=quotes.get(('future', contract['id'])), mark=quotes.get(('mark', contract['id']))))
        return dict(observed_at=time.time(), discovery_at=self.discovered_at, contracts=rows, errors=errors,
                    source='Binance public spot and linear USDT dated futures REST depth',
                    limitation='Spot depth has no exchange timestamp; local request age is only a freshness proxy.')
