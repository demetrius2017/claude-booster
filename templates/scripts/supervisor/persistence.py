#!/usr/bin/env python3
"""
Supervisor persistence — sqlite3 writers against rolling_memory.db.

Purpose:
  Persist supervisor_decisions + supervisor_quota rows so circuit
  state survives crashes (consilium §5/Q4). Applies schema.sql
  idempotently on init; does not bump rolling_memory SCHEMA_VERSION
  until supervisor.py main entry lands (see __init__.py).

Contract:
  SupervisorPersistence(db_path=~/.claude/rolling_memory.db)
    .record_decision(session_id, tool, args_digest, decision, tier,
                     rationale, approved_by, outcome=None) -> int
    .upsert_quota(snapshot: dict) -> None    # 1:1 with QuotaTracker.snapshot()
    .load_quota(session_id) -> dict | None
    .recent_by_args(args_digest, window_seconds) -> list[dict]   # §5/Q4 loop detection

Limitations:
  - Uses stdlib sqlite3, single-threaded writes. Supervisor MVP is
    single-process so no WAL contention beyond the project-wide PRAGMA.
  - Schema is CREATE IF NOT EXISTS — safe but does not migrate older
    shapes. Only bump via rolling_memory.SCHEMA_VERSION once stable.

ENV / Files:
  CLAUDE_BOOSTER_DB — override DB path (tests point at /tmp/*.db).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB = Path.home() / ".claude" / "rolling_memory.db"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupervisorPersistence:
    def __init__(self, db_path: str | Path | None = None) -> None:
        env_path = os.environ.get("CLAUDE_BOOSTER_DB")
        self.db_path = Path(db_path or env_path or DEFAULT_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._apply_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _apply_schema(self) -> None:
        ddl = SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(ddl)

    def record_decision(
        self,
        session_id: str,
        tool: str,
        args_digest: str,
        decision: str,
        tier: int | None,
        rationale: str,
        approved_by: str | None = None,
        outcome: str | None = None,
    ) -> int:
        if decision not in ("approve", "escalate", "deny"):
            raise ValueError(f"invalid decision: {decision!r}")
        if approved_by is not None and approved_by not in ("regex", "haiku", "dmitry"):
            raise ValueError(f"invalid approved_by: {approved_by!r}")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO supervisor_decisions "
                "(session_id, ts, tool, args_digest, decision, tier, rationale, approved_by, outcome) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, _utc_now_iso(), tool, args_digest, decision, tier, rationale, approved_by, outcome),
            )
            return int(cur.lastrowid)

    def upsert_quota(self, snapshot: dict) -> None:
        required = {"session_id", "started_at", "window_end", "supervisor_tokens", "worker_tokens", "circuit_state"}
        missing = required - snapshot.keys()
        if missing:
            raise ValueError(f"snapshot missing keys: {sorted(missing)}")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO supervisor_quota "
                "(session_id, started_at, window_end, supervisor_tokens, worker_tokens, circuit_state, updated_at) "
                "VALUES (:session_id, :started_at, :window_end, :supervisor_tokens, :worker_tokens, :circuit_state, :updated_at) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "window_end=excluded.window_end, "
                "supervisor_tokens=excluded.supervisor_tokens, "
                "worker_tokens=excluded.worker_tokens, "
                "circuit_state=excluded.circuit_state, "
                "updated_at=excluded.updated_at",
                {**snapshot, "updated_at": _utc_now_iso()},
            )

    def load_quota(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, started_at, window_end, supervisor_tokens, worker_tokens, circuit_state "
                "FROM supervisor_quota WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def recent_by_args(self, args_digest: str, window_seconds: int = 300) -> list[dict]:
        cutoff = datetime.now(timezone.utc).timestamp() - window_seconds
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, ts, tool, decision, tier FROM supervisor_decisions "
                "WHERE args_digest = ? AND ts >= ? ORDER BY ts DESC",
                (args_digest, datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()),
            ).fetchall()
        return [dict(r) for r in rows]
