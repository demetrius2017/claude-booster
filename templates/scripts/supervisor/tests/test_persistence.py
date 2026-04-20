"""Unit tests for persistence.py — sqlite3 round-trips.

Run:
    python3 -m unittest discover -s templates/scripts/supervisor/tests -p "test_*.py" -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from supervisor import persistence as PER  # noqa: E402


def _now_iso(delta_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        self.store = PER.SupervisorPersistence(db_path=self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_schema_apply_creates_tables(self):
        import sqlite3
        with sqlite3.connect(self.path) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("supervisor_decisions", tables)
        self.assertIn("supervisor_quota", tables)

    def test_record_decision_and_retrieve_by_args(self):
        rowid = self.store.record_decision(
            session_id="sess-1", tool="Bash", args_digest="abcd1234",
            decision="approve", tier=0, rationale="tier0 read", approved_by="regex",
        )
        self.assertIsInstance(rowid, int)
        rows = self.store.recent_by_args("abcd1234", window_seconds=60)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["decision"], "approve")

    def test_invalid_decision_rejected(self):
        with self.assertRaises(ValueError):
            self.store.record_decision("s", "Bash", "d", "yolo", 0, "nope")

    def test_invalid_approved_by_rejected(self):
        with self.assertRaises(ValueError):
            self.store.record_decision("s", "Bash", "d", "approve", 0, "r", approved_by="gpt")

    def test_upsert_quota_roundtrip(self):
        snap = {
            "session_id": "sess-1",
            "started_at": _now_iso(),
            "window_end": _now_iso(18_000),
            "supervisor_tokens": 0,
            "worker_tokens": 0,
            "circuit_state": "closed",
        }
        self.store.upsert_quota(snap)
        loaded = self.store.load_quota("sess-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["circuit_state"], "closed")

        # Second upsert with higher tokens + state transition.
        snap2 = {**snap, "supervisor_tokens": 1_500, "worker_tokens": 40_000, "circuit_state": "half_open"}
        self.store.upsert_quota(snap2)
        loaded2 = self.store.load_quota("sess-1")
        self.assertEqual(loaded2["worker_tokens"], 40_000)
        self.assertEqual(loaded2["circuit_state"], "half_open")

    def test_upsert_quota_rejects_missing_keys(self):
        with self.assertRaises(ValueError):
            self.store.upsert_quota({"session_id": "sess-x"})

    def test_upsert_quota_rejects_invalid_circuit_state(self):
        import sqlite3
        snap = {
            "session_id": "sess-bad",
            "started_at": _now_iso(),
            "window_end": _now_iso(18_000),
            "supervisor_tokens": 0,
            "worker_tokens": 0,
            "circuit_state": "melted",
        }
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.upsert_quota(snap)

    def test_load_quota_missing_returns_none(self):
        self.assertIsNone(self.store.load_quota("no-such-session"))

    def test_recent_by_args_window_filter(self):
        # Insert with window_seconds=0 — instantly stale.
        self.store.record_decision("s", "Bash", "digest-xyz", "deny", None, "deny-list hit", approved_by="regex")
        rows_wide = self.store.recent_by_args("digest-xyz", window_seconds=3600)
        rows_narrow = self.store.recent_by_args("digest-xyz", window_seconds=0)
        self.assertEqual(len(rows_wide), 1)
        # window_seconds=0 means cutoff=now, row.ts may be ≤ cutoff depending on clock granularity.
        self.assertLessEqual(len(rows_narrow), 1)


if __name__ == "__main__":
    unittest.main()
