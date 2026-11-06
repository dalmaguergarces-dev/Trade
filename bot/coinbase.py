"""Public candle data from Coinbase Exchange (no API key needed).

fetch_candles(product, granularity, start, end) has the same shape as
bot.data.fetch_candles, so accounts can use either source.
"""

import json
import time
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.exchange.coinbase.com"
MAX_PER_REQUEST = 300


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "paper-trading-bot"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def fetch_candles(symbol, granularity, start, end):
    """Closed candles with start <= time < end, oldest first.

    Each candle is a dict: t (open time, unix s), o, h, l, c, v.
    """
    out = {}
    chunk = granularity * MAX_PER_REQUEST
    cursor = start
    while cursor < end:
        stop = min(cursor + chunk, end)
        url = (f"{BASE}/products/{symbol}/candles?granularity={granularity}"
               f"&start={_iso(cursor)}&end={_iso(stop)}")
        for t, low, high, open_, close, vol in _get(url):
            out[t] = {"t": t, "o": open_, "h": high, "l": low, "c": close, "v": vol}
        cursor = stop
        time.sleep(0.15)  # stay well under the public rate limit
    now = time.time()
    return [out[t] for t in sorted(out)
            if start <= t < end and t + granularity <= now]
