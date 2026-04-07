"""
run_bot.py — Forge market making bot entrypoint.

Usage:
  python run_bot.py           # dry run (default, config.toml dry_run=true)
  python run_bot.py --live    # live trading (requires dry_run=false in config.toml)

Env vars required:
  KALSHI_API_KEY         — from kalshi.com/profile/api
  KALSHI_PRIVATE_KEY     — path to downloaded RSA private key PEM
  FORGE_TELEGRAM_TOKEN   — Forge bot token
  FORGE_TELEGRAM_CHAT_ID — Tom's Telegram chat ID

Architecture:
  - Main loop: scan for open KXBTCD markets every poll_interval_sec
  - Per market: subscribe to WS feed, run quote engine
  - Risk monitor: checks drawdown + position caps continuously
  - Graceful shutdown on SIGINT/SIGTERM: cancel all orders, report P&L
"""

import asyncio
import signal
import logging
import time
import os
import sys
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path

ROOT = Path(__file__).parent

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-20s %(levelname)-8s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "data" / "bot.log"),
    ],
)
logger = logging.getLogger("forge.main")


def load_config() -> dict:
    with open(ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


async def main(live: bool = False):
    from bot.kalshi_client import KalshiREST, KalshiWSClient
    from bot.strategy import MMStrategy
    from bot.risk import RiskMonitor
    from bot import alerts

    cfg = load_config()
    dry_run = not live  # CLI --live flag overrides config

    if live and cfg["live"].get("dry_run", True):
        print("ERROR: --live passed but config.toml has dry_run=true. Set dry_run=false first.")
        sys.exit(1)

    logger.info(f"Starting Forge MM Bot — {'DRY RUN' if dry_run else 'LIVE TRADING'}")

    client   = KalshiREST(dry_run=dry_run)
    balance  = client.get_balance() if not dry_run else float(cfg["backtest"]["starting_capital"])
    strategy = MMStrategy(cfg, client)
    risk     = RiskMonitor(cfg, starting_balance=balance)

    alerts.startup(dry_run)

    # ── Shutdown handler ─────────────────────────────────────────────────────
    shutdown_event = asyncio.Event()

    def handle_signal(sig, frame):
        logger.info(f"Signal {sig} received — shutting down")
        shutdown_event.set()

    signal.signal(signal.SIGINT,  handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    series  = cfg["strategy"]["series_ticker"]
    poll    = float(cfg["live"]["poll_interval_sec"])
    report_every = int(cfg["alerts"]["pnl_report_hours"]) * 3600
    last_report  = time.time()
    active_tickers: set[str] = set()
    _error_counts: dict = {}   # ticker → consecutive error count
    _error_tickers: set[str] = set()  # tickers suppressed until next market refresh

    # ── WS message handler ───────────────────────────────────────────────────
    async def on_ws_message(channel: str, ticker: str, data: dict):
        if not risk.ok:
            return
        if channel == "orderbook":
            strategy.on_orderbook(ticker, data)
        elif channel == "trade":
            strategy.on_trade(ticker, data)

    # ── Main loop ────────────────────────────────────────────────────────────
    ws_task = None

    while not shutdown_event.is_set():
        try:
            # Refresh balance and risk check
            if not dry_run:
                balance = client.get_balance()
                risk.update_balance(balance)
            risk.check_positions(strategy)

            if not risk.ok:
                logger.critical("Risk halt active — cancelling all orders")
                strategy.cancel_all_markets()
                break

            # Get live BTC price for strike filtering
            btc_price = None
            try:
                import requests as _req
                r = _req.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=5)
                btc_price = float(r.json()["data"]["amount"])
                logger.debug(f"BTC spot: ${btc_price:,.0f}")
            except Exception:
                pass

            # Discover currently open markets near current BTC price
            markets = client.get_active_markets(series=series, btc_price=btc_price, band_pct=0.05)
            if not markets:
                # Fallback: wider band or no filter
                markets = client.get_active_markets(series=series, btc_price=btc_price, band_pct=0.10)
            logger.info(f"Active markets near ${btc_price:,.0f}: {len(markets)}")
            new_tickers = {m["ticker"] for m in markets}
            # Clear error suppression for any tickers no longer in the active list
            _error_tickers -= (active_tickers - new_tickers)

            # Cancel orders + record settlement for markets that just closed
            closed = active_tickers - new_tickers
            for ticker in closed:
                market_data = client.get_market(ticker)
                yes_won = market_data.get("result", "").lower() == "yes"
                pnl = strategy.record_settlement(ticker, yes_won)
                logger.info(f"Market settled: {ticker}  yes_won={yes_won}  P&L=${pnl:+.4f}")
                active_tickers.discard(ticker)

            # Subscribe WS to any newly opened markets
            opened = new_tickers - active_tickers
            if opened and not dry_run:
                if ws_task and not ws_task.done():
                    ws_task.cancel()
                ws_client = KalshiWSClient(on_ws_message, list(new_tickers))
                ws_task = asyncio.create_task(ws_client.connect())
                active_tickers = new_tickers.copy()
                logger.info(f"Subscribed WS to {len(new_tickers)} markets")

            # In dry run: use market object bid/ask directly (skip broken orderbook endpoint)
            if dry_run:
                quoted = 0
                for market in markets[:cfg["strategy"]["max_concurrent"]]:
                    ticker = market["ticker"]
                    bid = float(market.get("yes_bid_dollars") or 0)
                    ask = float(market.get("yes_ask_dollars") or 1)
                    if bid == 0 and ask >= 1.0:
                        continue  # no market yet, skip
                    strategy.on_orderbook(ticker, market)
                    quoted += 1
                if quoted:
                    logger.info(f"Quoted {quoted} markets | BTC=${btc_price:,.0f}")

            # Periodic P&L report
            if time.time() - last_report >= report_every:
                summary = strategy.summary()
                alerts.pnl_report(summary)
                logger.info(f"P&L report: {summary}")
                last_report = time.time()

        except Exception as e:
            logger.exception(f"Main loop error: {e}")
            alerts.error(str(e))

        await asyncio.sleep(poll)

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down — cancelling all open orders")
    strategy.cancel_all_markets()
    summary = strategy.summary()
    alerts.shutdown(summary)
    logger.info(f"Final summary: {summary}")
    print(f"\n✓ Shutdown complete. Final P&L: ${summary['total_pnl']:+.2f}")


if __name__ == "__main__":
    live = "--live" in sys.argv
    asyncio.run(main(live=live))
