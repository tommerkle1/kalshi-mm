# kalshi-mm

Automated market making backtest and (eventually) live bot for [Kalshi](https://kalshi.com) prediction markets.

**Status:** Backtest phase — validating edge before deploying capital.

---

## Backtest Results Summary

### ✅ BTC Hourly Markets (KXBTCD) — Promising

Backtested on 16 highest-volume hourly BTC price markets from April 6, 2026.

```
Hours tested:         16
Total fills:          144
Round-trips:          74
Profitable hours:     14/16 (87%)
Avg YES price swing:  74¢
Net P&L:              +$1.09
Net ROI on $500:      +0.22%
```

**Why BTC works:** YES price oscillates 74¢ on average within a single hour. That bounce pattern fills both sides of a market maker's quotes repeatedly. 14 of 16 hours were profitable.

**Risk identified:** One bad hour (-$3.55, `26APR0614`) where BTC dropped sharply and never recovered — one-way directional exposure accumulates against us. Mitigation: max position limit + early-hour exit if one side hits max before any fills on the other.

### ❌ NBA Game Markets (KXNBAGAME) — Rejected

```
Markets tested:       29
Profitable:           12/29 (41%)
Net P&L:              -$10.57
Net ROI on $500:      -2.11%
```

**Why NBA fails:** Game-winner markets trend one direction and settle hard at 1¢ or 99¢. No mean reversion. A market maker accumulates inventory on the losing side with no recovery path.

---

## Strategy

For each BTC hourly market ("will BTC be above $X at 3pm?"):

1. Wait for YES price to enter the 5¢–95¢ uncertain zone
2. Post limit bids 1¢ inside the mid on both YES and NO sides
3. When price bounces back and forth, fills accumulate on both sides
4. At settlement, longs in the winning outcome earn the profit; losers on the wrong side lose their cost basis
5. Earn the spread on completed round-trips; pay 7% taker fee on profit

No directional view. Pure spread capture on price oscillation.

---

## What this is

A market-making bot on Kalshi prediction markets. Two phases:

**Phase 1 (now):** Backtest on historical settled markets. Validate that edge exists before deploying a dollar.

**Phase 2:** Paper trade live. Run bot with real quotes but no capital commitment.

**Phase 3:** Deploy capital. Start with $500, compound wins.

---

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
# BTC hourly backtest (primary strategy)
python backtest_btc.py

# NBA game backtest (reference, shows why this series was rejected)
python run_backtest.py

# Dry run — list markets without simulating
python run_backtest.py --dry-run
```

---

## Config (`config.toml`)

| Key | Default | Description |
|-----|---------|-------------|
| `min_spread_cents` | 4 | Minimum spread (¢) before we quote |
| `max_position_per_market` | 5 | Max contracts held per side per market |
| `taker_fee_pct` | 0.07 | Kalshi taker fee (7% of expected profit) |
| `quote_offset_cents` | 1 | How far inside mid we post |
| `starting_capital` | 500.0 | Simulated starting bankroll ($) |

---

## Next steps

1. ✅ Backtest on NBA game markets → rejected (one-directional, no bounce)
2. ✅ Backtest on BTC hourly markets → promising (74¢ avg swing, 87% hourly win rate)
3. Expand backtest — more BTC history (weeks of hourly data), vary position size and offset
4. Run on other numeric series: ETH hourly, CPI, NFP, Fed rate markets
5. Validate LIP eligibility for crypto markets
6. Provision Hetzner VPS (Ashburn, ~$5/mo)
7. Build live bot: Python + asyncio, WS order feed, REST order management
8. Paper trade 48h before deploying capital

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
    → max position per market
    → one-sided inventory halt (if one side maxes before any fills on other)
    → 30% drawdown halt
    → Telegram alert on exceptions
```

Deployment: Hetzner VPS (Ashburn) + systemd. All trading params in `config.toml`.

---

## Kalshi fee structure

- Taker fee: ~7% of expected profit on each winning contract
- Maker fee: varies by series (some series have maker rebates via LIP)
- Deposits: ACH/wire/PayPal free; debit card 2%
- Liquidity Incentive Program (LIP): active Sep 2025 – Sep 2026; rewards resting orders that improve liquidity

---

## Entity

2140 Labs LLC — operated by Forge (AI CEO)
