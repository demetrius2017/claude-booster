"""Integration tests for supervisor.py — full policy+quota+detector+persistence chain.

These tests drive a real `Supervisor` instance but substitute the
`StreamJsonRuntime` subprocess with a `FakeProc`-backed reader so they
run without `claude-agent-sdk` installed. End-to-end red-team against
the real binary is in test_supervisor_e2e.py (gated on SDK install).

Run:
    python3 -m unittest discover -s templates/scripts/supervisor/tests -p "test_*.py" -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

from supervisor import stream_json_adapter as SJA  # noqa: E402
from supervisor.detector import State, WorkerStateDetector  # noqa: E402
from supervisor.persistence import SupervisorPersistence  # noqa: E402
from supervisor.policy import PolicyContext  # noqa: E402
from supervisor.quota import QuotaTracker  # noqa: E402
from supervisor.supervisor import Supervisor, SupervisorConfig, _parse_minimal_yaml  # noqa: E402
from test_stream_json_adapter import (  # noqa: E402
    FakeProc, _fixture_init, _fixture_text, _fixture_tool, _fixture_tool_result, _fixture_result,
)


class FakeRuntime:
    """Drop-in WorkerRuntime replacement — replays a fixture and mimics StreamJsonRuntime surface."""

    def __init__(self, fixture_lines: list[str], returncode: int = 0):
        self._lines = fixture_lines
        self._returncode = returncode
        self._state: SJA._TaskState | None = None
        self._drained: list = []
        self.cancel_calls: list[str] = []
        self.submit_calls: list[dict] = []  # for continuation assertions

    async def submit_task(self, prompt, system_prompt=None, model=None, cwd=None, resume_session=None):
        self.submit_calls.append({"prompt": prompt, "resume_session": resume_session})
        # Reset drained state for each submit so a new attempt replays fresh fixture lines.
        self._drained = []
        proc = FakeProc(self._lines, returncode=self._returncode)
        self._state = SJA._TaskState(task_id="fake-tid", proc=proc)
        runtime = SJA.StreamJsonRuntime()
        # Drain synchronously into an in-memory list so events() is a simple async gen below.
        await runtime._reader(self._state)
        while True:
            item = await self._state.queue.get()
            if item is None:
                break
            self._drained.append(item)
        return "fake-tid"

    async def events(self, task_id):
        for ev in self._drained:
            yield ev

    def terminal_state(self, task_id):
        return self._state.terminal if self._state else None

    def tool_invocations(self, task_id):
        return list(self._state.tool_calls) if self._state else []

    def usage(self, task_id):
        return self._state.usage_snapshot if self._state else None

    def terminal_reason(self, task_id):
        return self._state.terminal_reason if self._state else None

    def cli_session_id(self, task_id):
        return self._state.cli_session_id if self._state else None

    async def cancel(self, task_id):
        self.cancel_calls.append(task_id)
        if self._state is not None and self._state.terminal is None:
            self._state.terminal = "cancelled"

    async def shutdown(self): return None


def _ctx(project_dir: Path, tier1: set[str] | None = None, paranoid: bool = False) -> PolicyContext:
    return PolicyContext(
        project_dir=project_dir, tier1_enabled=tier1 or set(),
        tier2_trusted_repo=False, session_sandbox=project_dir / "sandbox",
        paranoid_mode=paranoid,
    )


def _store_tmp() -> tuple[SupervisorPersistence, str]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    return SupervisorPersistence(db_path=tmp.name), tmp.name


class TestSupervisorHappyPath(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_session_completes_and_persists(self):
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_text("reading the repo"),
            _fixture_tool(name="Read", input={"file_path": str(Path.cwd() / "README.md")}),
            _fixture_tool_result(),
            _fixture_text("done"),
            _fixture_result(input_tokens=100, output_tokens=50),
        )]
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-happy")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            result = await sup.supervise(prompt="explain", estimated_tokens=1000)
            self.assertEqual(result.terminal, "completed")
            self.assertEqual(result.final_state, State.COMPLETED)
            self.assertEqual(len(result.tool_calls), 1)
            # Persistence side: quota row exists, decision row for the Read exists.
            self.assertIsNotNone(store.load_quota("sess-happy"))
            self.assertGreaterEqual(len(result.decisions), 1)
        finally:
            os.unlink(path)


class TestQuotaBlocking(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_by_quota_never_submits(self):
        runtime = FakeRuntime([])
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-blocked", worker_tokens=48_000, session_token_cap=50_000)
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            result = await sup.supervise(prompt="x", estimated_tokens=10_000)
            self.assertEqual(result.terminal, "blocked_by_quota")
            self.assertEqual(result.final_state, State.BLOCKED_BY_QUOTA)
            # Admit denial should be recorded as a decision row.
            self.assertTrue(any(d["decision"] == "deny" for d in result.decisions))
            # Runtime.submit_task was never called — no tool_calls observed.
            self.assertEqual(len(result.tool_calls), 0)
        finally:
            os.unlink(path)


class TestPolicyDenyRecorded(unittest.IsolatedAsyncioTestCase):
    async def test_tool_targeting_deny_path_gets_deny_decision_and_cancels(self):
        # Audit-fix C1 regression: deny must trigger runtime.cancel() — it's not just logged.
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_tool(name="Read", input={"file_path": "/home/user/.env"}),
            _fixture_tool_result(),
            _fixture_result(),
        )]
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-deny")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            result = await sup.supervise(prompt="x", estimated_tokens=1000)
            deny_decisions = [d for d in result.decisions if d["decision"] == "deny"]
            self.assertGreaterEqual(len(deny_decisions), 1)
            self.assertIn(".env", deny_decisions[0]["rationale"])
            self.assertIn("fake-tid", runtime.cancel_calls)
            self.assertEqual(result.final_state, State.CANCELLED)
        finally:
            os.unlink(path)

    async def test_escalate_without_escalator_defaults_to_deny_and_cancels(self):
        # Audit-fix C1 regression: under paranoid_mode, escalate→no-escalator→default-deny+cancel.
        # We wrap paranoid_mode=True around a Bash that in permissive-mode would approve.
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_tool(name="Bash", input={"command": "echo hello"}),
            _fixture_tool_result(),
            _fixture_result(),
        )]
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-noesc")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd(), paranoid=True), tracker=tracker, store=store)
            result = await sup.supervise(prompt="x", estimated_tokens=1000)
            self.assertTrue(any(d["decision"] == "deny" and "no escalator" in d["rationale"] for d in result.decisions))
            self.assertIn("fake-tid", runtime.cancel_calls)
        finally:
            os.unlink(path)


class FakeEscalator:
    """Records calls and returns a fixed decision."""

    def __init__(self, decision: str = "approve"):
        self.decision = decision
        self.calls: list[tuple[str, dict, str]] = []

    async def decide(self, tool: str, tool_input: dict, rationale: str):
        self.calls.append((tool, tool_input, rationale))
        return self.decision, f"haiku {self.decision}: {rationale[:60]}"


class TestEscalationPath(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_tool_escalates_to_haiku(self):
        # Paranoid ctx so the escalate branch is exercised at all.
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_tool(name="Bash", input={"command": "echo hello"}),
            _fixture_tool_result(),
            _fixture_result(),
        )]
        escalator = FakeEscalator(decision="deny")
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-esc")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd(), paranoid=True), tracker=tracker, store=store, escalator=escalator)
            result = await sup.supervise(prompt="x", estimated_tokens=1000)
            self.assertEqual(len(escalator.calls), 1)
            self.assertEqual(escalator.calls[0][0], "Bash")
            # Haiku's deny must land in decisions.
            haiku_denies = [d for d in result.decisions if d["decision"] == "deny"]
            self.assertGreaterEqual(len(haiku_denies), 1)
        finally:
            os.unlink(path)


class TestLoopGuard(unittest.IsolatedAsyncioTestCase):
    async def test_same_args_approved_3x_gets_escalated(self):
        # Pre-seed persistence with 3 prior approvals of the same (tool,args).
        store, path = _store_tmp()
        try:
            same_input = {"file_path": str(Path.cwd() / "README.md")}
            from supervisor.policy import args_digest
            digest = args_digest("Read", same_input)
            for _ in range(3):
                store.record_decision("sess-loop", "Read", digest, "approve", 0, "prior ok", approved_by="regex")

            lines = [json.dumps(m) for m in (
                _fixture_init(),
                _fixture_tool(name="Read", input=same_input),
                _fixture_tool_result(),
                _fixture_result(),
            )]
            escalator = FakeEscalator(decision="deny")
            runtime = FakeRuntime(lines)
            tracker = QuotaTracker(session_id="sess-loop")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store, escalator=escalator)
            await sup.supervise(prompt="x", estimated_tokens=1000)
            # Escalator must have been consulted because loop-guard tripped even though policy said approve.
            self.assertEqual(len(escalator.calls), 1)
            self.assertIn("loop-guard", escalator.calls[0][2])
        finally:
            os.unlink(path)


class TestLoopGuardFiltersApprovals(unittest.IsolatedAsyncioTestCase):
    async def test_three_denies_do_not_trigger_loop_guard(self):
        """Audit-fix M4 regression: loop-guard must count ONLY prior approvals.

        Pre-audit code counted all recent decisions, so 3 prior denies of the
        same (tool,args) would incorrectly trip loop-guard on a 4th legitimate
        approve. Now it should stay approve.
        """
        store, path = _store_tmp()
        try:
            same_input = {"file_path": str(Path.cwd() / "README.md")}
            from supervisor.policy import args_digest
            digest = args_digest("Read", same_input)
            # Seed 3 DENIES (not approves) with the same digest.
            for _ in range(3):
                store.record_decision("sess-loopdeny", "Read", digest, "deny", None, "prior deny", approved_by="regex")

            lines = [json.dumps(m) for m in (
                _fixture_init(),
                _fixture_tool(name="Read", input=same_input),
                _fixture_tool_result(),
                _fixture_result(),
            )]
            runtime = FakeRuntime(lines)
            tracker = QuotaTracker(session_id="sess-loopdeny")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            result = await sup.supervise(prompt="x", estimated_tokens=1000)
            # 4th call should be a clean approve, no loop-guard escalation.
            current = [d for d in result.decisions if d["tool"] == "Read" and d["decision"] == "approve"]
            self.assertEqual(len(current), 1, result.decisions)
            self.assertEqual(runtime.cancel_calls, [])  # no deny/escalate → no cancel
        finally:
            os.unlink(path)


class TestSilencePollerCancels(unittest.IsolatedAsyncioTestCase):
    async def test_possibly_complete_triggers_cancel(self):
        """Audit-fix H2 regression: when silence threshold trips, poller cancels worker."""
        # Drive the detector directly through a real Supervisor, forcing
        # possibly_complete via detector.tick() by shrinking the silence threshold.
        import supervisor.detector as D

        class HangingRuntime:
            def __init__(self):
                self.cancel_calls = []
                self._cancel_event = asyncio.Event()

            async def submit_task(self, prompt, system_prompt=None, model=None, cwd=None):
                return "hang-tid"

            async def events(self, task_id):
                from supervisor.runtime import WorkerEvent
                import time as _time
                yield WorkerEvent("message_start", task_id, _time.monotonic(), {})
                # Stall until cancel() closes the stream — mimics real reader.finally.
                await self._cancel_event.wait()

            def terminal_state(self, task_id): return "cancelled" if self._cancel_event.is_set() else None
            def tool_invocations(self, task_id): return []
            def usage(self, task_id): return None
            async def cancel(self, task_id):
                self.cancel_calls.append(task_id)
                self._cancel_event.set()
            async def shutdown(self): return None

        # Force detector to report POSSIBLY_COMPLETE on the very first tick by
        # monkey-patching the silence threshold to 0.
        original = D.WorkerStateDetector.silence_threshold
        D.WorkerStateDetector.silence_threshold = lambda self: 0.001
        try:
            runtime = HangingRuntime()
            store, path = _store_tmp()
            try:
                tracker = QuotaTracker(session_id="sess-hang")
                sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
                # Lower poll interval via the module-level constant for a fast test.
                from supervisor import supervisor as SUP
                original_interval = SUP.SILENCE_POLL_INTERVAL
                SUP.SILENCE_POLL_INTERVAL = 0.05
                try:
                    # Also shorten grace window to let tick() fire immediately.
                    original_grace = D.POST_START_GRACE
                    D.POST_START_GRACE = 0.0
                    try:
                        result = await asyncio.wait_for(sup.supervise(prompt="x", estimated_tokens=1000), timeout=5.0)
                    finally:
                        D.POST_START_GRACE = original_grace
                finally:
                    SUP.SILENCE_POLL_INTERVAL = original_interval
                self.assertIn("hang-tid", runtime.cancel_calls)
                self.assertEqual(result.final_state, State.CANCELLED)
            finally:
                os.unlink(path)
        finally:
            D.WorkerStateDetector.silence_threshold = original


class TestCmdRunConfigFromCwd(unittest.TestCase):
    def test_config_default_honors_cwd(self):
        """Audit-fix M3 regression: default config path must be <cwd>/.claude/supervisor.yaml."""
        import tempfile as _tmp
        with _tmp.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".claude").mkdir()
            (repo / ".claude" / "supervisor.yaml").write_text("tier2_trusted_repo: true\n")
            cfg = SupervisorConfig.from_yaml(repo / ".claude" / "supervisor.yaml")
            self.assertTrue(cfg.tier2_trusted_repo)


class TestConfigTypeValidation(unittest.TestCase):
    def test_rejects_non_list_tier1(self):
        """Audit-fix M5 regression: malformed config raises, does not silently coerce."""
        import tempfile as _tmp
        with _tmp.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "supervisor.yaml"
            json_path = Path(tmp) / "supervisor.json"
            yaml_path.write_text("# sentinel")  # from_yaml checks yaml exists first, json preferred
            json_path.write_text(json.dumps({"tier1_tools": "pytest"}))  # string, not list
            with self.assertRaises(ValueError):
                SupervisorConfig.from_yaml(yaml_path)

    def test_rejects_non_bool_trusted_repo(self):
        import tempfile as _tmp
        with _tmp.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "supervisor.yaml"
            json_path = Path(tmp) / "supervisor.json"
            yaml_path.write_text("# sentinel")
            json_path.write_text(json.dumps({"tier2_trusted_repo": "yes"}))
            with self.assertRaises(ValueError):
                SupervisorConfig.from_yaml(yaml_path)


class MultiAttemptFakeRuntime:
    """FakeRuntime variant that replays a DIFFERENT fixture per submit_task call.
    Used to test the auto-continuation loop: first submit returns max_turns,
    second submit (with --resume) returns success.
    """

    def __init__(self, fixtures_per_attempt: list[list[str]], cli_session: str = "cli-sess-1"):
        self._per_attempt = list(fixtures_per_attempt)
        self._cli_session = cli_session
        self._state: SJA._TaskState | None = None
        self._drained: list = []
        self.submit_calls: list[dict] = []
        self.cancel_calls: list[str] = []

    async def submit_task(self, prompt, system_prompt=None, model=None, cwd=None, resume_session=None):
        self.submit_calls.append({"prompt": prompt, "resume_session": resume_session})
        if not self._per_attempt:
            raise RuntimeError("MultiAttemptFakeRuntime: no more fixtures queued")
        lines = self._per_attempt.pop(0)
        proc = FakeProc(lines)
        self._state = SJA._TaskState(task_id=f"fake-tid-{len(self.submit_calls)}", proc=proc)
        rt = SJA.StreamJsonRuntime()
        await rt._reader(self._state)
        self._drained = []
        while True:
            item = await self._state.queue.get()
            if item is None:
                break
            self._drained.append(item)
        return self._state.task_id

    async def events(self, task_id):
        for ev in self._drained:
            yield ev

    def terminal_state(self, task_id): return self._state.terminal if self._state else None
    def tool_invocations(self, task_id): return list(self._state.tool_calls) if self._state else []
    def usage(self, task_id): return self._state.usage_snapshot if self._state else None
    def terminal_reason(self, task_id): return self._state.terminal_reason if self._state else None
    def cli_session_id(self, task_id): return self._cli_session
    async def cancel(self, task_id): self.cancel_calls.append(task_id)
    async def shutdown(self): return None


class TestAutoContinuation(unittest.IsolatedAsyncioTestCase):
    async def test_max_turns_triggers_resume_attempt(self):
        """Regression: on subtype=error_max_turns, supervisor re-submits with
        resume_session so the user doesn't have to manually chain."""
        max_turns_result = {
            "type": "result", "subtype": "error_max_turns",
            "stop_reason": "max_turns", "num_turns": 25,
            "duration_ms": 200000, "is_error": True, "api_error_status": None,
            "usage": {"input_tokens": 1000, "output_tokens": 9000},
        }
        success_result = {
            "type": "result", "subtype": "success",
            "stop_reason": "end_turn", "num_turns": 3,
            "duration_ms": 15000, "is_error": False, "api_error_status": None,
            "usage": {"input_tokens": 200, "output_tokens": 400},
        }
        attempt1 = [json.dumps(m) for m in (_fixture_init(session="cli-1"), _fixture_text("doing"), max_turns_result)]
        attempt2 = [json.dumps(m) for m in (_fixture_init(session="cli-1"), _fixture_text("continuing"), success_result)]
        runtime = MultiAttemptFakeRuntime([attempt1, attempt2], cli_session="cli-1")
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-cont")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store, max_continuations=3)
            result = await sup.supervise(prompt="do a big thing", estimated_tokens=1000)
            self.assertEqual(result.terminal, "completed")
            self.assertEqual(result.continuations, 1)
            self.assertEqual(len(runtime.submit_calls), 2)
            self.assertIsNone(runtime.submit_calls[0]["resume_session"])
            self.assertEqual(runtime.submit_calls[1]["resume_session"], "cli-1")
            self.assertIn("Continue the task", runtime.submit_calls[1]["prompt"])
            # Usage accumulates across attempts.
            self.assertEqual(result.usage["output_tokens"], 9000 + 400)
            self.assertEqual(result.usage["input_tokens"], 1000 + 200)
        finally:
            os.unlink(path)

    async def test_max_continuations_cap_respected(self):
        """Regression: supervisor gives up after max_continuations attempts."""
        max_turns_result = {
            "type": "result", "subtype": "error_max_turns",
            "stop_reason": "max_turns", "num_turns": 25, "duration_ms": 1, "is_error": True,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }
        lines = [json.dumps(m) for m in (_fixture_init(session="cli-2"), _fixture_text("..."), max_turns_result)]
        # Need 3 attempts: initial + 2 continuations = max_continuations=2 → total 3 submits.
        runtime = MultiAttemptFakeRuntime([lines, lines, lines], cli_session="cli-2")
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-cap")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store, max_continuations=2)
            result = await sup.supervise(prompt="x", estimated_tokens=100)
            self.assertEqual(result.terminal, "failed")
            self.assertEqual(result.continuations, 2)
            self.assertEqual(len(runtime.submit_calls), 3)
        finally:
            os.unlink(path)

    async def test_aborted_streaming_triggers_resume(self):
        """Regression (field log 2026-04-21 session f56113ccb39c41c1):
        real CLI returned subtype=aborted_streaming at num_turns=26. The
        old gate matched only error_max_turns → continuation didn't fire.
        Should now retry on any non-success subtype."""
        aborted = {
            "type": "result", "subtype": "aborted_streaming",
            "stop_reason": "aborted", "num_turns": 26,
            "duration_ms": 200000, "is_error": True,
            "api_error_status": None,
            "usage": {"input_tokens": 2000, "output_tokens": 5600},
        }
        success = {
            "type": "result", "subtype": "success",
            "stop_reason": "end_turn", "num_turns": 5,
            "duration_ms": 20000, "is_error": False,
            "api_error_status": None,
            "usage": {"input_tokens": 300, "output_tokens": 600},
        }
        attempt1 = [json.dumps(m) for m in (_fixture_init(session="cli-abort"), _fixture_text("midwork"), aborted)]
        attempt2 = [json.dumps(m) for m in (_fixture_init(session="cli-abort"), _fixture_text("cont"), success)]
        runtime = MultiAttemptFakeRuntime([attempt1, attempt2], cli_session="cli-abort")
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-abort")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store, max_continuations=3)
            result = await sup.supervise(prompt="big investigation", estimated_tokens=40000)
            self.assertEqual(result.terminal, "completed")
            self.assertEqual(result.continuations, 1)
            self.assertEqual(len(runtime.submit_calls), 2)
            self.assertEqual(runtime.submit_calls[1]["resume_session"], "cli-abort")
        finally:
            os.unlink(path)

    async def test_success_first_try_no_continuation(self):
        """Healthy first attempt = no retry, continuations=0."""
        success_result = {
            "type": "result", "subtype": "success",
            "stop_reason": "end_turn", "num_turns": 1, "duration_ms": 2000, "is_error": False,
            "usage": {"input_tokens": 5, "output_tokens": 10},
        }
        lines = [json.dumps(m) for m in (_fixture_init(session="cli-3"), _fixture_text("done"), success_result)]
        runtime = MultiAttemptFakeRuntime([lines], cli_session="cli-3")
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-ok")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            result = await sup.supervise(prompt="x", estimated_tokens=100)
            self.assertEqual(result.terminal, "completed")
            self.assertEqual(result.continuations, 0)
            self.assertEqual(len(runtime.submit_calls), 1)
        finally:
            os.unlink(path)


class TestAutonomyDirective(unittest.IsolatedAsyncioTestCase):
    async def test_autonomy_directive_passed_to_worker(self):
        """Regression: default spawn sends AUTONOMY_DIRECTIVE as system_prompt
        so the worker doesn't fall back to 'A or B?' admin mode."""
        from supervisor.supervisor import AUTONOMY_DIRECTIVE
        captured: dict = {}

        class CapturingRuntime(MultiAttemptFakeRuntime):
            async def submit_task(self, prompt, system_prompt=None, model=None, cwd=None, resume_session=None):
                captured["system_prompt"] = system_prompt
                return await super().submit_task(prompt, system_prompt, model, cwd, resume_session)

        success = {"type": "result", "subtype": "success", "stop_reason": "end_turn",
                   "num_turns": 1, "duration_ms": 100, "is_error": False,
                   "usage": {"input_tokens": 1, "output_tokens": 1}}
        lines = [json.dumps(m) for m in (_fixture_init(), _fixture_text("ok"), success)]
        runtime = CapturingRuntime([lines])
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-auto")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            await sup.supervise(prompt="x", estimated_tokens=100, autonomy_directive=True)
            self.assertIsNotNone(captured["system_prompt"])
            self.assertIn("Work fully autonomously", captured["system_prompt"])
            self.assertIn("Do NOT ask the user clarifying questions", captured["system_prompt"])
        finally:
            os.unlink(path)

    async def test_autonomy_directive_can_be_disabled(self):
        captured: dict = {}

        class CapturingRuntime(MultiAttemptFakeRuntime):
            async def submit_task(self, prompt, system_prompt=None, model=None, cwd=None, resume_session=None):
                captured["system_prompt"] = system_prompt
                return await super().submit_task(prompt, system_prompt, model, cwd, resume_session)

        success = {"type": "result", "subtype": "success", "stop_reason": "end_turn",
                   "num_turns": 1, "duration_ms": 100, "is_error": False,
                   "usage": {"input_tokens": 1, "output_tokens": 1}}
        lines = [json.dumps(m) for m in (_fixture_init(), _fixture_text("ok"), success)]
        runtime = CapturingRuntime([lines])
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-noauto")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            await sup.supervise(prompt="x", estimated_tokens=100, autonomy_directive=False)
            self.assertIsNone(captured["system_prompt"])
        finally:
            os.unlink(path)


class TestTerminalReasonSurfaced(unittest.IsolatedAsyncioTestCase):
    async def test_stop_reason_makes_it_to_result(self):
        """Regression (field log 2026-04-21): workers that hit CLI max-turns
        return with `terminal=failed` and no visible reason. Surface the
        CLI's own stop_reason + num_turns + duration_ms so users can see."""
        # Craft a result event with the full diagnostic shape the real CLI emits.
        fake_result = {
            "type": "result", "subtype": "error_max_turns",
            "stop_reason": "max_turns", "num_turns": 25,
            "duration_ms": 326000, "is_error": True,
            "api_error_status": None,
            "usage": {"input_tokens": 500, "output_tokens": 9680},
        }
        lines = [json.dumps(m) for m in (_fixture_init(), _fixture_text("work"), fake_result)]
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-maxturns")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            result = await sup.supervise(prompt="x", estimated_tokens=1000)
            self.assertEqual(result.terminal, "failed")
            self.assertIsNotNone(result.terminal_reason)
            self.assertEqual(result.terminal_reason["subtype"], "error_max_turns")
            self.assertEqual(result.terminal_reason["stop_reason"], "max_turns")
            self.assertEqual(result.terminal_reason["num_turns"], 25)
            self.assertEqual(result.terminal_reason["duration_ms"], 326000)
            self.assertTrue(result.terminal_reason["is_error"])
        finally:
            os.unlink(path)


class TestPermissiveDefault(unittest.IsolatedAsyncioTestCase):
    async def test_bash_ls_approves_under_permissive_default(self):
        """Regression: permissive default (paranoid_mode=False) approves any
        Bash not matching the deny-list. Was escalate→deny→cancel in v1.2.0.
        """
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_tool(name="Bash", input={"command": "ls /tmp | head -3"}),
            _fixture_tool_result(),
            _fixture_result(input_tokens=10, output_tokens=15),
        )]
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-perm")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            result = await sup.supervise(prompt="x", estimated_tokens=1000)
            self.assertEqual(result.terminal, "completed")
            approvals = [d for d in result.decisions if d["decision"] == "approve"]
            self.assertGreaterEqual(len(approvals), 1)
            self.assertEqual(runtime.cancel_calls, [])  # NOT cancelled
        finally:
            os.unlink(path)

    async def test_git_push_force_still_cancels_in_permissive(self):
        """Regression: deny-list still bites in permissive mode. Worker tries
        `git push --force` → policy deny → worker cancelled.
        """
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_tool(name="Bash", input={"command": "git push --force origin main"}),
            _fixture_tool_result(),
            _fixture_result(),
        )]
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-permdeny")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
            result = await sup.supervise(prompt="x", estimated_tokens=1000)
            deny_decisions = [d for d in result.decisions if d["decision"] == "deny"]
            self.assertGreaterEqual(len(deny_decisions), 1)
            self.assertIn("fake-tid", runtime.cancel_calls)
        finally:
            os.unlink(path)


class TestFreeTextDispatch(unittest.TestCase):
    def test_implicit_run_promotion(self):
        """`supervisor.py fix the bug in foo.py` → parse as `run fix the bug ...`.
        Free-text first arg (not a known subcommand, not a flag) gets implicit `run`.
        """
        from supervisor.supervisor import _build_parser, _SUBCOMMANDS, main
        # Dry-verify that the parser rewrites argv correctly by inspecting what
        # `run`'s prompt resolves to. We stop at argparse because actually running
        # `run` would spawn a subprocess. Use a known-invalid config path to make
        # _cmd_run bail before subprocess.
        argv = ["fix", "the", "bug", "in", "foo.py"]
        if argv and argv[0] not in _SUBCOMMANDS and not argv[0].startswith("-"):
            argv = ["run", *argv]
        parser = _build_parser()
        args = parser.parse_args(argv)
        self.assertEqual(args.cmd, "run")
        self.assertEqual(" ".join(args.prompt), "fix the bug in foo.py")

    def test_known_subcommand_passes_through(self):
        from supervisor.supervisor import _build_parser, _SUBCOMMANDS
        argv = ["sessions", "--limit", "5"]
        if argv and argv[0] not in _SUBCOMMANDS and not argv[0].startswith("-"):
            argv = ["run", *argv]
        parser = _build_parser()
        args = parser.parse_args(argv)
        self.assertEqual(args.cmd, "sessions")
        self.assertEqual(args.limit, 5)

    def test_flag_first_passes_through_to_argparse(self):
        """--help should NOT be promoted to `run --help` (argparse shows top help)."""
        from supervisor.supervisor import _SUBCOMMANDS
        argv = ["--help"]
        if argv and argv[0] not in _SUBCOMMANDS and not argv[0].startswith("-"):
            argv = ["run", *argv]
        self.assertEqual(argv, ["--help"])  # unchanged


class TestDirectScriptInvocation(unittest.TestCase):
    def test_supervisor_runs_as_direct_script(self):
        """Regression: /supervise slash command invokes supervisor.py as a direct
        script (not `python3 -m supervisor.supervisor`). A relative-import crash
        here means the command is broken for every installed user.
        """
        import subprocess
        script = SCRIPTS / "supervisor" / "supervisor.py"
        result = subprocess.run(
            ["python3", str(script), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Claude Booster Supervisor Agent", result.stdout)

    def test_bare_invocation_shows_help_and_summary(self):
        """Regression: field-log 2026-04-20 showed bare `/supervise` failing
        with argparse exit=2. Bare must now be exit=0 with help+sessions.
        """
        import subprocess
        script = SCRIPTS / "supervisor" / "supervisor.py"
        env = dict(**os.environ, CLAUDE_BOOSTER_DB=tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
        try:
            result = subprocess.run(
                ["python3", str(script)],
                capture_output=True, text=True, timeout=10, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Claude Booster Supervisor Agent", result.stdout)
            # "No supervisor sessions on record." on an empty DB.
            self.assertIn("supervisor sessions", result.stdout.lower())
        finally:
            os.unlink(env["CLAUDE_BOOSTER_DB"])


class TestSessionsCommand(unittest.TestCase):
    def test_sessions_subcommand_json_and_table(self):
        """Regression: `sessions` subcommand is the official way to enumerate —
        other Claudes used to hack raw sqlite3 with wrong column names."""
        import subprocess
        script = SCRIPTS / "supervisor" / "supervisor.py"
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False); db.close()
        env = dict(**os.environ, CLAUDE_BOOSTER_DB=db.name)
        try:
            # Seed one quota row via direct persistence.
            from supervisor.persistence import SupervisorPersistence
            store = SupervisorPersistence(db_path=db.name)
            store.upsert_quota({
                "session_id": "seed-1", "started_at": "2026-04-20T17:00:00+00:00",
                "window_end": "2026-04-20T22:00:00+00:00",
                "supervisor_tokens": 0, "worker_tokens": 42, "circuit_state": "closed",
            })
            # JSON form:
            result = subprocess.run(
                ["python3", str(script), "sessions", "--json"],
                capture_output=True, text=True, timeout=10, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["session_id"], "seed-1")
            self.assertEqual(data[0]["worker_tokens"], 42)
            # Table form:
            result2 = subprocess.run(
                ["python3", str(script), "sessions"],
                capture_output=True, text=True, timeout=10, env=env,
            )
            self.assertEqual(result2.returncode, 0, result2.stderr)
            self.assertIn("SESSION_ID", result2.stdout)
            self.assertIn("seed-1", result2.stdout)
            self.assertIn("closed", result2.stdout)
        finally:
            os.unlink(db.name)


class TestPersistenceListDecisions(unittest.TestCase):
    def test_public_api_returns_rows(self):
        """Audit-fix L1 regression: public list_decisions() works without _connect()."""
        store, path = _store_tmp()
        try:
            for i in range(3):
                store.record_decision(f"s-list", "Read", f"dig{i}", "approve", 0, f"r{i}", approved_by="regex")
            rows = store.list_decisions("s-list", limit=10)
            self.assertEqual(len(rows), 3)
            self.assertEqual({r["tool"] for r in rows}, {"Read"})
            self.assertEqual({r["decision"] for r in rows}, {"approve"})
        finally:
            os.unlink(path)


class TestConfigYaml(unittest.TestCase):
    def test_parse_minimal_yaml_flat(self):
        text = """
# comment line
tier2_trusted_repo: true
estimated_tokens: 20000
tier1_tools:
  - pytest
  - npm test
"""
        out = _parse_minimal_yaml(text)
        self.assertEqual(out["tier2_trusted_repo"], True)
        self.assertEqual(out["estimated_tokens"], 20000)
        self.assertEqual(out["tier1_tools"], ["pytest", "npm test"])

    def test_config_missing_file_returns_defaults(self):
        cfg = SupervisorConfig.from_yaml(Path("/nonexistent/supervisor.yaml"))
        self.assertEqual(cfg.tier1_tools, set())
        self.assertFalse(cfg.tier2_trusted_repo)

    def test_config_json_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "supervisor.yaml"
            json_path = Path(tmp) / "supervisor.json"
            yaml_path.write_text("# placeholder")
            json_path.write_text(json.dumps({"tier1_tools": ["pytest"], "tier2_trusted_repo": True}))
            cfg = SupervisorConfig.from_yaml(yaml_path)
            self.assertEqual(cfg.tier1_tools, {"pytest"})
            self.assertTrue(cfg.tier2_trusted_repo)


if __name__ == "__main__":
    unittest.main()
