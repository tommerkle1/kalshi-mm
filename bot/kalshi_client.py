"""
kalshi_client.py — REST + WebSocket client for Kalshi API.

Auth: Kalshi uses RSA-based request signing (PKCS#1 v1.5, SHA-256).
Env vars required:
  KALSHI_API_KEY     — your API key ID (from kalshi.com dashboard)
  KALSHI_PRIVATE_KEY — path to RSA private key PEM file
"""

import os
import time
import base64
import hashlib
import hmac
import json
import asyncio
import aiohttp
import requests
from pathlib import Path
from datetime import datetime, timezone
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


REST_URL = "https://api.elections.kalshi.com/trade-api/v2"
WS_URL   = "wss://api.elections.kalshi.com/trade-api/ws/v2"


def _load_private_key():
    from bot.secrets import get_private_key_path
    key_path = get_private_key_path()
    with open(key_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _sign(method: str, path: str, timestamp_ms: int) -> str:
    """Generate Kalshi request signature."""
    private_key = _load_private_key()
    msg = f"{timestamp_ms}{method.upper()}{path}"
    signature = private_key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def _auth_headers(method: str, path: str) -> dict:
    from bot.secrets import get_api_key
    api_key = get_api_key()
    if not api_key:
        raise EnvironmentError("Kalshi API key not found in env or Secret Manager.")
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    sig = _sign(method, path, ts)
    return {
        "Content-Type": "application/json",
        "KALSHI-ACCESS-KEY": api_key,
        "KALSHI-ACCESS-TIMESTAMP": str(ts),
        "KALSHI-ACCESS-SIGNATURE": sig,
    }


# ── REST helpers ──────────────────────────────────────────────────────────────

class KalshiREST:
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.base = REST_URL

    def _req(self, method: str, path: str, **kwargs):
        full_path = f"/trade-api/v2{path}"
        headers = _auth_headers(method, full_path)
        url = f"{self.base}{path}"
        r = requests.request(method, url, headers=headers, timeout=10, **kwargs)
        r.raise_for_status()
        return r.json()

    def get_balance(self) -> float:
        data = self._req("GET", "/portfolio/balance")
        return float(data.get("balance", 0)) / 100  # cents → dollars

    def get_open_orders(self, ticker: str = None) -> list:
        params = {"status": "resting"}
        if ticker:
            params["ticker"] = ticker
        data = self._req("GET", "/portfolio/orders", params=params)
        return data.get("orders", [])

    def place_order(self, ticker: str, side: str, price_cents: int,
                    count: int, client_order_id: str = None) -> dict:
        """
        side: "yes" or "no"
        price_cents: 1-99 (integer cents)
        count: number of contracts
        """
        if self.dry_run:
            print(f"  [DRY RUN] ORDER: {side.upper()} {count}x {ticker} @ {price_cents}¢")
            return {"order_id": f"dry_{client_order_id or 'x'}", "dry_run": True}

        body = {
            "action": "buy",
            "type": "limit",
            "ticker": ticker,
            "side": side,
            "count": count,
            "yes_price": price_cents,  # Kalshi uses yes_price for both sides
            "client_order_id": client_order_id or f"forge_{int(time.time()*1000)}",
        }
        return self._req("POST", "/portfolio/orders", json=body)

    def cancel_order(self, order_id: str) -> dict:
        if self.dry_run:
            print(f"  [DRY RUN] CANCEL: {order_id}")
            return {"dry_run": True}
        return self._req("DELETE", f"/portfolio/orders/{order_id}")

    def get_positions(self) -> list:
        data = self._req("GET", "/portfolio/positions")
        return data.get("market_positions", [])

    def get_market(self, ticker: str) -> dict:
        data = self._req("GET", f"/markets/{ticker}")
        return data.get("market", {})

    def get_active_markets(self, series: str = "KXBTCD") -> list:
        """Get currently open/active markets for a series."""
        params = {"series_ticker": series, "status": "open", "limit": 100}
        data = self._req("GET", "/markets", params=params)
        return data.get("markets", [])

    def get_orderbook(self, ticker: str) -> dict:
        data = self._req("GET", f"/markets/{ticker}/orderbook")
        return data.get("orderbook", {})


# ── WebSocket client ──────────────────────────────────────────────────────────

class KalshiWSClient:
    """
    Maintains a WebSocket connection to Kalshi.
    Subscribes to orderbook_delta and trade channels for specified tickers.
    Calls on_message(channel, ticker, data) for each update.
    """

    def __init__(self, on_message, tickers: list[str]):
        self.on_message = on_message
        self.tickers = tickers
        self._ws = None
        self._seq = 1
        self._running = False

    def _ws_auth_headers(self) -> dict:
        api_key = os.environ.get("KALSHI_API_KEY", "")
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        sig = _sign("GET", "/trade-api/ws/v2", ts)
        return {
            "KALSHI-ACCESS-KEY": api_key,
            "KALSHI-ACCESS-TIMESTAMP": str(ts),
            "KALSHI-ACCESS-SIGNATURE": sig,
        }

    async def connect(self):
        self._running = True
        headers = self._ws_auth_headers()

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL, headers=headers) as ws:
                self._ws = ws
                await self._subscribe()
                async for msg in ws:
                    if not self._running:
                        break
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        await self._handle(data)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break

    async def _subscribe(self):
        for ticker in self.tickers:
            for channel in ("orderbook_delta", "trade"):
                sub = {
                    "id": self._seq,
                    "cmd": "subscribe",
                    "params": {"channels": [channel], "market_tickers": [ticker]},
                }
                await self._ws.send_str(json.dumps(sub))
                self._seq += 1

    async def _handle(self, data: dict):
        msg_type = data.get("type")
        if msg_type in ("orderbook_delta", "orderbook_snapshot"):
            ticker = data.get("msg", {}).get("market_ticker", "")
            await self.on_message("orderbook", ticker, data.get("msg", {}))
        elif msg_type == "trade":
            ticker = data.get("msg", {}).get("market_ticker", "")
            await self.on_message("trade", ticker, data.get("msg", {}))

    def stop(self):
        self._running = False
