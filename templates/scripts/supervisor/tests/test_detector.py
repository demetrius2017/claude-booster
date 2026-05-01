"""Unit tests for detector.py — FSM + adaptive silence + accelerator.

Run:
    python3 -m unittest discover -s templates/scripts/supervisor/tests -p "test_*.py" -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from supervisor import detector as D  # noqa: E402
from supervisor.runtime import WorkerEvent  # noqa: E402


def _ev(kind, ts, payload=None, task_id="tid"):
    return WorkerEvent(kind=kind, task_id=task_id, timestamp=ts, payload=payload or {})


class TestStateTransitions(unittest.TestCase):
    def test_initial_state_is_queued(self):
        det = D.WorkerStateDetector()
        self.assertEqual(det.state, D.State.QUEUED)

    def test_message_start_moves_to_running(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        self.assertEqual(det.state, D.State.RUNNING)
        self.assertEqual(det.started_at, 100.0)

    def test_thinking_start_then_stop(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.on_event(_ev("thinking_start", 101.0))
        self.assertEqual(det.state, D.State.THINKING)
        det.on_event(_ev("thinking_stop", 102.0))
        self.assertEqual(det.state, D.State.RUNNING)

    def test_tool_use_start_then_stop(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.on_event(_ev("tool_use_start", 101.0, {"name": "Bash"}))
        self.assertEqual(det.state, D.State.WAITING_ON_TOOL)
        det.on_event(_ev("tool_use_stop", 102.0))
        self.assertEqual(det.state, D.State.RUNNING)

    def test_message_stop_success_to_completed(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.on_event(_ev("message_stop", 105.0, {"subtype": "success"}))
        self.assertEqual(det.state, D.State.COMPLETED)

    def test_message_stop_failure_to_failed(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.on_event(_ev("message_stop", 105.0, {"subtype": "error_max_turns"}))
        self.assertEqual(det.state, D.State.FAILED)

    def test_text_delta_in_thinking_keeps_state(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.on_event(_ev("thinking_start", 101.0))
        det.on_event(_ev("text_delta", 102.0, {"text": "internal"}))
        self.assertEqual(det.state, D.State.THINKING)


class TestForceTerminal(unittest.TestCase):
    def test_force_to_blocked_by_quota(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.force(D.State.BLOCKED_BY_QUOTA)
        self.assertEqual(det.state, D.State.BLOCKED_BY_QUOTA)

    def test_force_ignores_subsequent_events(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.force(D.State.CANCELLED)
        det.on_event(_ev("text_delta", 101.0, {"text": "stragglers"}))
        self.assertEqual(det.state, D.State.CANCELLED)

    def test_force_rejects_non_terminal(self):
        det = D.WorkerStateDetector()
        with self.assertRaises(ValueError):
            det.force(D.State.RUNNING)


class TestAdaptiveSilence(unittest.TestCase):
    def test_below_grace_does_not_fire(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.on_event(_ev("text_delta", 101.0, {"text": "start"}))
        det.tick(105.0)  # 5s after last event, well under grace
        self.assertEqual(det.state, D.State.RUNNING)

    def test_insufficient_gaps_uses_max_silence(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 100.0))
        det.on_event(_ev("text_delta", 101.0, {"text": "x"}))
        # 1 gap only; median unavailable → threshold = MAX_SILENCE = 180
        self.assertEqual(det.silence_threshold(), D.MAX_SILENCE)

    def test_median_seeded_after_three_gaps(self):
        det = D.WorkerStateDetector()
        # 4 events → 3 gaps of 2s each; median=2 → threshold = max(20, min(180, 6)) = 20 (MIN_SILENCE)
        for i, ts in enumerate((100.0, 102.0, 104.0, 106.0)):
            det.on_event(_ev("message_start" if i == 0 else "text_delta", ts, {"text": "x"}))
        self.assertEqual(det.silence_threshold(), D.MIN_SILENCE)

    def test_possibly_complete_fires_after_silence(self):
        det = D.WorkerStateDetector()
        # seed 3 gaps of 10s → median=10 → threshold = clamp(30, 20, 180) = 30
        det.on_event(_ev("message_start", 0.0))
        det.on_event(_ev("text_delta", 10.0, {"text": "a"}))
        det.on_event(_ev("text_delta", 20.0, {"text": "b"}))
        det.on_event(_ev("text_delta", 30.0, {"text": "c"}))
        self.assertAlmostEqual(det.silence_threshold(), 30.0)
        # tick 65s after message_start (past grace), 35s since last event (past threshold)
        det.tick(65.0)
        self.assertEqual(det.state, D.State.POSSIBLY_COMPLETE)

    def test_tick_on_terminal_is_noop(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 0.0))
        det.on_event(_ev("message_stop", 1.0, {"subtype": "success"}))
        det.tick(10_000.0)
        self.assertEqual(det.state, D.State.COMPLETED)

    def test_silence_budget_decreases(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 0.0))
        det.on_event(_ev("text_delta", 5.0, {"text": "a"}))
        det.on_event(_ev("text_delta", 10.0, {"text": "b"}))
        det.on_event(_ev("text_delta", 15.0, {"text": "c"}))
        # median gap = 5 → threshold = 20 (min)
        budget_now = det.silence_budget(now=15.0)
        budget_later = det.silence_budget(now=25.0)
        self.assertGreater(budget_now, budget_later)
        self.assertEqual(budget_later, 10.0)


class TestAcceleratorHeuristic(unittest.TestCase):
    def test_should_i_phrase_activates_accelerator(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 0.0))
        det.on_event(_ev("text_delta", 1.0, {"text": "Should I proceed?"}))
        self.assertTrue(det.accelerator_active)

    def test_unrelated_text_does_not_activate(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 0.0))
        det.on_event(_ev("text_delta", 1.0, {"text": "Here is the diff"}))
        self.assertFalse(det.accelerator_active)

    def test_accelerator_halves_threshold_never_below_min(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 0.0))
        det.on_event(_ev("text_delta", 20.0, {"text": "a"}))
        det.on_event(_ev("text_delta", 40.0, {"text": "b"}))
        det.on_event(_ev("text_delta", 60.0, {"text": "Should I proceed?"}))
        # median gap 20 → threshold base = 60 → halved = 30 (still ≥ MIN_SILENCE=20)
        self.assertAlmostEqual(det.silence_threshold(), 30.0)

    def test_accelerator_never_authoritative_by_itself(self):
        det = D.WorkerStateDetector()
        det.on_event(_ev("message_start", 0.0))
        det.on_event(_ev("text_delta", 1.0, {"text": "Should I proceed?"}))
        # Accelerator alone does NOT move state to possibly_complete.
        # FSM still RUNNING because tick hasn't fired with enough silence.
        self.assertEqual(det.state, D.State.RUNNING)


if __name__ == "__main__":
    unittest.main()
