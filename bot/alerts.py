"""
alerts.py — Telegram alerts for fills, halts, errors, and P&L reports.
"""

import os
import logging
import requests

logger = logging.getLogger("forge.alerts")

_TOKEN   = os.environ.get("FORGE_TELEGRAM_TOKEN", "")
_CHAT_ID = os.environ.get("FORGE_TELEGRAM_CHAT_ID", "")


def _send(text: str):
    if not _TOKEN or not _CHAT_ID:
        logger.debug(f"[ALERT] {text}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")


def fill(ticker: str, side: str, price: float, count: int):
    _send(f"✅ *FILL* {ticker}\n{side.upper()} {count}x @ {price*100:.0f}¢")


def halt(reason: str):
    _send(f"🛑 *HALT* {reason}")


def error(msg: str):
    _send(f"⚠️ *ERROR* {msg}")


def pnl_report(summary: dict):
    lines = [
        "📊 *P&L Report*",
        f"Markets tracked: {summary['markets_tracked']}",
        f"Active orders:   {summary['active_orders']}",
        f"Total fills:     {summary['total_fills']}",
        f"Total P&L:       ${summary['total_pnl']:+.2f}",
    ]
    _send("\n".join(lines))


def startup(dry_run: bool):
    mode = "🟡 DRY RUN" if dry_run else "🟢 LIVE"
    _send(f"🔥 *Forge MM Bot started* — {mode}")


def shutdown(summary: dict):
    _send(
        f"🔴 *Forge MM Bot stopped*\n"
        f"Final P&L: ${summary['total_pnl']:+.2f}  |  Fills: {summary['total_fills']}"
    )
