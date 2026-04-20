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

    async def submit_task(self, prompt, system_prompt=None, model=None, cwd=None):
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

    async def cancel(self, task_id):
        self.cancel_calls.append(task_id)
        if self._state is not None and self._state.terminal is None:
            self._state.terminal = "cancelled"

    async def shutdown(self): return None


def _ctx(project_dir: Path, tier1: set[str] | None = None) -> PolicyContext:
    return PolicyContext(project_dir=project_dir, tier1_enabled=tier1 or set(), tier2_trusted_repo=False, session_sandbox=project_dir / "sandbox")


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
        # Audit-fix C1 regression: unknown tool → policy.escalate → no escalator → default-deny + cancel.
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_tool(name="NotebookEdit", input={"path": "x.ipynb"}),
            _fixture_tool_result(),
            _fixture_result(),
        )]
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-noesc")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store)
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
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_tool(name="NotebookEdit", input={"path": "x.ipynb"}),
            _fixture_tool_result(),
            _fixture_result(),
        )]
        escalator = FakeEscalator(decision="deny")
        runtime = FakeRuntime(lines)
        store, path = _store_tmp()
        try:
            tracker = QuotaTracker(session_id="sess-esc")
            sup = Supervisor(runtime=runtime, ctx=_ctx(Path.cwd()), tracker=tracker, store=store, escalator=escalator)
            result = await sup.supervise(prompt="x", estimated_tokens=1000)
            self.assertEqual(len(escalator.calls), 1)
            self.assertEqual(escalator.calls[0][0], "NotebookEdit")
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
