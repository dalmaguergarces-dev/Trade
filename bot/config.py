"""All tunable settings in one place."""

SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD"]
GRANULARITY = 21600  # candle size in seconds (6 hours)

STARTING_CASH = 10_000.0

# Costs. These matter a lot for short-term strategies; set them to match the
# venue you'd actually trade on. Coinbase Advanced retail taker fees are
# 0.6%/side at the lowest tier. On 15m/1h candles this strategy lost money
# even at 0.1%; on 6h candles it trades rarely enough for costs to matter less.
FEE_RATE = 0.001       # 0.10% per side
SLIPPAGE = 0.0005      # 0.05% per side, against us

# Strategy: long-only trend following.
EMA_FAST = 10
EMA_SLOW = 100
EMA_TREND = 200
ATR_PERIOD = 14
STOP_ATR = 2.0         # stop-loss distance in ATRs
TARGET_ATR = 4.0       # take-profit distance in ATRs

# Risk management.
RISK_PER_TRADE = 0.01  # lose at most 1% of equity if the stop is hit
MAX_POSITION_FRACTION = 1 / 3  # never put more than 1/3 of equity in one coin

MAX_EQUITY_POINTS = 6000  # keep the state file small
