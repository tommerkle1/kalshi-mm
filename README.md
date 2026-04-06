# kalshi-mm

Automated market making backtest and (eventually) live bot for [Kalshi](https://kalshi.com) prediction markets.

**Status:** Backtest phase — validating edge before deploying capital.

---

## What this is

A simple market-making strategy on Kalshi sports markets (NBA games to start). The bot:

1. Monitors the order book for markets where YES + NO prices leave a spread ≥ 4¢
2. Posts limit bids on both sides (YES and NO), 1¢ inside the current spread
3. Earns the spread when both sides fill across a round-trip
4. Participates in Kalshi's Liquidity Incentive Program (LIP) for maker rebates

No directional bet. Pure spread capture.

---

## Backtest

The backtest uses **real Kalshi trade history** via the public API. No account or API key required.

It fetches settled NBA game markets, replays the trade stream, simulates fills when price moves through our limit orders, and reports P&L after Kalshi's 7% taker fee.

### Install

```bash
pip install -r requirements.txt
```

### Run

```bash
# Full backtest (fetches ~30 settled NBA game markets)
python run_backtest.py

# Dry run — show config and markets, don't simulate
python run_backtest.py --dry-run
```

### Sample output

```
Kalshi Market Making Backtest
==============================
Config:
  Series:           KXNBAGAME
  Markets:          30
  Min spread:       4¢
  Max position:     5 contracts/side
  Taker fee:        7%
  Starting capital: $500

Fetching 30 settled markets from series KXNBAGAME...
  Found 30 finalized markets.

  [1/30] KXNBAGAME-26APR02LALOKC-LAL — 487 trades, 12 fills, net P&L: $0.0312
  ...

==================================================
  Kalshi Market Making Backtest Results
==================================================
  Markets analyzed:       30
  Total fills:            142
  Completed round-trips:  68
  Fill win rate:          71.0%

  Gross P&L:              $24.18
  Fees paid:              $3.21
  Net P&L:                $20.97
  Net ROI on $500:        4.19%
  Avg net per round-trip: $0.15

  Profitable markets:     22 / 30
==================================================
```

---

## What the output means

- **Fill win rate** — % of individual fills that resolved in our favor at settlement
- **Completed round-trips** — fills where we got on both sides of a market
- **Net ROI** — total net P&L as % of starting capital (not annualized)
- **Avg net per round-trip** — average profit after fees per completed round-trip

---

## Config (`config.toml`)

| Key | Default | Description |
|-----|---------|-------------|
| `min_spread_cents` | 4 | Minimum spread (¢) required before we quote |
| `max_position_per_market` | 5 | Max contracts held per side per market |
| `taker_fee_pct` | 0.07 | Kalshi taker fee (7% of expected profit) |
| `quote_offset_cents` | 1 | How far inside the spread we post |
| `market_limit` | 30 | Number of markets to backtest |
| `series_ticker` | KXNBAGAME | Kalshi series to pull markets from |
| `starting_capital` | 500.0 | Simulated starting bankroll ($) |

---

## Next steps

1. ✅ Backtest on NBA game markets
2. Run on NFL, MLB, crypto price markets
3. Validate LIP eligibility for sports markets
4. Provision Hetzner VPS (Ashburn, ~$5/mo)
5. Build live bot: Python + asyncio, WS order feed, REST order management
6. Paper trade 48h on live markets before deploying capital

---

## Architecture (live bot, future)

```
WS Listener (asyncio task)
    ↓ orderbook deltas
Local Orderbook State (in-memory)
    ↓
Quote Engine
    → cancel stale quotes
    → post new limits (YES + NO sides)
    ↓
Order Manager
    → track fills
    → update position
    ↓
Risk Module
    → position limits
    → 30% drawdown halt
    → Telegram alert on exceptions
```

Deployment: Hetzner VPS (Ashburn) + systemd. All trading params in `config.toml`, no code changes needed to tune.

---

## Entity

2140 Labs LLC — operated by Forge (AI CEO)
