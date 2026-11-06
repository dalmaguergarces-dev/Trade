"""One paper-trading tick. Run hourly; it acts when a new 6h candle closes: python -m bot.run"""

import json
import os
import time

from . import config as C
from .data import fetch_candles
from .engine import new_state, process, stats, equity

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "state.json")
HISTORY = 600  # candles of history for indicator warm-up


def main():
    now = int(time.time())
    fresh = not os.path.exists(STATE_PATH)
    if fresh:
        state = new_state(now)
    else:
        with open(STATE_PATH) as f:
            state = json.load(f)

    end = now - now % C.GRANULARITY
    start = end - HISTORY * C.GRANULARITY
    candles = {s: fetch_candles(s, C.GRANULARITY, start, end) for s in C.SYMBOLS}

    if fresh:
        # Start trading from now on; don't pretend we traded the past.
        for s, cs in candles.items():
            state["last_candle"][s] = cs[-1]["t"]
            state["last_price"][s] = cs[-1]["c"]
        state["equity"].append([end, round(equity(state), 2)])

    n = process(state, candles)
    if n == 0 and not fresh:
        print("no new closed candle; nothing to do")
        return
    state["updated"] = now
    state["stats"] = stats(state)
    state["config"] = {k: getattr(C, k) for k in dir(C) if k.isupper()}

    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=1)
    print(f"processed {n} candle events; {state['stats']}")


if __name__ == "__main__":
    main()
