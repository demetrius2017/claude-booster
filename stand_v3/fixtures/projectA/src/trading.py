"""Trading module. Main entry: calculate_pnl (renamed from compute_pnl on 2026-04-17)."""
from __future__ import annotations

from decimal import Decimal


def calculate_pnl(trades: list[dict]) -> Decimal:
    """Return total realized P&L from a list of trades."""
    total = Decimal("0")
    for t in trades:
        total += Decimal(str(t["proceeds"])) - Decimal(str(t["cost"]))
    return total


def apply_commissions(pnl: Decimal, commission_rate: Decimal) -> Decimal:
    return pnl - (abs(pnl) * commission_rate)


def realized_vs_unrealized(trades: list[dict]) -> tuple[Decimal, Decimal]:
    realized = Decimal("0")
    unrealized = Decimal("0")
    for t in trades:
        if t.get("closed", False):
            realized += Decimal(str(t["proceeds"])) - Decimal(str(t["cost"]))
        else:
            unrealized += Decimal(str(t.get("mark", 0))) - Decimal(str(t["cost"]))
    return realized, unrealized
