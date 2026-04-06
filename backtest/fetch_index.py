"""
fetch_index.py — Build and cache the KXBTCD settled market index.

Run this once (or to refresh). Saves to data/kxbtcd_index.json.
Subsequent backtest runs load from cache — no API dependency.
"""

import requests
import time
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_FILE = DATA_DIR / "kxbtcd_index.json"


def fetch_index(max_pages: int = 120, delay: float = 0.05) -> list:
    """Pull all settled KXBTCD markets from Kalshi API."""
    all_markets, cursor, page = [], None, 0
    while page < max_pages:
        params = {"series_ticker": "KXBTCD", "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            r = requests.get(
                "https://api.elections.kalshi.com/trade-api/v2/markets",
                params=params, timeout=15
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ⚠️  Page {page} error: {e} — retrying in 2s")
            time.sleep(2)
            continue

        batch = data.get("markets", [])
        if not batch:
            break
        all_markets.extend(batch)
        cursor = data.get("cursor")
        page += 1

        if page % 10 == 0:
            print(f"  page {page:3d}: {len(all_markets):,} markets fetched")

        if not cursor:
            break
        time.sleep(delay)

    return all_markets


def build_cache(force: bool = False) -> list:
    DATA_DIR.mkdir(exist_ok=True)

    if INDEX_FILE.exists() and not force:
        with open(INDEX_FILE) as f:
            data = json.load(f)
        markets = data["markets"]
        print(f"✓ Loaded {len(markets):,} markets from cache ({INDEX_FILE})")
        print(f"  Cached: {data['fetched_at']}  |  Pages: {data['pages_fetched']}")
        return markets

    print("Fetching KXBTCD market index from Kalshi API...")
    print("(this takes ~3 min — run once, then uses cache)")
    markets = fetch_index()

    from datetime import datetime, timezone
    cache = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "series": "KXBTCD",
        "count": len(markets),
        "pages_fetched": len(markets) // 200 + 1,
        "markets": markets,
    }
    with open(INDEX_FILE, "w") as f:
        json.dump(cache, f)

    print(f"\n✓ Cached {len(markets):,} markets to {INDEX_FILE}")
    return markets


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv or "-f" in sys.argv
    markets = build_cache(force=force)
    print(f"\nMarket count: {len(markets):,}")
    if markets:
        tickers = sorted(m["ticker"] for m in markets)
        print(f"Earliest: {tickers[0]}")
        print(f"Latest:   {tickers[-1]}")
