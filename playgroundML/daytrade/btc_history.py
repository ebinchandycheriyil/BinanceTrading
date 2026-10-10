"""Long BTC history, 5m: Coinbase BTC-USD (mid-2015 .. Nov 2017) spliced to Binance BTCUSDT spot (Dec 2017 ..).

Binance BTC data only starts in Aug 2017 (spot, thin until late 2017) / 2019 (futures), so Coinbase fills
2015-2017. Volume levels differ between venues, so volume indicators are distorted for about a day after
the splice. Saved to data/BTCUSD_CB_5m.pkl in the same column layout as data.py.

  python -m daytrade.btc_history
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import time
import urllib.error
import urllib.request

import pandas as pd

from . import data
from .data import ROOT

SPLICE = '2017-12-01'
SPOT = 'https://data.binance.vision/data/spot'

URL = 'https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=300&start={}&end={}'
PATH = os.path.join(ROOT, 'BTCUSD_CB_5m.pkl')
ETH_PATH = os.path.join(ROOT, 'ETHUSDT_spot_5m.pkl')
STEP = pd.Timedelta(minutes=5 * 299)          # 300 candles per request (inclusive bounds)


def _chunk(start):
    end = start + STEP
    url = URL.format(start.strftime('%Y-%m-%dT%H:%M:%SZ'), end.strftime('%Y-%m-%dT%H:%M:%SZ'))
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            time.sleep(1.5 * (attempt + 1) if e.code == 429 else 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'failed: {url}')


def binance_spot(start=SPLICE, end='2026-10-01', symbol='BTCUSDT'):
    frames = []
    for m in pd.date_range(start, end, freq='MS', inclusive='left'):
        blob = data._get(f'{SPOT}/monthly/klines/{symbol}/5m/{symbol}-5m-{m:%Y-%m}.zip')
        if blob is None:      # current month: daily files
            for d in pd.date_range(m, min(m + pd.offsets.MonthEnd(0), pd.Timestamp(end) - pd.Timedelta(days=1)), freq='D'):
                b = data._get(f'{SPOT}/daily/klines/{symbol}/5m/{symbol}-5m-{d:%Y-%m-%d}.zip')
                if b is not None:
                    frames.append(data._csv(b, data.COLS))
            continue
        frames.append(data._csv(blob, data.COLS))
    df = pd.concat(frames, ignore_index=True)
    ts = df['open_time'].astype('int64')
    ts = ts.where(ts < 10**14, ts // 1000)            # 2025+ archives use microseconds
    df.index = pd.DatetimeIndex(pd.to_datetime(ts, unit='ms', utc=True)).as_unit('ns')
    df = df[['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'taker_buy_volume']].astype('float64')
    return df[~df.index.duplicated()].sort_index()


def download_hybrid(workers=6):
    cb_path = os.path.join(ROOT, 'BTCUSD_coinbase_2015_2017.pkl')
    if os.path.exists(cb_path):
        cb = pd.read_pickle(cb_path)
    else:
        cb = download(start='2015-06-01', end=SPLICE, workers=workers, path=cb_path)
    bn = binance_spot()
    df = pd.concat([cb[cb.index < pd.Timestamp(SPLICE, tz='UTC')], bn]).sort_index()
    df = df[~df.index.duplicated()]
    df.to_pickle(PATH)
    print(f'hybrid: {len(cb)} Coinbase + {len(bn)} Binance candles -> {len(df)} '
          f'({df.index[0]} -> {df.index[-1]})', flush=True)


def download(start='2015-01-01', end='2026-10-01', workers=4, path=PATH):
    starts = pd.date_range(start, end, freq=STEP + pd.Timedelta(minutes=5), tz='UTC')
    rows = []
    with ThreadPoolExecutor(workers) as pool:
        for i, part in enumerate(pool.map(_chunk, starts), 1):
            rows += part or []
            if i % 500 == 0:
                print(f'{i}/{len(starts)} requests, {len(rows)} candles', flush=True)
    # Coinbase row: [time, low, high, open, close, volume]
    df = pd.DataFrame(rows, columns=['ts', 'low', 'high', 'open', 'close', 'volume']).drop_duplicates('ts').sort_values('ts')
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop('ts'), unit='s', utc=True)).as_unit('ns')
    df = df[['open', 'high', 'low', 'close', 'volume']].astype('float64')
    df = df[df.index < pd.Timestamp(end, tz='UTC')]
    df['quote_volume'] = df['volume'] * df['close']
    df['taker_buy_volume'] = df['volume'] * 0.5      # not published by Coinbase candles
    df.to_pickle(path)
    full = pd.date_range(df.index[0], df.index[-1], freq='5min')
    print(f'saved {len(df)} candles {df.index[0]} -> {df.index[-1]}; '
          f'{100 * (1 - len(df) / len(full)):.2f}% of 5m slots missing (no trades or outages)', flush=True)
    return df


if __name__ == '__main__':
    download_hybrid()
