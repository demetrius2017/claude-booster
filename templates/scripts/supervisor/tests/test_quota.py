"""Unit tests for quota.py.

Run:
    python3 -m pytest templates/scripts/supervisor/tests/test_quota.py -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from supervisor.quota import CircuitState, QuotaTracker  # noqa: E402


class TestQuotaTracker(unittest.TestCase):
    def test_initial_state_closed(self) -> None:
        q = QuotaTracker(session_id="s1")
        self.assertIs(q.state, CircuitState.CLOSED)
        self.assertEqual(q.supervisor_reserve_tokens, 7_500)  # 15% of 50k
        self.assertEqual(q.worker_budget_remaining, 42_500)

    def test_half_open_at_fifty_percent(self) -> None:
        q = QuotaTracker(session_id="s2")
        q.record(worker_tokens=25_000)  # exactly 50%
        self.assertIs(q.state, CircuitState.HALF_OPEN)

    def test_open_at_eighty_five_percent(self) -> None:
        q = QuotaTracker(session_id="s3")
        q.record(worker_tokens=42_500)  # 85%
        self.assertIs(q.state, CircuitState.OPEN)

    def test_admit_closed_within_budget(self) -> None:
        q = QuotaTracker(session_id="s4")
        ok, reason = q.admit(10_000)
        self.assertTrue(ok, reason)

    def test_admit_rejects_exceed_worker_budget(self) -> None:
        q = QuotaTracker(session_id="s5")
        # total cap 50k, reserve 7.5k → worker budget 42.5k
        ok, reason = q.admit(50_000)
        self.assertFalse(ok)
        self.assertIn("exceeds worker budget", reason)

    def test_admit_blocks_in_open_state(self) -> None:
        q = QuotaTracker(session_id="s6")
        q.record(worker_tokens=45_000)
        self.assertIs(q.state, CircuitState.OPEN)
        ok, reason = q.admit(1_000)
        self.assertFalse(ok)
        self.assertIn("OPEN", reason)

    def test_half_open_degrades_admission(self) -> None:
        q = QuotaTracker(session_id="s7")
        q.record(worker_tokens=26_000)  # above 50%
        self.assertIs(q.state, CircuitState.HALF_OPEN)
        # remaining worker budget 42_500 - 26_000 = 16_500 → half = 8_250
        ok_small, _ = q.admit(5_000)
        self.assertTrue(ok_small)
        ok_big, reason = q.admit(10_000)
        self.assertFalse(ok_big)
        self.assertIn("HALF_OPEN", reason)

    def test_record_refuses_negative(self) -> None:
        q = QuotaTracker(session_id="s8")
        with self.assertRaises(ValueError):
            q.record(supervisor_tokens=-1)

    def test_snapshot_roundtrip_shape(self) -> None:
        q = QuotaTracker(session_id="s9")
        q.record(supervisor_tokens=1_000, worker_tokens=2_000)
        snap = q.snapshot()
        for key in (
            "session_id", "started_at", "window_end",
            "supervisor_tokens", "worker_tokens", "circuit_state", "updated_at",
        ):
            self.assertIn(key, snap)
        self.assertEqual(snap["session_id"], "s9")
        self.assertEqual(snap["supervisor_tokens"], 1_000)
        self.assertEqual(snap["worker_tokens"], 2_000)
        self.assertEqual(snap["circuit_state"], "closed")


if __name__ == "__main__":
    unittest.main()
