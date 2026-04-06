"""
engine.py — Backtest engine.

Note on methodology:
The Kalshi public API provides executed trade prices (YES+NO = $1.00 always).
Historical order book depth is not available. This backtest uses a synthetic spread
model: we infer bid/ask around the trade midpoint and simulate fills when price
moves through our limit level. This approximates real market making P&L but
cannot capture queue position effects or actual bid/ask depth.

What this backtest IS good for:
- Price volatility analysis (how much does YES price swing per game?)
- Directional inventory risk (does price tend to trend, hurting one side?)
- Rough fill frequency at different offset/spread settings
- P&L distribution across many markets

What it CANNOT measure:
- Actual fill rates (depends on real queue position)
- LIP rebate eligibility and actual rebate amounts
- Slippage from thin markets
"""

from .fetch import get_all_market_trades
from .strategy import MarketMaker, MarketState


def run_backtest(config: dict, dry_run: bool = False) -> list[MarketState]:
    series = config["backtest"]["series_ticker"]
    limit = config["backtest"]["market_limit"]
    max_pages = config["backtest"].get("max_pages_per_market", 20)

    # Import here to avoid circular
    from .fetch import get_settled_markets
    print(f"\nFetching up to {limit} settled markets from series {series}...")
    markets = get_settled_markets(series, limit=limit)
    print(f"  Found {len(markets)} finalized markets.\n")

    if dry_run:
        print("[DRY RUN] Would backtest the following markets:")
        for m in markets:
            print(f"  {m['ticker']} — vol: {float(m.get('volume_fp','0')):,.0f} contracts, result: {m.get('result','?')}")
        return []

    mm = MarketMaker(config)
    results = []

    for i, market in enumerate(markets):
        ticker = market["ticker"]
        result_val = market.get("result", "").lower()

        if result_val == "yes":
            yes_won = True
        elif result_val == "no":
            yes_won = False
        else:
            print(f"  [{i+1}/{len(markets)}] {ticker} — skipped (result: '{result_val}')")
            continue

        print(f"  [{i+1}/{len(markets)}] {ticker} (result: {'YES' if yes_won else 'NO'})", end="", flush=True)

        trades = get_all_market_trades(ticker, max_pages=max_pages)

        # Filter to trades during active price discovery (not end-of-game cleanup)
        uncertain_trades = [t for t in trades
                            if 0.05 <= float(t["yes_price_dollars"]) <= 0.95]

        if len(uncertain_trades) < 3:
            print(f" — skipped (only {len(uncertain_trades)} uncertain-price trades out of {len(trades)})")
            continue

        state = MarketState(ticker=ticker)
        for trade in uncertain_trades:
            mm.process_trade(state, trade)

        mm.settle(state, yes_won)

        fills = len(state.fills)
        price_range = ""
        if uncertain_trades:
            prices = [float(t["yes_price_dollars"]) for t in uncertain_trades]
            price_range = f" | YES range: {min(prices):.2f}–{max(prices):.2f}"

        print(f" — {len(uncertain_trades)} active trades, {fills} fills, net: ${state.net_pnl:.4f}{price_range}")
        results.append(state)

    return results
