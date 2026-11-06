"""Backtest the exact live strategy on past data.

    python -m bot.backtest [days]
"""

import json
import os
import sys
import time

from . import config as C
from .data import fetch_candles
from .engine import new_state, process, stats

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "backtest.json")


def run(candles):
    state = new_state(0)
    process(state, candles)
    return state


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 1460
    end = int(time.time())
    end -= end % C.GRANULARITY
    start = end - days * 86400
    print(f"fetching {days} days of {C.GRANULARITY // 3600}h candles...")
    candles = {s: fetch_candles(s, C.GRANULARITY, start, end) for s in C.SYMBOLS}

    state = run(candles)
    result = stats(state)
    print("base case:", result)

    # How sensitive is the result to trading costs?
    sensitivity = []
    base_fee = C.FEE_RATE
    for fee in (0.0, 0.001, 0.0025, 0.006):
        C.FEE_RATE = fee
        s = stats(run(candles))
        sensitivity.append({"fee_pct": fee * 100, **s})
        print(f"fee {fee * 100:.2f}%/side:", s)
    C.FEE_RATE = base_fee

    # Buy-and-hold comparison: equal split across symbols.
    hold = sum(cs[-1]["c"] / cs[C.EMA_TREND]["c"] for cs in candles.values()) / len(candles)

    by_symbol = {}
    for t in state["trades"]:
        b = by_symbol.setdefault(t["symbol"], {"trades": 0, "pnl": 0.0})
        b["trades"] += 1
        b["pnl"] = round(b["pnl"] + t["pnl"], 2)

    with open(OUT, "w") as f:
        json.dump({
            "generated": int(time.time()), "days": days, "start": start, "end": end,
            "stats": result,
            "buy_and_hold_return_pct": round((hold - 1) * 100, 2),
            "fee_sensitivity": sensitivity,
            "by_symbol": by_symbol,
            "equity": state["equity"][::4],  # hourly points are plenty
            "trades": state["trades"][-200:],
        }, f, indent=1)
    print(f"buy & hold: {(hold - 1) * 100:.2f}%  -> wrote {OUT}")


if __name__ == "__main__":
    main()
