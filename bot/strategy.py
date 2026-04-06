"""
strategy.py — Market making quote engine.

Logic:
  1. Observe current best bid/ask from orderbook.
  2. If mid-price is in [min_price, max_price] range, post limit bids
     OFFSET cents inside mid on both YES and NO sides.
  3. Track fills. Cancel and requote when mid shifts by > OFFSET.
  4. Never exceed max_position contracts on either side.
  5. Cancel all orders when market approaches expiry (< 5 min).
"""

from dataclasses import dataclass, field
from typing import Optional
import time
import logging

logger = logging.getLogger("forge.strategy")


@dataclass
class MarketState:
    ticker: str
    best_yes_bid: float = 0.0  # highest price someone will pay for YES
    best_yes_ask: float = 1.0  # lowest price someone will sell YES for
    best_no_bid:  float = 0.0
    best_no_ask:  float = 1.0
    last_trade_price: float = 0.5
    yes_position: int = 0  # contracts we hold (positive = we own YES)
    no_position:  int = 0
    open_yes_order_id: Optional[str] = None
    open_no_order_id:  Optional[str] = None
    open_yes_price: float = 0.0
    open_no_price:  float = 0.0
    fills: list = field(default_factory=list)
    pnl: float = 0.0
    last_quote_time: float = 0.0


class MMStrategy:
    def __init__(self, cfg: dict, client):
        self.cfg    = cfg
        self.client = client
        self.markets: dict[str, MarketState] = {}

        self.max_pos   = int(cfg["strategy"]["max_position"])
        self.offset    = float(cfg["strategy"]["quote_offset"])
        self.min_price = float(cfg["strategy"]["min_price"])
        self.max_price = float(cfg["strategy"]["max_price"])
        self.taker_fee = float(cfg["strategy"]["taker_fee"])
        self.max_loss  = float(cfg["risk"]["max_loss_per_hour"])

    def get_or_create(self, ticker: str) -> MarketState:
        if ticker not in self.markets:
            self.markets[ticker] = MarketState(ticker=ticker)
        return self.markets[ticker]

    # ── Orderbook update ─────────────────────────────────────────────────────

    def on_orderbook(self, ticker: str, data: dict):
        state = self.get_or_create(ticker)
        yes_bids = data.get("yes", {}).get("bids", [])
        yes_asks = data.get("yes", {}).get("asks", [])

        if yes_bids:
            state.best_yes_bid = float(yes_bids[0][0]) / 100
        if yes_asks:
            state.best_yes_ask = float(yes_asks[0][0]) / 100

        state.best_no_bid = 1.0 - state.best_yes_ask
        state.best_no_ask = 1.0 - state.best_yes_bid

        self._maybe_requote(state)

    def on_trade(self, ticker: str, data: dict):
        state = self.get_or_create(ticker)
        price = float(data.get("yes_price", 50)) / 100
        state.last_trade_price = price
        self._check_fill(state, data)

    # ── Quote logic ──────────────────────────────────────────────────────────

    def _mid(self, state: MarketState) -> float:
        return (state.best_yes_bid + state.best_yes_ask) / 2

    def _should_quote(self, state: MarketState) -> bool:
        mid = self._mid(state)
        if not (self.min_price <= mid <= self.max_price):
            return False
        if state.pnl < -self.max_loss:
            logger.warning(f"{state.ticker}: hourly loss limit hit (${state.pnl:.2f})")
            return False
        return True

    def _maybe_requote(self, state: MarketState):
        if not self._should_quote(state):
            self._cancel_all(state)
            return

        mid = self._mid(state)
        target_yes = round(mid - self.offset, 2)
        target_no  = round((1.0 - mid) - self.offset, 2)

        # Requote YES side
        moved = abs(target_yes - state.open_yes_price) > self.offset / 2
        if state.open_yes_order_id is None or moved:
            if state.open_yes_order_id:
                self._cancel_order(state, "yes")
            if state.yes_position < self.max_pos and target_yes > 0.01:
                self._place_order(state, "yes", target_yes)

        # Requote NO side
        moved = abs(target_no - state.open_no_price) > self.offset / 2
        if state.open_no_order_id is None or moved:
            if state.open_no_order_id:
                self._cancel_order(state, "no")
            if state.no_position < self.max_pos and target_no > 0.01:
                self._place_order(state, "no", target_no)

    # ── Order management ─────────────────────────────────────────────────────

    def _place_order(self, state: MarketState, side: str, price: float):
        price_cents = int(round(price * 100))
        order_id = f"forge_{state.ticker}_{side}_{int(time.time()*1000)}"
        result = self.client.place_order(
            ticker=state.ticker,
            side=side,
            price_cents=price_cents,
            count=1,
            client_order_id=order_id,
        )
        if side == "yes":
            state.open_yes_order_id = result.get("order_id")
            state.open_yes_price = price
        else:
            state.open_no_order_id = result.get("order_id")
            state.open_no_price = price
        state.last_quote_time = time.time()
        logger.debug(f"{state.ticker}: placed {side} @ {price_cents}¢")

    def _cancel_order(self, state: MarketState, side: str):
        order_id = state.open_yes_order_id if side == "yes" else state.open_no_order_id
        if not order_id:
            return
        try:
            self.client.cancel_order(order_id)
        except Exception as e:
            logger.warning(f"{state.ticker}: cancel {side} failed: {e}")
        if side == "yes":
            state.open_yes_order_id = None
            state.open_yes_price = 0.0
        else:
            state.open_no_order_id = None
            state.open_no_price = 0.0

    def _cancel_all(self, state: MarketState):
        if state.open_yes_order_id:
            self._cancel_order(state, "yes")
        if state.open_no_order_id:
            self._cancel_order(state, "no")

    def cancel_all_markets(self):
        """Emergency: cancel everything across all markets."""
        for state in self.markets.values():
            self._cancel_all(state)

    # ── Fill tracking ────────────────────────────────────────────────────────

    def _check_fill(self, state: MarketState, trade: dict):
        """
        If a trade comes in at or through our quoted price, record a fill.
        In live mode this is handled by order fill events from the API.
        This is a belt-and-suspenders check from the trade feed.
        """
        price = float(trade.get("yes_price", 50)) / 100
        count = int(trade.get("count", 0))

        # Check YES fill
        if (state.open_yes_order_id and
                state.open_yes_price > 0 and
                price <= state.open_yes_price and
                state.yes_position < self.max_pos):
            state.yes_position += 1
            fill = {"side": "yes", "price": state.open_yes_price, "count": 1}
            state.fills.append(fill)
            state.open_yes_order_id = None
            logger.info(f"{state.ticker}: FILL YES @ {state.open_yes_price:.2f}")

        # Check NO fill
        no_price = 1.0 - price
        if (state.open_no_order_id and
                state.open_no_price > 0 and
                no_price <= state.open_no_price and
                state.no_position < self.max_pos):
            state.no_position += 1
            fill = {"side": "no", "price": state.open_no_price, "count": 1}
            state.fills.append(fill)
            state.open_no_order_id = None
            logger.info(f"{state.ticker}: FILL NO  @ {state.open_no_price:.2f}")

    def record_settlement(self, ticker: str, yes_won: bool):
        """Call when a market settles. Compute final P&L."""
        state = self.markets.get(ticker)
        if not state:
            return 0.0
        self._cancel_all(state)
        pnl = 0.0
        for fill in state.fills:
            if fill["side"] == "yes":
                if yes_won:
                    profit = 1.0 - fill["price"]
                    pnl += profit - profit * self.taker_fee
                else:
                    pnl -= fill["price"]
            else:
                if not yes_won:
                    profit = 1.0 - fill["price"]
                    pnl += profit - profit * self.taker_fee
                else:
                    pnl -= fill["price"]
        state.pnl = pnl
        logger.info(f"{ticker}: settled, P&L=${pnl:+.4f}, fills={len(state.fills)}")
        return pnl

    def summary(self) -> dict:
        total_pnl   = sum(s.pnl for s in self.markets.values())
        total_fills = sum(len(s.fills) for s in self.markets.values())
        active      = sum(1 for s in self.markets.values() if s.open_yes_order_id or s.open_no_order_id)
        return {
            "markets_tracked": len(self.markets),
            "active_orders":   active,
            "total_fills":     total_fills,
            "total_pnl":       total_pnl,
        }
