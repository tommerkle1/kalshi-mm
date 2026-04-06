"""
fetch.py — Pull market and trade data from Kalshi's public API.
No API key required. Uses the public trades endpoint.
"""

import time
import requests
from typing import Optional

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json"})


def get_settled_markets(series_ticker: str, limit: int = 50) -> list[dict]:
    """
    Fetch settled (finalized) markets from a given series.
    Returns markets sorted by volume descending.
    """
    markets = []
    cursor = None
    fetched = 0

    while fetched < limit * 3:  # over-fetch to filter by finalized
        params = {"limit": 200, "series_ticker": series_ticker}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = SESSION.get(f"{BASE_URL}/markets", params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [fetch] Error fetching markets: {e}")
            break

        batch = data.get("markets", [])
        if not batch:
            break

        finalized = [m for m in batch if m.get("status") == "finalized"]
        markets.extend(finalized)
        fetched += len(batch)

        cursor = data.get("cursor")
        if not cursor or len(markets) >= limit * 2:
            break

        time.sleep(0.1)

    # Sort by volume, return top `limit`
    markets.sort(key=lambda m: float(m.get("volume_fp", "0")), reverse=True)
    return markets[:limit]


def get_all_market_trades(ticker: str, max_pages: int = 100) -> list[dict]:
    """
    Fetch ALL trades for a market by paginating to the oldest.
    Kalshi returns newest-first; we reverse to get oldest-first.
    WARNING: high-volume markets can have 5000+ trades — use sparingly.
    """
    trades = []
    cursor = None

    for _ in range(max_pages):
        params = {"ticker": ticker, "limit": 100}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = SESSION.get(f"{BASE_URL}/markets/trades", params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [fetch] Error fetching trades for {ticker}: {e}")
            break

        batch = data.get("trades", [])
        if not batch:
            break

        trades.extend(batch)
        cursor = data.get("cursor")
        if not cursor:
            break

        time.sleep(0.05)

    # Sort oldest first
    trades.sort(key=lambda t: t["created_time"])
    return trades


def get_market_snapshot(ticker: str) -> Optional[dict]:
    """
    Get current market data including last_price, yes/no bid/ask.
    Used to understand market state — not for live orderbook backtest.
    """
    try:
        resp = SESSION.get(f"{BASE_URL}/markets/{ticker}", timeout=10)
        resp.raise_for_status()
        return resp.json().get("market")
    except Exception:
        return None
