"""Live trading with the bot's own Solana wallet.

    LIVE_TRADING=dry|on SOLANA_BOT_KEY=... python -m bot.live

Follows the same strategy as the solana_15m paper account, but places real
Jupiter swaps between USDC and each token, within the hard limits in
config.LIVE. Modes:
  dry  build, sign and *simulate* each swap; nothing is sent.
  on   send real transactions.
Selling is always allowed; the loss limits only block new buys.
"""

import json
import os
import sys
import time

from . import config as C
from .solana_wallet import SOL, USDC, SwapError, Wallet

PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "solana_live.json")
MAX_LOG = 100


def utc_day(t):
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def log(state, msg, **extra):
    print(msg)
    state["log"].append({"t": int(time.time()), "msg": msg, **extra})
    state["log"] = state["log"][-MAX_LOG:]


def main():
    mode = os.environ.get("LIVE_TRADING", "off").strip().lower()
    if mode not in ("dry", "on"):
        print("LIVE_TRADING is not 'dry' or 'on'; live trading is off.")
        return
    secret = os.environ.get("SOLANA_BOT_KEY", "")
    if not secret:
        print("SOLANA_BOT_KEY secret is not set; nothing to do.")
        return
    wallet = Wallet(secret, os.environ.get("SOLANA_RPC_URL") or None)
    L = C.LIVE
    acct = C.ACCOUNTS[L["strategy_account"]]
    markets = acct["markets"]
    strat = C.make_strategy(L["strategy_account"])
    now = int(time.time())

    if os.path.exists(PATH):
        with open(PATH) as f:
            state = json.load(f)
    else:
        state = {"started": now, "positions": {}, "trades": [], "last_candle": {},
                 "equity": [], "log": [], "halt": None}
    if state.get("wallet") not in (None, wallet.pubkey):
        # A different wallet: keep the old history out of this one's numbers.
        state = {"started": now, "positions": {}, "trades": [], "last_candle": {},
                 "equity": [], "log": [], "halt": None}
    state["wallet"] = wallet.pubkey
    state["mode"] = mode
    state["limits"] = L

    # ---- prices and balances ------------------------------------------------
    gran = acct["granularity"]
    end = now - now % gran
    start = end - acct["history"] * gran
    candles = {m: C.fetch(L["strategy_account"], m, start, end) for m in markets}
    price = {m: cs[-1]["c"] for m, cs in candles.items()}

    usdc = wallet.token_raw(USDC) / 1e6
    sol_raw = wallet.sol_lamports()
    sol_px = price.get("SOL")
    holdings = {}
    for m, info in markets.items():
        if m == "SOL":
            continue
        holdings[m] = wallet.token_raw(info["mint"]) / 10 ** wallet.decimals(info["mint"])
    value = usdc + sol_raw / 1e9 * (sol_px or 0) + sum(q * price[m] for m, q in holdings.items())
    state["balances"] = {"USDC": round(usdc, 4), "SOL": sol_raw / 1e9,
                         **{m: q for m, q in holdings.items() if q}}
    state["value"] = round(value, 2)
    state["prices"] = price
    # Loss limits measure from when the wallet was first funded.
    if (state.get("start_value") or 0) < L["min_trade_usd"]:
        state["start_value"] = round(value, 2)

    # ---- loss limits (block new buys only) -----------------------------------
    day = utc_day(now)
    if state.get("day") != day:
        state["day"], state["day_start_value"] = day, round(value, 2)
        if state.get("halt") and state["halt"].get("until_next_day"):
            state["halt"] = None
    if state["start_value"] and value < state["start_value"] * (1 - L["total_loss_limit_pct"] / 100):
        if not state.get("halt") or state["halt"].get("until_next_day"):
            state["halt"] = {"reason": f"wallet down {L['total_loss_limit_pct']}% from start; "
                                       "delete 'halt' and 'start_value' in solana_live.json to reset"}
            log(state, "HALTED: total loss limit reached")
    elif value < state["day_start_value"] - L["daily_loss_limit_usd"] and not state.get("halt"):
        state["halt"] = {"reason": f"lost over ${L['daily_loss_limit_usd']:.0f} today", "until_next_day": True}
        log(state, "Paused new buys for today: daily loss limit reached")

    # ---- trading -----------------------------------------------------------
    def buy(m, atr_value, px):
        info = markets[m]
        stop_dist = strat.stop_atr * atr_value
        usd = min(value * L["risk_per_trade"] / (stop_dist / px) if stop_dist > 0 else 0,
                  value * L["max_position_fraction"], L["max_trade_usd"], usdc - 0.01)
        if usd < L["min_trade_usd"]:
            log(state, f"Skipped {m} buy: only ${usd:.2f} available/allowed")
            return
        q = wallet.quote(USDC, info["mint"], int(usd * 1e6), L["slippage_bps"])
        impact = float(q.get("priceImpactPct") or 0) * 100
        if impact > L["max_price_impact_pct"]:
            log(state, f"Skipped {m} buy: price impact {impact:.2f}% too high")
            return
        if mode == "dry":
            sim = wallet.simulate(q)
            log(state, f"[dry run] would buy ${usd:.2f} of {m}; simulation "
                       f"{'OK' if sim['ok'] else 'failed: ' + str(sim['err'])}")
            return
        r = wallet.execute(q, USDC, info["mint"])
        spent, qty = r["spent_raw"] / 1e6, r["received_raw"] / 10 ** wallet.decimals(info["mint"])
        entry = spent / qty
        state["positions"][m] = {"qty_raw": r["received_raw"], "qty": qty, "cost": spent, "entry": entry,
                                 "stop": entry - stop_dist, "opened": now, "bars": 0, "high": entry,
                                 "target": entry + strat.target_atr * atr_value if strat.target_atr else None,
                                 "tx": r["signature"]}
        log(state, f"Bought {qty:.6g} {m} for ${spent:.2f}", tx=r["signature"])

    def sell(m, reason):
        info, p = markets[m], state["positions"][m]
        amount = p["qty_raw"]
        if m == "SOL":  # never sell the fee reserve
            amount = min(amount, wallet.sol_lamports() - int(L["sol_reserve"] * 1e9))
        else:
            amount = min(amount, wallet.token_raw(info["mint"]))
        if amount <= 0:
            log(state, f"{m} position not found in wallet; removing it")
            state["positions"].pop(m)
            return
        q = wallet.quote(info["mint"], USDC, amount, L["slippage_bps"])
        if mode == "dry":
            sim = wallet.simulate(q)
            log(state, f"[dry run] would sell {m} ({reason}); simulation "
                       f"{'OK' if sim['ok'] else 'failed: ' + str(sim['err'])}")
            return
        r = wallet.execute(q, info["mint"], USDC)
        got = r["received_raw"] / 1e6
        sold_frac = r["spent_raw"] / p["qty_raw"]
        pnl = got - p["cost"] * sold_frac
        state["trades"].append({"symbol": m, "qty": p["qty"] * sold_frac, "entry": p["entry"],
                                "exit": got / (p["qty"] * sold_frac), "opened": p["opened"], "closed": now,
                                "pnl": pnl, "pnl_pct": pnl / (p["cost"] * sold_frac) * 100,
                                "reason": reason, "tx_buy": p.get("tx"), "tx_sell": r["signature"]})
        state["positions"].pop(m)
        log(state, f"Sold {m} ({reason}) for ${got:.2f}, P&L ${pnl:+.2f}", tx=r["signature"])

    for m, cs in candles.items():
        if not cs:
            continue
        ind = strat.prepare(cs)
        i = len(cs) - 1
        last = state["last_candle"].get(m)
        state["last_candle"][m] = cs[i]["t"]
        if last is None:
            continue  # first run: start from the next candle
        new = [c for c in cs if c["t"] > last]
        if not new:
            continue
        try:
            p = state["positions"].get(m)
            if p:
                p["bars"] += len(new)
                p["high"] = max([p["high"]] + [c["h"] for c in new])
                if strat.trail_atr:
                    p["stop"] = max(p["stop"], p["high"] - strat.trail_atr * ind["atr"][i])
                reason = None
                if min(c["l"] for c in new) <= p["stop"]:
                    reason = "stop"
                elif p.get("target") and max(c["h"] for c in new) >= p["target"]:
                    reason = "target"
                elif strat.exit(ind, i):
                    reason = "signal"
                elif strat.max_bars and p["bars"] >= strat.max_bars:
                    reason = "time"
                if reason:
                    sell(m, reason)
            elif (not state.get("halt") and len(state["positions"]) < L["max_open_positions"]
                  and i >= strat.warmup and strat.entry(ind, i)):
                buy(m, ind["atr"][i], cs[i]["c"])
        except SwapError as e:
            log(state, f"{m}: swap error: {e}")

    state["equity"].append([now, state["value"]])
    state["equity"] = state["equity"][-20000:]
    state["updated"] = now
    with open(PATH, "w") as f:
        json.dump(state, f, separators=(",", ":"))
    print(f"wallet {wallet.pubkey} value ${state['value']} mode={mode} positions={list(state['positions'])}")


if __name__ == "__main__":
    try:
        main()
    except SwapError as e:
        print(f"live: error: {e}")
        sys.exit(1)
