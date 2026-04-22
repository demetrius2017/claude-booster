"""Datetime and formatting helpers used across the project."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def format_money(value: Decimal, currency: str = "USD") -> str:
    return f"{value:,.2f} {currency}"


def clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
