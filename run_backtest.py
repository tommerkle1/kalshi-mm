#!/usr/bin/env python3
"""
run_backtest.py — Entry point for Kalshi market making backtest.

Usage:
    python run_backtest.py              # Run full backtest
    python run_backtest.py --dry-run    # Show markets, don't simulate

No API key or Kalshi account required. Uses public trade history.
"""

import argparse
import sys

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # pip install tomli
    except ImportError:
        print("Error: tomllib not available. Run: pip install tomli")
        sys.exit(1)

from backtest.engine import run_backtest
from backtest.report import print_report


def load_config(path: str = "config.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    parser = argparse.ArgumentParser(description="Kalshi Market Making Backtest")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config and market list without running simulation"
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to config file (default: config.toml)"
    )
    args = parser.parse_args()

    print("Kalshi Market Making Backtest")
    print("==============================")

    config = load_config(args.config)

    print(f"Config:")
    print(f"  Series:          {config['backtest']['series_ticker']}")
    print(f"  Markets:         {config['backtest']['market_limit']}")
    print(f"  Min spread:      {config['strategy']['min_spread_cents']}¢")
    print(f"  Max position:    {config['strategy']['max_position_per_market']} contracts/side")
    print(f"  Taker fee:       {config['strategy']['taker_fee_pct']*100:.0f}%")
    print(f"  Starting capital: ${config['backtest']['starting_capital']:.0f}")

    if args.dry_run:
        print("\n[DRY RUN MODE — no simulation will run]")

    results = run_backtest(config, dry_run=args.dry_run)

    if not args.dry_run:
        print_report(results, config)


if __name__ == "__main__":
    main()
