"""
strategy.py — Market making backtest strategy.

KEY INSIGHT about Kalshi trade data:
- The public trades API returns EXECUTED trades: (yes_price, no_price) where yes+no = $1.00 always.
- There is no spread visible in the trade feed — spread lives in the ORDER BOOK (bids/asks).
- Historical order book depth is not available via the public API.

WHAT WE CAN BACKTEST:
- Price movement patterns (how much does the YES price move during a game?)
- Inventory risk (how often does price move decisively one way?)
- Spread inference: we assume a typical bid/ask spread of N cents around the last trade price.

APPROACH: Synthetic spread model
- At each trade, infer a synthetic bid/ask: bid = trade_price - spread/2, ask = trade_price + spread/2
- Simulate posting a YES bid 1¢ below current trade price and a NO bid 1¢ below (1 - trade_price)
- A "fill" occurs when price moves through our posted limit
- This models what a market maker WOULD earn if they could post orders at those levels
- It's an approximation — real fills depend on queue position and actual orderbook depth

This gives us:
1. Price volatility data per market (how much does YES price swing?)
2. Estimated fill frequency at different spread targets
3. Directional P&L after settlement (did inventory build up on the wrong side?)
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Fill:
    side: str
    price: float
    size: int = 1
    revenue: float = 0.0


@dataclass
class Position:
    yes_contracts: int = 0
    no_contracts: int = 0
    cost_basis: float = 0.0


@dataclass
class MarketState:
    ticker: str
    position: Position = field(default_factory=Position)
    fills: list = field(default_factory=list)
    gross_pnl: float = 0.0
    fees_paid: float = 0.0
    round_trips: int = 0
    yes_quote_price: Optional[float] = None   # our active YES bid
    no_quote_price: Optional[float] = None    # our active NO bid
    last_yes_price: Optional[float] = None
    price_moves: list = field(default_factory=list)  # track price deltas

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees_paid


class MarketMaker:
    def __init__(self, config: dict):
        self.min_spread = config["strategy"]["min_spread_cents"] / 100.0
        self.max_pos = config["strategy"]["max_position_per_market"]
        self.taker_fee = config["strategy"]["taker_fee_pct"]
        self.offset = config["strategy"]["quote_offset_cents"] / 100.0
        # Assumed bid-ask spread around midpoint (market maker captures half of this)
        self.assumed_spread = config["strategy"].get("assumed_spread_cents", 3) / 100.0

    def process_trade(self, state: MarketState, trade: dict) -> None:
        """
        Process one trade. The trade price IS the midpoint.
        We infer bid/ask as midpoint ± assumed_spread/2.
        Check if our resting quotes would have been filled.
        """
        yes_price = float(trade["yes_price_dollars"])  # executed YES price = midpoint

        # Track price movement
        if state.last_yes_price is not None:
            delta = yes_price - state.last_yes_price
            state.price_moves.append(delta)

        # YES bid we'd post: midpoint - offset (below current price, waiting for dip)
        # A fill occurs if price drops TO our bid level
        if state.yes_quote_price is not None:
            if yes_price <= state.yes_quote_price and state.position.yes_contracts < self.max_pos:
                fill = Fill(side="yes", price=state.yes_quote_price)
                state.fills.append(fill)
                state.position.yes_contracts += 1
                state.position.cost_basis += state.yes_quote_price
                state.yes_quote_price = None  # filled, cancel

        # NO bid: post at (1 - yes_price) - offset
        no_price = 1.0 - yes_price
        if state.no_quote_price is not None:
            if no_price <= state.no_quote_price and state.position.no_contracts < self.max_pos:
                fill = Fill(side="no", price=state.no_quote_price)
                state.fills.append(fill)
                state.position.no_contracts += 1
                state.position.cost_basis += state.no_quote_price
                state.no_quote_price = None

        # Post new quotes if we have capacity
        # We only quote if there's meaningful price uncertainty (not near 1¢ or 99¢)
        if 0.05 <= yes_price <= 0.95:
            if state.yes_quote_price is None and state.position.yes_contracts < self.max_pos:
                state.yes_quote_price = round(yes_price - self.offset, 4)

            if state.no_quote_price is None and state.position.no_contracts < self.max_pos:
                state.no_quote_price = round(no_price - self.offset, 4)

        state.last_yes_price = yes_price

    def settle(self, state: MarketState, yes_won: bool) -> None:
        """Settle positions. Apply taker fee on winning side."""
        state.yes_quote_price = None
        state.no_quote_price = None

        gross = 0.0
        fees = 0.0

        for fill in state.fills:
            if fill.side == "yes":
                if yes_won:
                    profit = (1.0 - fill.price) * fill.size
                    fee = profit * self.taker_fee
                    gross += profit
                    fees += fee
                    fill.revenue = profit - fee
                    state.round_trips += 1
                else:
                    loss = fill.price * fill.size
                    gross -= loss
                    fill.revenue = -loss
            else:  # NO
                no_win = not yes_won
                if no_win:
                    profit = (1.0 - fill.price) * fill.size
                    fee = profit * self.taker_fee
                    gross += profit
                    fees += fee
                    fill.revenue = profit - fee
                    state.round_trips += 1
                else:
                    loss = fill.price * fill.size
                    gross -= loss
                    fill.revenue = -loss

        state.gross_pnl = gross
        state.fees_paid = fees
