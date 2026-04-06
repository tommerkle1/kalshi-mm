"""
risk.py — Portfolio-level risk monitor.

Checks:
  - Max drawdown from peak balance: halt if exceeded
  - Position hard cap across all markets
  - Reports breach immediately via alerts
"""

import logging
from bot import alerts

logger = logging.getLogger("forge.risk")


class RiskMonitor:
    def __init__(self, cfg: dict, starting_balance: float):
        self.max_drawdown_pct = float(cfg["risk"]["max_drawdown_pct"])
        self.position_hard_cap = int(cfg["risk"]["position_hard_cap"])
        self.peak_balance = starting_balance
        self.current_balance = starting_balance
        self.halted = False

    def update_balance(self, balance: float):
        self.current_balance = balance
        if balance > self.peak_balance:
            self.peak_balance = balance
        self._check_drawdown()

    def _check_drawdown(self):
        if self.peak_balance <= 0:
            return
        dd = (self.peak_balance - self.current_balance) / self.peak_balance
        if dd >= self.max_drawdown_pct and not self.halted:
            self.halted = True
            msg = (f"Drawdown {dd*100:.1f}% exceeded limit {self.max_drawdown_pct*100:.0f}%. "
                   f"Peak: ${self.peak_balance:.2f}  Current: ${self.current_balance:.2f}")
            logger.critical(f"HALT — {msg}")
            alerts.halt(msg)

    def check_positions(self, strategy) -> bool:
        """Returns True if positions are within limits."""
        total = sum(
            s.yes_position + s.no_position
            for s in strategy.markets.values()
        )
        if total > self.position_hard_cap:
            msg = f"Position hard cap exceeded: {total} contracts (limit {self.position_hard_cap})"
            logger.critical(f"HALT — {msg}")
            alerts.halt(msg)
            self.halted = True
            return False
        return True

    @property
    def ok(self) -> bool:
        return not self.halted
