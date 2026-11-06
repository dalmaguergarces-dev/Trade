# Paper Trading Bot

A crypto trading bot that trades **paper money only**. It trades BTC, ETH and SOL
against USD using public Coinbase price data, and you can watch it on a web dashboard.

## How it works

- **Strategy (long-only trend following, 6-hour candles):** it buys when the 10-EMA
  crosses above the 100-EMA while price is above the 200-EMA. It sells at a stop-loss
  (2 × ATR below entry) or a take-profit (4 × ATR above), or when the 10-EMA crosses
  back below the 100-EMA.
- **Risk:** each trade risks 1% of the account, with at most 1/3 of the account in any one coin.
- **Costs:** every fill pays 0.10% in fees plus 0.05% slippage.
- **Schedule:** GitHub Actions runs `python -m bot.run` every hour. The bot acts only
  when a new 6-hour candle has closed, and it saves its state to `docs/data/state.json`.
- **Backtest:** `python -m bot.backtest [days]` runs the same engine over past data
  and writes `docs/data/backtest.json`.

Everything is in `bot/config.py`. The code uses only the Python standard library,
so there's nothing to install.

## Backtest results (Nov 2022 – Nov 2026)

| | Strategy | Buy & hold |
|---|---|---|
| Return | +22.0% | +378% |
| Max drawdown | −10.2% | much larger |
| Trades | 90 (43% win rate, profit factor 1.44) | – |

At Coinbase's 0.6% retail fee the strategy loses money (−3.2%). Faster versions
(15-minute and 1-hour candles) lost money at every setting tested, which is why it
uses 6-hour candles.

## Viewing the dashboard

The dashboard is the static site in `docs/`. Pick one way to host it:

- **GitHub Pages:** Settings → Pages → Deploy from branch → `main` / `/docs`.
  Private repos need a paid GitHub plan for this.
- **Vercel:** import the repo and set the **Root Directory** to `docs`. No build
  command is needed. Vercel redeploys whenever the bot commits, which is at most
  about 4 times a day.
- **Locally:** `cd docs && python3 -m http.server`, then open http://localhost:8000.

## Real money

The bot can't place real orders; it only has a paper account. A few days of paper
results says almost nothing about a strategy that trades about twice a month.
