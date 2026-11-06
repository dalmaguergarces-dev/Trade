"""Trading strategies. Each one decides entries/exits at a candle's close.

A strategy provides:
  prepare(candles) -> dict of indicator lists (same length as candles)
  entry(ind, i)    -> True to buy at the next candle's open
  exit(ind, i)     -> True to sell at the next candle's open
and risk settings: stop_atr, target_atr (None = no target), trail_atr
(None = no trailing stop) and max_bars (None = no time limit).
"""


def ema(values, period):
    k = 2 / (period + 1)
    out, cur = [], None
    for v in values:
        cur = v if cur is None else cur + k * (v - cur)
        out.append(cur)
    return out


def sma(values, period):
    out, total = [], 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        out.append(total / min(i + 1, period))
    return out


def stdev(values, period, means):
    out = []
    for i in range(len(values)):
        w = values[max(0, i - period + 1):i + 1]
        m = means[i]
        out.append((sum((x - m) ** 2 for x in w) / len(w)) ** 0.5)
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


def rsi(closes, period):
    out, gain, loss = [], None, None
    for i, c in enumerate(closes):
        if i == 0:
            out.append(50.0)
            continue
        ch = c - closes[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if gain is None:
            gain, loss = g, l
        else:
            gain += (g - gain) / period
            loss += (l - loss) / period
        out.append(100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return out


class Strategy:
    name = "base"
    warmup = 200
    stop_atr = 2.0
    target_atr = None
    trail_atr = None
    max_bars = None

    def __init__(self, **params):
        for k, v in params.items():
            setattr(self, k, v)
        # Long EMAs need enough history before their values mean anything.
        self.warmup = max(self.warmup, getattr(self, "trend", 0))

    def describe(self):
        keys = [k for k in vars(self)]
        return self.name + "(" + ", ".join(f"{k}={getattr(self, k)}" for k in keys) + ")"


class TrendEMA(Strategy):
    """Buy when the fast EMA crosses above the slow one in an uptrend."""
    name = "trend_ema"
    fast, slow, trend = 10, 50, 200
    target_atr = 4.0

    def prepare(self, cs):
        closes = [c["c"] for c in cs]
        return {"c": closes, "fast": ema(closes, self.fast), "slow": ema(closes, self.slow),
                "trend": ema(closes, self.trend), "atr": atr(cs, 14)}

    def entry(self, d, i):
        return (d["fast"][i - 1] <= d["slow"][i - 1] and d["fast"][i] > d["slow"][i]
                and d["c"][i] > d["trend"][i])

    def exit(self, d, i):
        return d["fast"][i] < d["slow"][i]


class DipBuy(Strategy):
    """Buy a sharp short-term dip while the bigger trend is up; sell on the bounce.

    Entry: price closes below the lower Bollinger band and short RSI is
    oversold, while price is above the long trend EMA.
    Exit: price closes back above the middle band plus `exit_k` standard
    deviations (or stop / time limit).
    """
    name = "dip_buy"
    bb_period, bb_k = 20, 2.0
    exit_k = 0.0
    rsi_period, rsi_max = 3, 15
    trend = 200
    stop_atr = 2.5
    max_bars = 32  # 8 hours on 15m candles

    def prepare(self, cs):
        closes = [c["c"] for c in cs]
        mid = sma(closes, self.bb_period)
        sd = stdev(closes, self.bb_period, mid)
        return {"c": closes, "low": [m - self.bb_k * s for m, s in zip(mid, sd)],
                "exit": [m + self.exit_k * s for m, s in zip(mid, sd)],
                "rsi": rsi(closes, self.rsi_period), "trend": ema(closes, self.trend),
                "atr": atr(cs, 14)}

    def entry(self, d, i):
        return (d["c"][i] < d["low"][i] and d["rsi"][i] < self.rsi_max
                and d["c"][i] > d["trend"][i])

    def exit(self, d, i):
        return d["c"][i] > d["exit"][i]


class Breakout(Strategy):
    """Buy a close above the recent high on strong volume, in an uptrend.

    Exit with a trailing stop, a target, or a time limit.
    """
    name = "breakout"
    lookback = 48     # 12 hours on 15m candles
    vol_mult = 2.0
    trend = 200
    stop_atr = 2.0
    target_atr = 6.0
    trail_atr = 3.0
    max_bars = 96     # 24 hours

    def prepare(self, cs):
        closes = [c["c"] for c in cs]
        highs = [c["h"] for c in cs]
        vols = [c["v"] for c in cs]
        prior_high = [max(highs[max(0, i - self.lookback):i]) if i else highs[0]
                      for i in range(len(cs))]
        return {"c": closes, "hh": prior_high, "vavg": sma(vols, self.lookback), "v": vols,
                "trend": ema(closes, self.trend), "atr": atr(cs, 14)}

    def entry(self, d, i):
        return (d["c"][i] > d["hh"][i] and d["v"][i] > self.vol_mult * d["vavg"][i - 1]
                and d["c"][i] > d["trend"][i])

    def exit(self, d, i):
        return False


class Rotation(Strategy):
    """Hold the strongest tokens; rotate out of the weak ones.

    Every `rebalance` candles, rank tokens by their return over the last
    `lookback` candles. Hold the top `top_k`, but only if that return is
    above `min_ret` (so in a falling market the bot sits in cash). Sell a
    token when it drops out of the top group or its momentum turns. A wide
    ATR stop guards against crashes between rebalances.
    """
    name = "rotation"
    lookback = 96     # 24 hours on 15m candles
    rebalance = 16    # every 4 hours
    top_k = 2
    min_ret = 0.0
    stop_atr = 4.0
    warmup = 200

    def prepare_all(self, by_sym):
        # Align markets on timestamps so ranks compare the same moment.
        closes = {s: {c["t"]: c["c"] for c in cs} for s, cs in by_sym.items()}
        out = {}
        for s, cs in by_sym.items():
            out[s] = {"t": [c["t"] for c in cs], "atr": atr(cs, 14), "rank": [], "ret": []}
        gran = by_sym[next(iter(by_sym))][1]["t"] - by_sym[next(iter(by_sym))][0]["t"]
        for s, cs in by_sym.items():
            for c in cs:
                t = c["t"]
                rets = {}
                for s2, m in closes.items():
                    now, then = m.get(t), m.get(t - self.lookback * gran)
                    if now and then:
                        rets[s2] = now / then - 1
                r = rets.get(s)
                rank = 1 + sum(1 for v in rets.values() if r is not None and v > r)
                out[s]["ret"].append(r)
                out[s]["rank"].append(rank if r is not None else 99)
        self._gran = gran
        return out

    def _due(self, d, i):
        return (d["t"][i] // self._gran) % self.rebalance == 0

    def entry(self, d, i):
        r = d["ret"][i]
        return (self._due(d, i) and r is not None and d["rank"][i] <= self.top_k
                and r > self.min_ret)

    def exit(self, d, i):
        r = d["ret"][i]
        return self._due(d, i) and (r is None or d["rank"][i] > self.top_k or r <= self.min_ret)


STRATEGIES = {s.name: s for s in (TrendEMA, DipBuy, Breakout, Rotation)}
