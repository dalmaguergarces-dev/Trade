# Trading Bots

Two crypto trading bots that trade paper money, an optional live mode that
trades a small real Solana wallet, and a dashboard for all of them:
https://trade-paper-bot.vercel.app

| Account | Markets | Candles | Strategy | Trades |
|---|---|---|---|---|
| `solana_15m` | SOL, JUP, BONK, RAY, PENGU on Solana DEXs | 15 min | Dip-buyer | ~1 / day |
| `swing_6h` | BTC, ETH, SOL (Coinbase prices) | 6 hours | Trend-follower | ~2 / month |

The **Bot runner** workflow (`.github/workflows/runner.yml`) runs the bots every
15 minutes, just after each candle closes. Each job runs for about 5.5 hours and
then starts the next one; an hourly schedule restarts the chain if it ever
breaks. (GitHub's own 15-minute schedules proved unreliable, sometimes skipping
hours.) Each account saves its state to `docs/data/<account>.json`.

To stop all bots: GitHub → Actions → Bot runner → ⋯ → **Disable workflow**.
The dashboard reads those files straight from GitHub, so it stays current
without redeploying. Settings for both accounts live in `bot/config.py`. The
code uses only the Python standard library.

## How the bots trade

- **Signals:** decided at a candle's close; orders fill at the next candle's open.
- **Costs:** every fill pays a realistic cost as a worse price. For Solana tokens
  that's the DEX fee plus price impact of a ~$2,000 Jupiter swap, measured from
  live quotes, with a 0.05% floor for network fees. For the swing account it's
  0.10% fee plus 0.05% slippage.
- **Stops:** a stop-loss is checked against each candle's low. If a candle
  touches both the stop and the target, the stop is assumed to come first.
- **Risk:** each trade risks 1% of the account if the stop is hit, with at most
  25% (Solana) or 33% (swing) of the account in any one token.

**Dip-buyer (`solana_15m`):** buys when price closes below the lower Bollinger
band (20, 2.5σ) and 3-period RSI is under 10, while price is above its 800-candle
(~8-day) EMA. It sells when price closes back above the 20-candle average,
after 4 hours, or at a stop 3 × ATR below entry.

**Trend-follower (`swing_6h`):** buys when the 10-EMA crosses above the 100-EMA
with price above the 200-EMA. It sells at a stop (2 × ATR), a target (4 × ATR),
or when the 10-EMA crosses back below the 100-EMA.

## What the research found

The goal was several profitable trades a day. **I could not find a strategy
that does that reliably after real trading costs.** The details:

- **Data:** 6 months of Solana DEX candles (GeckoTerminal's free limit), plus
  up to 3 years of 15-minute Coinbase candles for SOL, BONK, WIF and PENGU as a
  longer stand-in. Coinbase data was used for research only.
- **Strategies tested:** trend-following, dip-buying, breakouts and momentum
  rotation — 72+ settings in total, all at 1–4 trades a day.
- **Testing method:** walk-forward. Settings are re-picked using only past data,
  then traded on the next 90 days they haven't seen, repeated across 3 years.
- **Result with real costs:** every strategy family lost money in walk-forward
  testing (dip-buyer −16% to −21%, rotation −49%, trend −78% over 3 years).
- **Result with zero costs:** the dip-buyer was profitable in 31 of 32
  settings (+49% walk-forward). So there is a small real edge, about 0.1–0.25%
  per trade, but a DEX round trip costs roughly the same.
- **Per token:** PENGU and SOL are cheap enough to trade; WIF (0.58% round
  trip) loses badly and was dropped.
- **The live setting:** roughly breakeven over 3 years of Coinbase data
  (+0.3%, ~0.6 trades/day on 3 tokens). On 6 months of Solana data it made
  +6.7% (~1 trade/day), but that period was also used to choose it, so treat
  that number as optimistic.

The swing account held up better: +24% over 4 years with a −10% worst drawdown,
though buy-and-hold made +390% in the same period.

## Backtests

```
python -m bot.backtest solana_15m      # 178 days (free API limit)
python -m bot.backtest swing_6h        # 4 years
```

Each writes `docs/data/<account>_backtest.json`, which the dashboard shows,
including the final third of the period on its own and the effect of
doubling or removing trading costs. The **Backtest** workflow in GitHub
Actions runs the same thing on demand.

## Dashboard

`docs/` is a static site, deployed on Vercel with Root Directory `docs`. It
loads its data from this repo's `main` branch, so the repo must stay public
for the live numbers to show (otherwise it falls back to the copy deployed
with the site).

## Live wallet (real money)

`bot/live.py` follows the `solana_15m` strategy with real Jupiter swaps from
the bot's **own** Solana wallet. Your main wallet is never connected.

**Hard limits** (`LIVE` in `bot/config.py`):

| Limit | Default |
|---|---|
| Max spend per buy | $25 (and at most 25% of the wallet) |
| Open positions at once | 3 |
| Daily loss before new buys pause | $10 (resumes next UTC day) |
| Total loss before new buys stop | 30% from when the wallet was funded |
| Max slippage / price impact | 0.5% / 0.5% |
| SOL kept aside for fees | 0.02 SOL |

Sells are always allowed, so a paused bot still closes its positions.

**Setup:**

1. In Phantom, create a **new account** just for the bot.
2. Send it $50–100 of USDC and about 0.05 SOL for network fees.
3. In Phantom: Settings → Manage Accounts → the bot account → Show Private Key.
4. In GitHub: Settings → Secrets and variables → Actions → **New repository
   secret** `SOLANA_BOT_KEY`, and paste the key. Never paste it anywhere else.
5. On the **Variables** tab, add `LIVE_TRADING` = `dry`. The bot then builds,
   signs and simulates every swap without sending it, so you can check the
   Activity list on the dashboard.
6. Change `LIVE_TRADING` to `on` to trade for real, or `off` to stop.

Optional: a faster RPC (for example a free Helius endpoint) as the secret
`SOLANA_RPC_URL`. The default public RPC can be slow to land transactions.

**Things to know:**

- The bot's wallet address and trades are public: the repo is public and so
  is the Solana blockchain.
- Withdrawing or depositing changes the wallet value the loss limits measure
  from. After a large deposit, delete `start_value` in
  `docs/data/solana_live.json` to reset the baseline.
- At $25 a trade, network and priority fees (~$0.01–0.02 per swap) add
  roughly 0.1% per round trip on top of the costs used in testing.
- Stops are checked every 15 minutes, not continuously, so a fast crash can
  sell well below the stop price.

## A word on real money

The research above found no reliable edge at this trade frequency after costs.
Keep the live wallet small, and judge it over months, not days.
