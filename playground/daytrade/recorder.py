"""Record public Binance futures data that has no free history: liquidations and order-book depth.

Streams (public websocket, no keys):
  !forceOrder@arr        liquidation orders, all symbols (Binance pushes at most one per symbol per second)
  btcusdt/ethusdt@depth10@500ms   top-10 book, sampled every 5 s per symbol
Writes daily CSVs to artifacts/daytrade/recorder/. Reconnects automatically. Stop: create recorder/STOP.

  cd playground && ..\\.venv\\Scripts\\python.exe -m daytrade.recorder
"""
import asyncio
import json
import os
import time

import aiohttp

# same folder as research.OUT, without importing pandas (keeps the recorder small on a 1 GB server)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'artifacts', 'daytrade', 'recorder')
BOOK_SYMBOLS = ['btcusdt', 'ethusdt']
URL = 'wss://fstream.binance.com/stream?streams=' + '/'.join(['!forceOrder@arr'] + [f'{s}@depth10@500ms' for s in BOOK_SYMBOLS])
SAMPLE_S = 5.0
HEAD = {'liquidations': 'ts_ms,symbol,side,price,qty,usd\n',
        'book': 'ts_ms,symbol,bid,ask,spread_bp,bid_qty10,ask_qty10,imbalance\n'}


def _write(kind, line):
    day = time.strftime('%Y-%m-%d', time.gmtime())
    path = os.path.join(OUT, f'{kind}_{day}.csv')
    new = not os.path.exists(path)
    with open(path, 'a') as fh:
        if new:
            fh.write(HEAD[kind])
        fh.write(line)


async def run():
    os.makedirs(OUT, exist_ok=True)
    last = {}
    counts = {'liq': 0, 'book': 0}
    while not os.path.exists(os.path.join(OUT, 'STOP')):
        try:
            async with aiohttp.ClientSession() as s, s.ws_connect(URL, heartbeat=30, timeout=30) as ws:
                print(time.strftime('%H:%M:%S'), 'connected', flush=True)
                async for msg in ws:
                    if os.path.exists(os.path.join(OUT, 'STOP')):
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    d = json.loads(msg.data); stream, p = d.get('stream', ''), d.get('data', {})
                    if stream == '!forceOrder@arr':
                        o = p['o']; px = float(o['ap'] or o['p']); q = float(o['z'] or o['q'])
                        _write('liquidations', f"{o['T']},{o['s']},{o['S']},{px},{q},{px * q:.2f}\n")
                        counts['liq'] += 1
                    elif '@depth' in stream:
                        sym = stream.split('@')[0]
                        now = time.time()
                        if now - last.get(sym, 0) < SAMPLE_S:
                            continue
                        last[sym] = now
                        bids, asks = p['b'], p['a']
                        if not bids or not asks:
                            continue
                        bid, ask = float(bids[0][0]), float(asks[0][0])
                        bq = sum(float(x[1]) for x in bids); aq = sum(float(x[1]) for x in asks)
                        _write('book', f"{p['E']},{sym.upper()},{bid},{ask},{(ask - bid) / bid * 1e4:.3f},"
                                       f"{bq:.4f},{aq:.4f},{(bq - aq) / (bq + aq):.4f}\n")
                        counts['book'] += 1
                        if counts['book'] % 720 == 0:     # ~ every 30 min
                            print(time.strftime('%H:%M:%S'), counts, flush=True)
        except Exception as e:
            print(time.strftime('%H:%M:%S'), 'reconnect after', repr(e), flush=True)
            await asyncio.sleep(5)
    print('stopped', counts, flush=True)


if __name__ == '__main__':
    import sys
    os.makedirs(OUT, exist_ok=True)
    _lock = open(os.path.join(OUT, 'run.lock'), 'a+')
    try:
        if os.name == 'nt':
            import msvcrt; msvcrt.locking(_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl; fcntl.flock(_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f'Another recorder is already writing to {OUT}; not starting a second copy.')
    asyncio.run(run())
