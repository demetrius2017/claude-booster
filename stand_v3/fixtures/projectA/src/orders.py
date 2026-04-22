"""Order lifecycle primitives: creation, status, cancellation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    quantity: Decimal
    limit_price: Decimal | None = None
    status: str = "new"
    filled_qty: Decimal = Decimal("0")
    created_at: datetime = field(default_factory=datetime.utcnow)

    def is_filled(self) -> bool:
        return self.status == "filled" and self.filled_qty >= self.quantity

    def remaining(self) -> Decimal:
        return self.quantity - self.filled_qty


def cancel_order(order: Order) -> Order:
    if order.status in ("filled", "cancelled"):
        return order
    order.status = "cancelled"
    return order
