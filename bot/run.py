"""One paper-trading tick for every account. Run every 15 minutes:

    python -m bot.run [account ...]

An account only acts when one of its candles has closed since the last run.
"""

import json
import os
import sys
import time

from . import config as C
from .engine import Account, new_state, process, stats, equity

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")


def tick(name):
    acct_cfg = C.ACCOUNTS[name]
    path = os.path.join(DATA_DIR, f"{name}.json")
    now = int(time.time())
    gran = acct_cfg["granularity"]
    end = now - now % gran
    fresh = not os.path.exists(path)
    if fresh:
        state = new_state(now)
    else:
        with open(path) as f:
            state = json.load(f)
        # Nothing new to do until another candle closes.
        if state["last_candle"] and min(state["last_candle"].values()) + 2 * gran > end:
            print(f"{name}: no new closed candle")
            return

    start = end - acct_cfg["history"] * gran
    candles = {m: C.fetch(name, m, start, end) for m in acct_cfg["markets"]}

    if fresh:
        # Start trading from now on; don't pretend we traded the past.
        for m, cs in candles.items():
            state["last_candle"][m] = cs[-1]["t"]
            state["last_price"][m] = cs[-1]["c"]
        state["equity"].append([cs[-1]["t"], round(equity(state), 2)])

    n = process(state, candles, Account(name))
    if n == 0 and not fresh:
        print(f"{name}: no new closed candle")
        return
    state["account"] = name
    state["title"] = acct_cfg["title"]
    state["updated"] = now
    state["stats"] = stats(state, max(now - state["started"], 1) / 86400)
    state["config"] = {k: v for k, v in acct_cfg.items() if k != "markets"}
    state["config"]["markets"] = list(acct_cfg["markets"])
    state["config"]["costs"] = {m: v["cost"] for m, v in acct_cfg["markets"].items()}
    state["config"]["STARTING_CASH"] = C.STARTING_CASH

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, separators=(",", ":"))
    print(f"{name}: processed {n} candle events; {state['stats']}")


def main():
    names = sys.argv[1:] or list(C.ACCOUNTS)
    failed = []
    for name in names:
        try:
            tick(name)
        except Exception as e:  # one account's data outage shouldn't stop the others
            print(f"{name}: FAILED: {e!r}")
            failed.append(name)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
