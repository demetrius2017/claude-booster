#!/usr/bin/env python3
"""
Supervisor quota tracker — admission control + circuit-breaker.

Purpose:
  Address consilium §7 R1 (shared-quota self-deadlock): supervisor + worker
  share the Max/Pro 5-hour subscription quota. Without admission control
  workers can consume the whole window leaving supervisor unable to
  escalate/cleanup. This module reserves 15 %% for supervisor control
  traffic and flips state to half_open at 50 %% use / open at 85 %%.

Contract:
  tracker.admit(estimated_worker_tokens) -> (bool admitted, str reason)
  tracker.record(supervisor_tokens, worker_tokens) -> None
  tracker.state -> CircuitState ∈ {closed, half_open, open}
  tracker.snapshot() -> dict (persistable; maps 1:1 to supervisor_quota row)

Limitations:
  In-memory MVP. SQLite persistence is applied by runtime.py via
  schema.sql when supervisor.py main entry lands. On crash mid-session
  state is lost; supervisor re-seeds from supervisor_quota row on
  restart (not implemented in this skeleton).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    HALF_OPEN = "half_open"
    OPEN = "open"


@dataclass
class QuotaTracker:
    """Per-session token accounting over a 5-hour rolling window.

    Defaults come from consilium recommendations:
      reserve_pct=0.15 (D2 MVP choice, 15 %% supervisor reserve)
      half_open_threshold=0.50, open_threshold=0.85
      session_token_cap=50_000 (circuit-breaker per session)
      daily_token_cap=500_000 (cross-session cap, enforced externally)
      window_seconds=5*3600 (Max/Pro rolling window)
    """

    session_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reserve_pct: float = 0.15
    half_open_threshold: float = 0.50
    open_threshold: float = 0.85
    session_token_cap: int = 50_000
    window_seconds: int = 5 * 3600
    supervisor_tokens: int = 0
    worker_tokens: int = 0

    @property
    def total_used(self) -> int:
        return self.supervisor_tokens + self.worker_tokens

    @property
    def usage_pct(self) -> float:
        if self.session_token_cap <= 0:
            return 0.0
        return self.total_used / self.session_token_cap

    @property
    def state(self) -> CircuitState:
        pct = self.usage_pct
        if pct >= self.open_threshold:
            return CircuitState.OPEN
        if pct >= self.half_open_threshold:
            return CircuitState.HALF_OPEN
        return CircuitState.CLOSED

    @property
    def window_end(self) -> datetime:
        return self.started_at + timedelta(seconds=self.window_seconds)

    @property
    def supervisor_reserve_tokens(self) -> int:
        return int(self.session_token_cap * self.reserve_pct)

    @property
    def worker_budget_remaining(self) -> int:
        non_reserved = self.session_token_cap - self.supervisor_reserve_tokens
        return max(0, non_reserved - self.worker_tokens)

    def admit(self, estimated_worker_tokens: int) -> tuple[bool, str]:
        """Gate new worker spawn against the supervisor reserve + session cap."""
        if estimated_worker_tokens < 0:
            return False, "negative estimate"
        if self.state is CircuitState.OPEN:
            return False, f"circuit OPEN (usage {self.usage_pct:.0%})"
        remaining = self.worker_budget_remaining
        if estimated_worker_tokens > remaining:
            return (
                False,
                f"estimate {estimated_worker_tokens} exceeds worker budget "
                f"{remaining} (reserve {self.supervisor_reserve_tokens} held)",
            )
        if self.state is CircuitState.HALF_OPEN and estimated_worker_tokens > remaining // 2:
            return (
                False,
                f"HALF_OPEN degraded mode: estimate {estimated_worker_tokens} "
                f"> half-remaining {remaining // 2}",
            )
        return True, "ok"

    def record(self, supervisor_tokens: int = 0, worker_tokens: int = 0) -> None:
        if supervisor_tokens < 0 or worker_tokens < 0:
            raise ValueError("token counts must be non-negative")
        self.supervisor_tokens += supervisor_tokens
        self.worker_tokens += worker_tokens

    def snapshot(self) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "window_end": self.window_end.isoformat(),
            "supervisor_tokens": self.supervisor_tokens,
            "worker_tokens": self.worker_tokens,
            "circuit_state": self.state.value,
            "updated_at": now,
        }
