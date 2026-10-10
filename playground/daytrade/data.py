"""Bulk download of public Binance USD-M 5m klines + funding from data.binance.vision.

Monthly archives are used where published; a missing month falls back to daily files.
Cached as pandas pickles under data/ (gitignored).
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import io
import os
import urllib.error
import urllib.request
import zipfile

import pandas as pd

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
BASE = 'https://data.binance.vision/data/futures/um'
UNIVERSE = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT',
            'DOGEUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'LTCUSDT']
START = date(2021, 11, 1)   # two months of indicator warmup before 2022
COLS = ['open_time', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume',
        'count', 'taker_buy_volume', 'taker_buy_quote_volume', 'ignore']


def _get(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
    return None


def _csv(blob, names):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        raw = z.read(z.namelist()[0])
    has_header = raw[:1].isalpha()
    return pd.read_csv(io.BytesIO(raw), header=0 if has_header else None,
                       names=None if has_header else names)


def _months(end):
    d = START
    while d <= end:
        yield d
        d = date(d.year + d.month // 12, d.month % 12 + 1, 1)


def _fetch_klines(symbol, end):
    frames = []
    for m in _months(end):
        blob = _get(f'{BASE}/monthly/klines/{symbol}/5m/{symbol}-5m-{m:%Y-%m}.zip')
        if blob is not None:
            frames.append(_csv(blob, COLS))
            continue
        if (end.year - m.year) * 12 + end.month - m.month > 1:
            continue        # a missing older month means "not listed yet"; daily files only for recent months
        d = m
        while d.month == m.month and d <= end:
            b = _get(f'{BASE}/daily/klines/{symbol}/5m/{symbol}-5m-{d:%Y-%m-%d}.zip')
            if b is not None:
                frames.append(_csv(b, COLS))
            d += timedelta(days=1)
    df = pd.concat(frames, ignore_index=True)
    df = df[['open_time', 'open', 'high', 'low', 'close', 'volume', 'quote_volume', 'taker_buy_volume']]
    df = df.astype({'open_time': 'int64'}).drop_duplicates('open_time').sort_values('open_time')
    df.index = pd.to_datetime(df.pop('open_time'), unit='ms', utc=True)
    return df.astype('float64')


def _fetch_funding(symbol, end):
    frames = []
    for m in _months(end):
        blob = _get(f'{BASE}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{m:%Y-%m}.zip')
        if blob is not None:
            frames.append(_csv(blob, ['calc_time', 'funding_interval_hours', 'last_funding_rate']))
    if not frames:
        return pd.Series(dtype='float64', name='funding')
    f = pd.concat(frames, ignore_index=True).drop_duplicates('calc_time').sort_values('calc_time')
    # calc_time can carry a few ms of jitter; snap to the minute.
    idx = pd.to_datetime(f['calc_time'].astype('int64') // 60000 * 60000, unit='ms', utc=True)
    return pd.Series(f['last_funding_rate'].astype('float64').values, index=idx, name='funding')


def download(symbols=UNIVERSE, end=None, skip_existing=False):
    end = end or date.today() - timedelta(days=1)
    os.makedirs(ROOT, exist_ok=True)
    if skip_existing:
        symbols = [s for s in symbols if not os.path.exists(os.path.join(ROOT, f'{s}_5m.pkl'))]

    def one(sym):
        k = _fetch_klines(sym, end)
        k.to_pickle(os.path.join(ROOT, f'{sym}_5m.pkl'))
        f = _fetch_funding(sym, end)
        f.to_pickle(os.path.join(ROOT, f'{sym}_funding.pkl'))
        return sym, len(k), k.index[0], k.index[-1], len(f)

    with ThreadPoolExecutor(5) as pool:
        for sym, n, a, b, nf in pool.map(one, symbols):
            print(f'{sym}: {n} bars {a} -> {b}, {nf} funding events', flush=True)


METRIC_COLS = {'sum_open_interest': 'oi', 'sum_open_interest_value': 'oi_value',
               'count_toptrader_long_short_ratio': 'top_ls_acct', 'sum_toptrader_long_short_ratio': 'top_ls_pos',
               'count_long_short_ratio': 'ls_acct', 'sum_taker_long_short_vol_ratio': 'taker_ratio'}


def _fetch_metrics_day(args):
    symbol, d = args
    blob = _get(f'{BASE}/daily/metrics/{symbol}/{symbol}-metrics-{d:%Y-%m-%d}.zip')
    if blob is None:
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))


def download_metrics(symbols=UNIVERSE, end=None, workers=24, skip_existing=False):
    """5m open interest / long-short ratios (published from Dec 2021) -> data/{SYM}_metrics.pkl."""
    end = end or date.today() - timedelta(days=1)
    with ThreadPoolExecutor(workers) as pool:
        for sym in symbols:
            if skip_existing and os.path.exists(os.path.join(ROOT, f'{sym}_metrics.pkl')):
                continue
            first = date(2021, 12, 1)
            kp = os.path.join(ROOT, f'{sym}_5m.pkl')
            if os.path.exists(kp):          # start at the listing date instead of probing 404s
                first = max(first, pd.read_pickle(kp).index[0].date())
            days = pd.date_range(first, end, freq='D').date
            frames = [f for f in pool.map(_fetch_metrics_day, [(sym, d) for d in days]) if f is not None]
            m = pd.concat(frames, ignore_index=True)
            m.index = pd.to_datetime(m.pop('create_time'), utc=True)
            m = m[list(METRIC_COLS)].rename(columns=METRIC_COLS).astype('float64')
            m = m[~m.index.duplicated()].sort_index()
            m.to_pickle(os.path.join(ROOT, f'{sym}_metrics.pkl'))
            print(f'{sym}: {len(m)} metric rows {m.index[0]} -> {m.index[-1]}', flush=True)


def load_metrics(symbol):
    path = os.path.join(ROOT, f'{symbol}_metrics.pkl')
    return pd.read_pickle(path) if os.path.exists(path) else None


def load(symbol):
    k = pd.read_pickle(os.path.join(ROOT, f'{symbol}_5m.pkl'))
    f = pd.read_pickle(os.path.join(ROOT, f'{symbol}_funding.pkl'))
    return k, f


if __name__ == '__main__':
    import sys
    download_metrics() if 'metrics' in sys.argv[1:] else download()
