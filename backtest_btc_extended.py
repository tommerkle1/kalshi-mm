"""
Extended BTC hourly market making backtest.

Covers ~67 days of KXBTCD history (Jan 28 – Apr 6, 2026).
Tests position sizes: 5, 10, 20 contracts.
Samples 100 hours spread across the full date range.
"""

import requests
import time
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
TAKER_FEE    = 0.07     # 7% of expected profit
OFFSET       = 0.01     # post 1¢ inside mid
POSITION_SIZES = [5, 10, 20]
HOURS_TO_TEST  = 100
MIN_VOL        = 30_000  # skip thin markets


# ── API helpers ───────────────────────────────────────────────────────────────

def get_trades(ticker: str, max_pages: int = 40) -> list:
    trades, cursor = [], None
    for _ in range(max_pages):
        params = {"ticker": ticker, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(
                "https://api.elections.kalshi.com/trade-api/v2/markets/trades",
                params=params, timeout=10
            )
            data = r.json()
        except Exception:
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


def fetch_market_index(pages: int = 80) -> list:
    """Pull settled KXBTCD market list across many pages."""
    all_markets, cursor = [], None
    for p in range(pages):
        params = {"series_ticker": "KXBTCD", "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(
            "https://api.elections.kalshi.com/trade-api/v2/markets",
            params=params, timeout=12
        )
        data = r.json()
        batch = data.get("markets", [])
        if not batch:
            break
        all_markets.extend(batch)
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.06)
        if p > 0 and p % 20 == 0:
            print(f"  ...{len(all_markets)} markets fetched")
    return all_markets


# ── Simulation ────────────────────────────────────────────────────────────────

def simulate_mm(trades: list, yes_won: bool, max_pos: int) -> dict:
    yes_pos = no_pos = 0
    fills = []
    yes_q = no_q = None

    for trade in trades:
        yes_price = float(trade["yes_price_dollars"])
        if not (0.05 <= yes_price <= 0.95):
            yes_q = no_q = None
            continue

        no_price = 1.0 - yes_price

        # Check fills
        if yes_q is not None and yes_price <= yes_q and yes_pos < max_pos:
            fills.append(("yes", yes_q))
            yes_pos += 1
            yes_q = None

        if no_q is not None and no_price <= no_q and no_pos < max_pos:
            fills.append(("no", no_q))
            no_pos += 1
            no_q = None

        # Post new quotes
        if yes_q is None and yes_pos < max_pos:
            yes_q = round(yes_price - OFFSET, 4)
        if no_q is None and no_pos < max_pos:
            no_q = round(no_price - OFFSET, 4)

    # Settle P&L
    gross = fees = round_trips = 0.0
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
        else:
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
        "round_trips": int(round_trips),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\nExtended BTC Hourly Market Making Backtest")
    print("=" * 60)
    print(f"Offset: {OFFSET*100:.0f}¢  |  Taker fee: {TAKER_FEE*100:.0f}%  |  Min vol: {MIN_VOL:,}")
    print(f"Position sizes tested: {POSITION_SIZES}  |  Target hours: {HOURS_TO_TEST}")

    # Build market index
    print("\nBuilding market index...")
    all_markets = fetch_market_index(pages=80)
    print(f"Total settled KXBTCD markets: {len(all_markets)}")

    # Group by hour, pick highest-volume market per hour
    hours = defaultdict(list)
    for m in all_markets:
        parts = m["ticker"].split("-")
        if len(parts) >= 2:
            hours[parts[1]].append(m)

    # Filter hours by volume and sort by date
    candidates = []
    for hour, markets in hours.items():
        best = max(markets, key=lambda m: float(m.get("volume_fp", "0")))
        vol = float(best.get("volume_fp", "0"))
        if vol >= MIN_VOL:
            candidates.append((hour, best))

    candidates.sort(key=lambda x: x[0])  # sort by date string
    print(f"Hours with vol ≥ {MIN_VOL:,}: {len(candidates)}")

    # Sample evenly across the date range
    step = max(1, len(candidates) // HOURS_TO_TEST)
    selected = candidates[::step][:HOURS_TO_TEST]
    print(f"Sampled {len(selected)} hours from {selected[0][0]} to {selected[-1][0]}")
    print()

    # Run backtest
    results = []
    for i, (hour, market) in enumerate(selected):
        ticker   = market["ticker"]
        yes_won  = market.get("result", "").lower() == "yes"
        vol      = float(market.get("volume_fp", "0"))

        trades   = get_trades(ticker, max_pages=30)
        uncertain = [t for t in trades if 0.05 <= float(t["yes_price_dollars"]) <= 0.95]

        if len(uncertain) < 5:
            print(f"  [{i+1:3d}] {hour}  SKIP ({len(uncertain)} uncertain trades)")
            continue

        prices = [float(t["yes_price_dollars"]) for t in uncertain]
        swing  = max(prices) - min(prices)

        row = {
            "hour": hour,
            "ticker": ticker,
            "yes_won": yes_won,
            "vol": vol,
            "uncertain_trades": len(uncertain),
            "swing": swing,
        }
        for max_pos in POSITION_SIZES:
            r = simulate_mm(uncertain, yes_won, max_pos)
            row[f"pnl_{max_pos}"]  = r["net_pnl"]
            row[f"fills_{max_pos}"] = r["fills"]
            row[f"rt_{max_pos}"]   = r["round_trips"]

        # Display row
        pnl_5  = row["pnl_5"]
        pnl_10 = row["pnl_10"]
        pnl_20 = row["pnl_20"]
        print(f"  [{i+1:3d}] {hour}  swing={swing:.2f}  "
              f"t={len(uncertain):4d}  "
              f"p5=${pnl_5:+.3f}  p10=${pnl_10:+.3f}  p20=${pnl_20:+.3f}")
        results.append(row)

    # ── Summary ──────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print(f"{'SUMMARY':^70}")
    print("=" * 70)

    if not results:
        print("No results.")
        return

    n = len(results)
    avg_swing = sum(r["swing"] for r in results) / n

    print(f"\nHours tested:          {n}")
    print(f"Date range:            {results[0]['hour']} → {results[-1]['hour']}")
    print(f"Avg YES price swing:   {avg_swing:.2f} ({avg_swing*100:.0f}¢)")
    print()

    print(f"{'Max Pos':>10}  {'Net P&L':>10}  {'ROI/500':>9}  {'Win%':>6}  {'Fills':>6}  {'RTrips':>7}  {'P&L/RT':>8}")
    print("-" * 70)
    for max_pos in POSITION_SIZES:
        total_pnl = sum(r[f"pnl_{max_pos}"] for r in results)
        wins      = sum(1 for r in results if r[f"pnl_{max_pos}"] > 0)
        fills     = sum(r[f"fills_{max_pos}"] for r in results)
        rts       = sum(r[f"rt_{max_pos}"] for r in results)
        pnl_per_rt = total_pnl / rts if rts > 0 else 0
        print(f"{max_pos:>10}  ${total_pnl:>9.2f}  {total_pnl/500*100:>8.2f}%  "
              f"{wins/n*100:>5.0f}%  {fills:>6d}  {rts:>7d}  ${pnl_per_rt:>7.4f}")

    # P&L distribution for each size
    for max_pos in POSITION_SIZES:
        buckets = {
            "< -$1.00": 0, "-$1 to -$0.10": 0, "-$0.10 to $0": 0,
            "$0 to $0.10": 0, "$0.10 to $1.00": 0, "> $1.00": 0,
        }
        for r in results:
            p = r[f"pnl_{max_pos}"]
            if p < -1.00:      buckets["< -$1.00"] += 1
            elif p < -0.10:    buckets["-$1 to -$0.10"] += 1
            elif p < 0:        buckets["-$0.10 to $0"] += 1
            elif p < 0.10:     buckets["$0 to $0.10"] += 1
            elif p < 1.00:     buckets["$0.10 to $1.00"] += 1
            else:              buckets["> $1.00"] += 1

        print(f"\n  Distribution (max_pos={max_pos}):")
        for bucket, count in buckets.items():
            bar = "█" * count
            print(f"    {bucket:18s} {count:3d}  {bar}")

    # Worst/best hours (pos=20)
    print()
    best  = max(results, key=lambda r: r["pnl_20"])
    worst = min(results, key=lambda r: r["pnl_20"])
    print(f"  Best  hour (pos=20):  {best['hour']}  ${best['pnl_20']:+.3f}  swing={best['swing']:.2f}")
    print(f"  Worst hour (pos=20):  {worst['hour']}  ${worst['pnl_20']:+.3f}  swing={worst['swing']:.2f}")

    # Hours with pos=20 loss > $5
    big_losses = [r for r in results if r["pnl_20"] < -5.0]
    if big_losses:
        print(f"\n  ⚠️  Hours with >$5 loss at pos=20: {len(big_losses)}")
        for r in sorted(big_losses, key=lambda r: r["pnl_20"])[:5]:
            print(f"    {r['hour']}  ${r['pnl_20']:+.2f}  swing={r['swing']:.2f}  trades={r['uncertain_trades']}")

    print("=" * 70)


if __name__ == "__main__":
    main()
