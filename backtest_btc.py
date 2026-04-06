"""
BTC hourly market making backtest.

Strategy: For each BTC hourly "will BTC be above $X at time T" market,
we pick the market closest to 50/50 (most uncertainty) and simulate
market making around it.

Key insight: BTC hourly markets have YES price that swings dramatically
(e.g. 0.23 → 0.99 in one hour). A market maker posting 1¢ inside the
midpoint can get filled on both sides if price oscillates.
"""

import requests
import time
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

TAKER_FEE = config["strategy"]["taker_fee_pct"]
OFFSET = config["strategy"]["quote_offset_cents"] / 100.0
MAX_POS = config["strategy"]["max_position_per_market"]
HOURS_TO_TEST = 30  # Number of hourly windows to backtest

def get_all_trades(ticker: str, max_pages: int = 30) -> list:
    trades = []
    cursor = None
    for _ in range(max_pages):
        params = {"ticker": ticker, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = requests.get(
                "https://api.elections.kalshi.com/trade-api/v2/markets/trades",
                params=params, timeout=10
            )
            data = resp.json()
        except Exception as e:
            break
        batch = data.get("trades", [])
        if not batch:
            break
        trades.extend(batch)
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.04)
    trades.sort(key=lambda t: t["created_time"])
    return trades


def simulate_mm(trades: list, yes_won: bool) -> dict:
    """Market maker simulation on a single market's trades."""
    yes_pos = 0  # contracts held
    no_pos = 0
    fills = []
    yes_quote = None
    no_quote = None
    last_yes = None

    for trade in trades:
        yes_price = float(trade["yes_price_dollars"])

        # Only quote when there's real uncertainty
        if not (0.05 <= yes_price <= 0.95):
            yes_quote = None
            no_quote = None
            last_yes = yes_price
            continue

        no_price = 1.0 - yes_price

        # Check if our resting quotes were hit
        if yes_quote is not None and yes_price <= yes_quote and yes_pos < MAX_POS:
            fills.append(("yes", yes_quote))
            yes_pos += 1
            yes_quote = None

        if no_quote is not None and no_price <= no_quote and no_pos < MAX_POS:
            fills.append(("no", no_quote))
            no_pos += 1
            no_quote = None

        # Post new quotes
        if yes_quote is None and yes_pos < MAX_POS:
            yes_quote = round(yes_price - OFFSET, 4)
        if no_quote is None and no_pos < MAX_POS:
            no_quote = round(no_price - OFFSET, 4)

        last_yes = yes_price

    # Settle
    gross = 0.0
    fees = 0.0
    round_trips = 0
    for side, price in fills:
        if side == "yes":
            if yes_won:
                profit = 1.0 - price
                fee = profit * TAKER_FEE
                gross += profit - fee
                fees += fee
                round_trips += 1
            else:
                gross -= price
        else:  # no
            if not yes_won:
                profit = 1.0 - price
                fee = profit * TAKER_FEE
                gross += profit - fee
                fees += fee
                round_trips += 1
            else:
                gross -= price

    return {
        "fills": len(fills),
        "net_pnl": gross,
        "fees": fees,
        "round_trips": round_trips,
        "yes_pos": yes_pos,
        "no_pos": no_pos,
    }


def main():
    print("\nKalshi BTC Hourly Market Making Backtest")
    print("=" * 50)
    print(f"Taker fee: {TAKER_FEE*100:.0f}%  |  Quote offset: {OFFSET*100:.1f}¢  |  Max position: {MAX_POS}")

    # Fetch settled KXBTCD markets
    print("\nFetching settled BTC hourly markets...")
    all_markets = []
    cursor = None
    pages = 0
    while pages < 15:
        params = {"series_ticker": "KXBTCD", "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get("https://api.elections.kalshi.com/trade-api/v2/markets",
                            params=params, timeout=10)
        data = resp.json()
        batch = data.get("markets", [])
        if not batch:
            break
        all_markets.extend(batch)
        cursor = data.get("cursor")
        pages += 1
        if not cursor:
            break
        time.sleep(0.05)

    # Group by hour
    hours = defaultdict(list)
    for m in all_markets:
        parts = m["ticker"].split("-")
        if len(parts) >= 2:
            hours[parts[1]].append(m)

    # For each hour, pick the market closest to 50/50 by result split
    # i.e. find where YES and NO results are mixed — meaning BTC was near the strike
    print(f"Total settled markets: {len(all_markets)}")
    print(f"Unique hours: {len(hours)}")

    # Pick hours with high total volume
    high_vol_hours = []
    for hour, markets in hours.items():
        total_vol = sum(float(m.get("volume_fp", "0")) for m in markets)
        # Find the market closest to 50/50 (highest uncertainty)
        candidates = [m for m in markets if float(m.get("volume_fp", "0")) > 1000]
        if candidates and total_vol > 50000:
            high_vol_hours.append((hour, total_vol, candidates))

    high_vol_hours.sort(key=lambda x: -x[1])
    selected_hours = high_vol_hours[:HOURS_TO_TEST]
    print(f"Testing {len(selected_hours)} highest-volume hours\n")

    results = []
    for i, (hour, total_vol, candidates) in enumerate(selected_hours):
        # Pick the market with YES result closest to NO result counts (most competitive hour)
        # Or just pick the highest volume market in this hour
        best_market = max(candidates, key=lambda m: float(m.get("volume_fp", "0")))
        ticker = best_market["ticker"]
        result = best_market.get("result", "").lower()
        yes_won = result == "yes"

        print(f"  [{i+1}/{len(selected_hours)}] {hour} | {ticker} | result={'YES' if yes_won else 'NO'} | vol={float(best_market.get('volume_fp','0')):,.0f}", end="", flush=True)

        trades = get_all_trades(ticker, max_pages=25)
        uncertain = [t for t in trades if 0.05 <= float(t["yes_price_dollars"]) <= 0.95]

        if len(uncertain) < 5:
            print(f" | SKIP ({len(uncertain)} uncertain trades)")
            continue

        prices = [float(t["yes_price_dollars"]) for t in uncertain]
        result_data = simulate_mm(uncertain, yes_won)

        print(f" | trades={len(uncertain)} | YES_range=[{min(prices):.2f},{max(prices):.2f}] | fills={result_data['fills']} | net=${result_data['net_pnl']:.4f}")
        results.append({
            "hour": hour,
            "ticker": ticker,
            "yes_won": yes_won,
            "vol": float(best_market.get("volume_fp", "0")),
            "uncertain_trades": len(uncertain),
            "price_min": min(prices),
            "price_max": max(prices),
            "price_swing": max(prices) - min(prices),
            **result_data,
        })

    # Summary
    print("\n" + "=" * 60)
    print("  BTC Hourly Market Making Results")
    print("=" * 60)
    if not results:
        print("No results.")
        return

    total_pnl = sum(r["net_pnl"] for r in results)
    total_fills = sum(r["fills"] for r in results)
    total_rt = sum(r["round_trips"] for r in results)
    winners = sum(1 for r in results if r["net_pnl"] > 0)
    avg_swing = sum(r["price_swing"] for r in results) / len(results)

    print(f"  Hours tested:       {len(results)}")
    print(f"  Total fills:        {total_fills}")
    print(f"  Round-trips (wins): {total_rt}")
    print(f"  Profitable hours:   {winners}/{len(results)}")
    print(f"  Avg YES price swing: {avg_swing:.2f} ({avg_swing*100:.0f}¢)")
    print(f"  Net P&L:            ${total_pnl:.4f}")
    print(f"  Net ROI on $500:    {total_pnl/500*100:.2f}%")
    print()
    print("  Best hour: ", max(results, key=lambda r: r["net_pnl"])["hour"], f"${max(r['net_pnl'] for r in results):.4f}")
    print("  Worst hour:", min(results, key=lambda r: r["net_pnl"])["hour"], f"${min(r['net_pnl'] for r in results):.4f}")
    print()

    # Show distribution
    buckets = {"<-0.50": 0, "-0.50 to -0.10": 0, "-0.10 to 0": 0, "0 to +0.10": 0, "+0.10 to +0.50": 0, ">+0.50": 0}
    for r in results:
        p = r["net_pnl"]
        if p < -0.50: buckets["<-0.50"] += 1
        elif p < -0.10: buckets["-0.50 to -0.10"] += 1
        elif p < 0: buckets["-0.10 to 0"] += 1
        elif p < 0.10: buckets["0 to +0.10"] += 1
        elif p < 0.50: buckets["+0.10 to +0.50"] += 1
        else: buckets[">+0.50"] += 1
    print("  P&L distribution:")
    for bucket, count in buckets.items():
        print(f"    {bucket:20s}: {count}")
    print("=" * 60)


if __name__ == "__main__":
    main()
