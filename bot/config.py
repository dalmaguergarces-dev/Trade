"""All tunable settings in one place.

Each entry in ACCOUNTS is a separate paper account with its own markets,
strategy and state file (docs/data/<account>.json).
"""

STARTING_CASH = 10_000.0
MAX_EQUITY_POINTS = 20000  # ~7 months of 15m points per live account

ACCOUNTS = {
    # Solana tokens priced from their busiest DEX pool (GeckoTerminal).
    # `cost` is the one-way trading cost: DEX fee + price impact for a ~$2,000
    # Jupiter swap (measured Nov 2026), with a 0.05% floor for network fees.
    "solana_15m": {
        "title": "Solana dip-buyer (15m)",
        "source": "geckoterminal",
        "granularity": 900,
        "history": 1100,  # candles fetched each run for indicator warm-up
        "markets": {
            "SOL":   {"pool": "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE", "cost": 0.0005,
                      "mint": "So11111111111111111111111111111111111111112"},
            "JUP":   {"pool": "C1MgLojNLWBKADvu9BHdtgzz1oZX4dZ5zGdGcgvvW8Wz", "cost": 0.0006,
                      "mint": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"},
            "BONK":  {"pool": "5zpyutJu9ee6jFymDGoK7F6S5Kczqtc9FomP3ueKuyA9", "cost": 0.0012,
                      "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"},
            "RAY":   {"pool": "2AXXcN6oN9bBT5owwmTH53C7QHUXvhLeu718Kqt8rvY2", "cost": 0.0009,
                      "mint": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"},
            "PENGU": {"pool": "FAqh648xeeaTqL7du49sztp9nfj5PjRQrfvaMccyd9cz", "cost": 0.0005,
                      "mint": "2zMMhcVQEXDtdE6vsFS7S7D5oUodfJHE8vd1gnBouauv"},
        },
        # WIF was dropped: its ~0.3% one-way cost ate the whole edge.
        # About 1 trade/day. Roughly breakeven after costs in testing (see
        # README): the edge before costs is only ~0.1-0.25% per trade.
        "strategy": "dip_buy",
        "params": {"bb_k": 2.5, "rsi_max": 10, "max_bars": 16, "stop_atr": 3.0, "trend": 800},
        "risk_per_trade": 0.01,
        "max_position_fraction": 0.25,
        "backtest_days": 178,  # the free data API only serves 180 days
    },
    # The original bot: BTC/ETH/SOL on Coinbase prices, 6-hour candles.
    "swing_6h": {
        "title": "Swing trend-follower (6h)",
        "source": "coinbase",
        "granularity": 21600,
        "history": 600,
        "markets": {
            "BTC-USD": {"cost": 0.0015},  # 0.10% fee + 0.05% slippage
            "ETH-USD": {"cost": 0.0015},
            "SOL-USD": {"cost": 0.0015},
        },
        "strategy": "trend_ema",
        "params": {"fast": 10, "slow": 100, "trend": 200, "stop_atr": 2.0, "target_atr": 4.0},
        "risk_per_trade": 0.01,
        "max_position_fraction": 1 / 3,
        "backtest_days": 1460,
    },
}


def fetch(account, market, start, end):
    """Candles for one market of an account, from that account's data source."""
    acct = ACCOUNTS[account]
    if acct["source"] == "coinbase":
        from .coinbase import fetch_candles
        return fetch_candles(market, acct["granularity"], start, end)
    from .data import fetch_candles
    return fetch_candles(acct["markets"][market]["pool"], acct["granularity"], start, end)


def make_strategy(account):
    from .strategies import STRATEGIES
    acct = ACCOUNTS[account]
    return STRATEGIES[acct["strategy"]](**acct["params"])
