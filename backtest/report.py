"""
report.py — Print summary stats from backtest results.
"""

from .strategy import MarketState


def print_report(results: list[MarketState], config: dict) -> None:
    if not results:
        print("\nNo results to report.")
        return

    starting_capital = config["backtest"]["starting_capital"]

    total_markets = len(results)
    total_fills = sum(len(r.fills) for r in results)
    total_round_trips = sum(r.round_trips for r in results)
    total_gross = sum(r.gross_pnl for r in results)
    total_fees = sum(r.fees_paid for r in results)
    total_net = sum(r.net_pnl for r in results)
    profitable_markets = sum(1 for r in results if r.net_pnl > 0)

    # Win rate: round trips where we won (fills that resolved in our favor)
    winning_fills = sum(
        1 for r in results
        for f in r.fills
        if f.revenue > 0
    )
    total_resolved_fills = sum(len(r.fills) for r in results)
    win_rate = (winning_fills / total_resolved_fills * 100) if total_resolved_fills > 0 else 0

    roi = (total_net / starting_capital * 100) if starting_capital > 0 else 0
    avg_per_trip = (total_net / total_round_trips) if total_round_trips > 0 else 0

    # Best and worst markets
    results_sorted = sorted(results, key=lambda r: r.net_pnl, reverse=True)
    best = results_sorted[0] if results_sorted else None
    worst = results_sorted[-1] if results_sorted else None

    print("\n" + "=" * 50)
    print("  Kalshi Market Making Backtest Results")
    print("=" * 50)
    print(f"  Series:                 {config['backtest']['series_ticker']}")
    print(f"  Markets analyzed:       {total_markets}")
    print(f"  Total fills:            {total_fills}")
    print(f"  Completed round-trips:  {total_round_trips}")
    print(f"  Fill win rate:          {win_rate:.1f}%")
    print(f"")
    print(f"  Gross P&L:              ${total_gross:.4f}")
    print(f"  Fees paid:              ${total_fees:.4f}")
    print(f"  Net P&L:                ${total_net:.4f}")
    print(f"  Net ROI on ${starting_capital:.0f}:      {roi:.2f}%")
    print(f"  Avg net per round-trip: ${avg_per_trip:.4f}")
    print(f"")
    print(f"  Profitable markets:     {profitable_markets} / {total_markets}")

    if best:
        print(f"  Best market:            {best.ticker} (${best.net_pnl:.4f})")
    if worst:
        print(f"  Worst market:           {worst.ticker} (${worst.net_pnl:.4f})")

    print("=" * 50)

    # Interpretation
    print("\nInterpretation:")
    if total_net > 0:
        print(f"  ✅ Strategy is profitable over {total_markets} markets.")
        print(f"  At ${starting_capital:.0f} deployed, estimated monthly return: ${total_net:.2f}")
        print(f"  (Assumes similar number of markets/month as in this backtest)")
    else:
        print(f"  ❌ Strategy lost money over {total_markets} markets.")
        print(f"  Review spread settings or market selection before going live.")

    if total_fills < 10:
        print(f"\n  ⚠️  Low fill count ({total_fills}). Consider reducing min_spread_cents")
        print(f"     or increasing quote_offset_cents to improve fill rate.")
