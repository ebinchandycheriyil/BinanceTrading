"""CCXT public market data only: no keys, private calls, or order methods."""
import time
from .engine import Quote
from .signals import Candle


class BinanceData:
    def __init__(self, symbols):
        import ccxt
        self.exchange = ccxt.binanceusdm({"enableRateLimit": True, "timeout": 10000})
        self.exchange.load_markets()
        for symbol in symbols:
            market = self.exchange.market(symbol)
            if not (market.get("active") and market.get("linear") and market.get("swap")):
                raise ValueError(f"Not an active linear perpetual: {symbol}")

    def quote(self, symbol):
        book = self.exchange.fetch_order_book(symbol, limit=5)
        if not book["bids"] or not book["asks"] or not book.get("timestamp"):
            raise ValueError("Missing exchange order-book timestamp or liquidity")
        return Quote(float(book["bids"][0][0]), float(book["asks"][0][0]),
                     book["timestamp"] / 1000, float(book["bids"][0][1]), float(book["asks"][0][1]))

    def candles(self, symbol, since=None, limit=400):
        raw = self.exchange.fetch_ohlcv(symbol, "1m", since=since, limit=limit)
        now_ms = int(time.time() * 1000)
        return [Candle(int(r[0]), *map(float, r[1:6])) for r in raw if r[0] + 60000 <= now_ms]

    def round_qty(self, symbol, qty):
        return float(self.exchange.amount_to_precision(symbol, qty))

    def minimums(self, symbol):
        limits = self.exchange.market(symbol)["limits"]
        return dict(min_notional=limits.get("cost", {}).get("min") or 5.0,
                    min_qty=limits.get("amount", {}).get("min") or 0.0)

    def history(self, symbol, start_ms, end_ms):
        result = {}
        cursor = start_ms
        while cursor < end_ms:
            rows = self.candles(symbol, since=cursor, limit=1000)
            if not rows:
                break
            for c in rows:
                if start_ms <= c.ts < end_ms:
                    result[c.ts] = c
            next_cursor = rows[-1].ts + 60000
            if next_cursor <= cursor:
                raise ValueError("Market data pagination did not advance")
            cursor = next_cursor
        return [result[k] for k in sorted(result)]

    def funding_history(self, symbol, start_ms, end_ms):
        rows = self.exchange.fapiPublicGetFundingRate({
            "symbol": self.exchange.market(symbol)["id"], "startTime": start_ms,
            "endTime": end_ms - 1, "limit": 1000})
        if len(rows) == 1000:
            raise ValueError("Funding response may be truncated; shorten the data range")
        return [dict(ts=int(r["fundingTime"]), rate=float(r["fundingRate"]),
                     mark=float(r["markPrice"])) for r in rows]
