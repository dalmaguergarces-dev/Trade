"""Strategy and paper-trading simulator.

The live paper bot and the backtest both call `process`, so what you see in
the backtest is exactly the logic that runs live.

Rules (long-only):
  * Entry signal at a candle close: fast EMA crosses above slow EMA while
    price is above the trend EMA. The order fills at the NEXT candle's open.
  * Exit: stop-loss at entry - STOP_ATR*ATR, take-profit at
    entry + TARGET_ATR*ATR, or fast EMA crossing back below slow EMA
    (fills at the next open). If a candle touches both stop and target, we
    assume the stop was hit first (the pessimistic case).
"""

from . import config as C


def ema(values, period):
    k = 2 / (period + 1)
    out, cur = [], None
    for v in values:
        cur = v if cur is None else cur + k * (v - cur)
        out.append(cur)
    return out


def atr(candles, period):
    out, cur, prev_close = [], None, None
    for c in candles:
        tr = c["h"] - c["l"] if prev_close is None else max(
            c["h"] - c["l"], abs(c["h"] - prev_close), abs(c["l"] - prev_close))
        cur = tr if cur is None else cur + (tr - cur) / period
        out.append(cur)
        prev_close = c["c"]
    return out


def indicators(candles):
    closes = [c["c"] for c in candles]
    return {
        "fast": ema(closes, C.EMA_FAST),
        "slow": ema(closes, C.EMA_SLOW),
        "trend": ema(closes, C.EMA_TREND),
        "atr": atr(candles, C.ATR_PERIOD),
    }


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


def _open(state, sym, c, atr_value):
    price = c["o"] * (1 + C.SLIPPAGE)
    stop_dist = C.STOP_ATR * atr_value
    if stop_dist <= 0:
        return
    eq = equity(state)
    qty = (eq * C.RISK_PER_TRADE) / stop_dist
    max_notional = min(eq * C.MAX_POSITION_FRACTION, state["cash"] / (1 + C.FEE_RATE))
    qty = min(qty, max_notional / price)
    if qty * price < 10:  # not worth it / no cash
        return
    fee = qty * price * C.FEE_RATE
    state["cash"] -= qty * price + fee
    state["positions"][sym] = {
        "qty": qty, "entry": price, "opened": c["t"], "entry_fee": fee,
        "stop": price - stop_dist, "target": price + C.TARGET_ATR * atr_value,
    }


def _close(state, sym, price, t, reason):
    p = state["positions"].pop(sym)
    price *= (1 - C.SLIPPAGE)
    fee = p["qty"] * price * C.FEE_RATE
    state["cash"] += p["qty"] * price - fee
    pnl = p["qty"] * (price - p["entry"]) - fee - p["entry_fee"]
    state["trades"].append({
        "symbol": sym, "qty": p["qty"], "entry": p["entry"], "exit": price,
        "opened": p["opened"], "closed": t, "pnl": pnl,
        "pnl_pct": pnl / (p["qty"] * p["entry"]) * 100, "reason": reason,
    })


def _step(state, sym, candles, ind, i):
    c = candles[i]
    pend = state["pending"].pop(sym, None)

    # 1. Fill orders decided at the previous close.
    if pend and pend["side"] == "buy" and sym not in state["positions"]:
        _open(state, sym, c, pend["atr"])
    elif pend and pend["side"] == "sell" and sym in state["positions"]:
        _close(state, sym, c["o"], c["t"], "signal")

    # 2. Stop-loss / take-profit inside this candle.
    p = state["positions"].get(sym)
    if p:
        if c["l"] <= p["stop"]:
            _close(state, sym, min(p["stop"], c["o"]), c["t"], "stop")
        elif c["h"] >= p["target"]:
            _close(state, sym, max(p["target"], c["o"]), c["t"], "target")

    state["last_price"][sym] = c["c"]
    state["last_candle"][sym] = c["t"]

    # 3. Signals at this candle's close (need warm-up and a previous candle).
    if i < C.EMA_TREND or i == 0:
        return
    f0, s0 = ind["fast"][i - 1], ind["slow"][i - 1]
    f1, s1 = ind["fast"][i], ind["slow"][i]
    if sym in state["positions"]:
        if f0 >= s0 and f1 < s1:
            state["pending"][sym] = {"side": "sell"}
    elif f0 <= s0 and f1 > s1 and c["c"] > ind["trend"][i]:
        state["pending"][sym] = {"side": "buy", "atr": ind["atr"][i]}


def process(state, candles_by_sym):
    """Feed every not-yet-processed candle through the strategy, in time order.

    `candles_by_sym` must include enough history before the new candles for
    the indicators to warm up.
    """
    ind = {s: indicators(cs) for s, cs in candles_by_sym.items()}
    events = []
    for sym, cs in candles_by_sym.items():
        last = state["last_candle"].get(sym, -1)
        for i, c in enumerate(cs):
            if c["t"] > last:
                events.append((c["t"], sym, i))
    events.sort()
    for idx, (t, sym, i) in enumerate(events):
        _step(state, sym, candles_by_sym[sym], ind[sym], i)
        if idx + 1 == len(events) or events[idx + 1][0] != t:
            state["equity"].append([t, round(equity(state), 2)])
    if len(state["equity"]) > C.MAX_EQUITY_POINTS:
        state["equity"] = state["equity"][-C.MAX_EQUITY_POINTS:]
    return len(events)


def stats(state):
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
    return {
        "equity": round(eq, 2),
        "return_pct": round((eq / C.STARTING_CASH - 1) * 100, 2),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
    }
