"""Paper-trading simulator shared by the live bot and the backtest.

Both call `process`, so the backtest runs exactly the logic that runs live.

Fill rules:
  * Signals are decided at a candle's close and fill at the NEXT candle's open.
  * Each fill pays the token's one-way cost (DEX fee + price impact + network
    fees, measured from real Jupiter quotes) as a worse price.
  * Stop-loss / take-profit trigger on the candle's low/high. If one candle
    touches both, we assume the stop came first (the pessimistic case). If
    the candle opens past the stop, we fill at the open (a gap).
"""

from . import config as C


class Account:
    """Per-account settings the simulator needs."""

    def __init__(self, name):
        a = C.ACCOUNTS[name]
        self.costs = {m: v["cost"] for m, v in a["markets"].items()}
        self.risk = a["risk_per_trade"]
        self.max_fraction = a["max_position_fraction"]
        self.strat = C.make_strategy(name)


def new_state(now):
    return {
        "started": now,
        "cash": C.STARTING_CASH,
        "positions": {},      # symbol -> position dict
        "pending": {},        # symbol -> {"side": "buy"/"sell", "atr": float}
        "last_price": {},     # symbol -> last close
        "last_candle": {},    # symbol -> last processed candle time
        "trades": [],         # closed trades
        "equity": [],         # [time, equity]
    }


def equity(state):
    total = state["cash"]
    for sym, p in state["positions"].items():
        total += p["qty"] * state["last_price"].get(sym, p["entry"])
    return total


def _open(state, sym, c, atr_value, acct):
    strat = acct.strat
    cost = acct.costs[sym]
    price = c["o"] * (1 + cost)
    stop_dist = strat.stop_atr * atr_value
    if stop_dist <= 0 or price <= stop_dist:
        return
    eq = equity(state)
    qty = (eq * acct.risk) / stop_dist
    max_notional = min(eq * acct.max_fraction, state["cash"])
    qty = min(qty, max_notional / price)
    if qty * price < 10:  # not worth it / no cash
        return
    state["cash"] -= qty * price
    state["positions"][sym] = {
        "qty": qty, "entry": price, "opened": c["t"], "bars": 0,
        "stop": price - stop_dist, "high": c["o"],
        "target": price + strat.target_atr * atr_value if strat.target_atr else None,
    }


def _close(state, sym, price, t, reason, acct):
    p = state["positions"].pop(sym)
    price *= (1 - acct.costs[sym])
    state["cash"] += p["qty"] * price
    pnl = p["qty"] * (price - p["entry"])
    state["trades"].append({
        "symbol": sym, "qty": p["qty"], "entry": p["entry"], "exit": price,
        "opened": p["opened"], "closed": t, "pnl": pnl,
        "pnl_pct": (price / p["entry"] - 1) * 100, "reason": reason,
    })


def _step(state, sym, candles, ind, i, acct):
    strat = acct.strat
    c = candles[i]
    pend = state["pending"].pop(sym, None)

    # 1. Fill orders decided at the previous close.
    if pend and pend["side"] == "buy" and sym not in state["positions"]:
        _open(state, sym, c, pend["atr"], acct)
    elif pend and pend["side"] == "sell" and sym in state["positions"]:
        _close(state, sym, c["o"], c["t"], pend.get("reason", "signal"), acct)

    # 2. Stop-loss / take-profit inside this candle.
    p = state["positions"].get(sym)
    if p:
        if c["l"] <= p["stop"]:
            _close(state, sym, min(p["stop"], c["o"]), c["t"], "stop", acct)
        elif p["target"] and c["h"] >= p["target"]:
            _close(state, sym, max(p["target"], c["o"]), c["t"], "target", acct)

    state["last_price"][sym] = c["c"]
    state["last_candle"][sym] = c["t"]

    # 3. Decisions at this candle's close.
    if i < strat.warmup:
        return
    p = state["positions"].get(sym)
    if p:
        p["bars"] += 1
        p["high"] = max(p["high"], c["h"])
        if strat.trail_atr:
            p["stop"] = max(p["stop"], p["high"] - strat.trail_atr * ind["atr"][i])
        if strat.exit(ind, i):
            state["pending"][sym] = {"side": "sell", "reason": "signal"}
        elif strat.max_bars and p["bars"] >= strat.max_bars:
            state["pending"][sym] = {"side": "sell", "reason": "time"}
    elif strat.entry(ind, i):
        state["pending"][sym] = {"side": "buy", "atr": ind["atr"][i]}


def process(state, candles_by_sym, acct):
    """Feed every not-yet-processed candle through the strategy, in time order.

    `candles_by_sym` must include enough history before the new candles for
    the indicators to warm up.
    """
    if hasattr(acct.strat, "prepare_all"):  # strategies that compare markets
        ind = acct.strat.prepare_all(candles_by_sym)
    else:
        ind = {s: acct.strat.prepare(cs) for s, cs in candles_by_sym.items()}
    events = []
    for sym, cs in candles_by_sym.items():
        last = state["last_candle"].get(sym, -1)
        for i, c in enumerate(cs):
            if c["t"] > last:
                events.append((c["t"], sym, i))
    events.sort()
    for idx, (t, sym, i) in enumerate(events):
        _step(state, sym, candles_by_sym[sym], ind[sym], i, acct)
        if idx + 1 == len(events) or events[idx + 1][0] != t:
            state["equity"].append([t, round(equity(state), 2)])
    if len(state["equity"]) > C.MAX_EQUITY_POINTS:
        state["equity"] = state["equity"][-C.MAX_EQUITY_POINTS:]
    return len(events)


def stats(state, days=None):
    trades = state["trades"]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    peak, max_dd = 0.0, 0.0
    for _, e in state["equity"]:
        peak = max(peak, e)
        if peak:
            max_dd = max(max_dd, (peak - e) / peak)
    eq = equity(state)
    out = {
        "equity": round(eq, 2),
        "return_pct": round((eq / C.STARTING_CASH - 1) * 100, 2),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_trade_pct": round(sum(t["pnl_pct"] for t in trades) / len(trades), 3) if trades else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
    }
    if days:
        out["trades_per_day"] = round(len(trades) / days, 2)
    return out
