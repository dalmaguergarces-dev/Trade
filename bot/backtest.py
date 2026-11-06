"""Backtest an account's exact live strategy on past data.

    python -m bot.backtest <account> [days]

Writes docs/data/<account>_backtest.json. Besides the full period it reports
the final third separately: when settings were tuned on earlier data, that
final stretch is the more honest test.
"""

import json
import os
import sys
import time

from . import config as C
from .engine import Account, new_state, process, stats

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "data")


def run(name, candles, cost_scale=1.0):
    acct = Account(name)
    acct.costs = {m: c * cost_scale for m, c in acct.costs.items()}
    state = new_state(0)
    process(state, candles, acct)
    return state


def hold_return(candles, warmup):
    return sum(cs[-1]["c"] / cs[warmup]["c"] for cs in candles.values()) / len(candles) * 100 - 100


def main():
    C.MAX_EQUITY_POINTS = 10**9  # keep the whole curve for stats and charts
    name = sys.argv[1]
    cfg = C.ACCOUNTS[name]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else cfg["backtest_days"]
    gran = cfg["granularity"]
    end = int(time.time())
    end -= end % gran
    start = end - days * 86400
    print(f"{name}: fetching {days} days of {gran // 60}m candles...")
    candles = {m: C.fetch(name, m, start, end) for m in cfg["markets"]}
    n = min(len(cs) for cs in candles.values())
    candles = {m: cs[-n:] for m, cs in candles.items()}
    warmup = Account(name).strat.warmup
    traded_days = (n - warmup) * gran / 86400

    state = run(name, candles)
    result = stats(state, traded_days)
    print("full period:", result)

    # Final third on its own (with warm-up history before it).
    cut = warmup + (n - warmup) * 2 // 3
    tail = {m: cs[cut - warmup:] for m, cs in candles.items()}
    tail_state = run(name, tail)
    tail_days = (n - cut) * gran / 86400
    tail_stats = stats(tail_state, tail_days)
    print("final third:", tail_stats)

    sensitivity = []
    for scale in (0.0, 1.0, 2.0):
        s = stats(run(name, candles, scale), traded_days)
        sensitivity.append({"cost_scale": scale, **s})
        print(f"costs x{scale}:", s)

    by_market = {}
    for t in state["trades"]:
        b = by_market.setdefault(t["symbol"], {"trades": 0, "pnl": 0.0})
        b["trades"] += 1
        b["pnl"] = round(b["pnl"] + t["pnl"], 2)

    step = max(1, len(state["equity"]) // 1500)
    out = {
        "account": name, "title": cfg["title"],
        "generated": int(time.time()), "days": round(traded_days, 1),
        "start": candles[next(iter(candles))][warmup]["t"], "end": end,
        "stats": result,
        "buy_and_hold_return_pct": round(hold_return(candles, warmup), 2),
        "final_third": {"days": round(tail_days, 1), "stats": tail_stats,
                        "buy_and_hold_return_pct": round(hold_return(tail, warmup), 2)},
        "cost_sensitivity": sensitivity,
        "by_market": by_market,
        "equity": state["equity"][::step],
        "trades": state["trades"][-200:],
    }
    path = os.path.join(DATA_DIR, f"{name}_backtest.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"buy & hold: {out['buy_and_hold_return_pct']}%  -> wrote {path}")


if __name__ == "__main__":
    main()
