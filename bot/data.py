"""Solana DEX candles from GeckoTerminal's free public API (no key needed).

Prices are in USD from the token's most liquid Solana pool. The free API
serves the last 180 days and about 30 requests per minute.
"""

import json
import time
import urllib.error
import urllib.request

BASE = "https://api.geckoterminal.com/api/v2/networks/solana/pools"
MAX_PER_REQUEST = 1000
_last_call = [0.0]


def _get(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json;version=20230203",
        "User-Agent": "paper-trading-bot",
    })
    for attempt in range(6):
        wait = 2.2 - (time.time() - _last_call[0])  # ~27 requests/minute
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(10 * (attempt + 1))
        except Exception:
            if attempt == 5:
                raise
            time.sleep(3 * (attempt + 1))


def fetch_candles(pool, granularity, start, end):
    """Closed candles with start <= time < end, oldest first, on a gap-free grid.

    Each candle is a dict: t (open time, unix s), o, h, l, c, v (USD volume).
    Intervals with no trades are filled with a flat candle at the last close.
    """
    minutes = granularity // 60
    raw = {}
    before = end
    while before > start:
        url = (f"{BASE}/{pool}/ohlcv/minute?aggregate={minutes}"
               f"&limit={MAX_PER_REQUEST}&before_timestamp={before}&currency=usd")
        rows = _get(url)["data"]["attributes"]["ohlcv_list"]
        if not rows:
            break
        for t, o, h, l, c, v in rows:
            raw[int(t)] = {"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": v}
        oldest = min(int(r[0]) for r in rows)
        if oldest >= before:
            break
        before = oldest

    now = time.time()
    times = [t for t in sorted(raw) if start <= t < end and t + granularity <= now]
    if not times:
        return []
    out, prev = [], None
    t = times[0]
    while t <= times[-1]:
        c = raw.get(t)
        if c is None:
            c = {"t": t, "o": prev["c"], "h": prev["c"], "l": prev["c"], "c": prev["c"], "v": 0.0}
        out.append(c)
        prev = c
        t += granularity
    return out
