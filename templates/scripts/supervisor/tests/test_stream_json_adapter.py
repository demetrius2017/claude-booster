"""Unit tests for stream_json_adapter.py.

Run:
    python3 -m unittest discover -s templates/scripts/supervisor/tests -p "test_*.py" -v
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from supervisor import stream_json_adapter as SJA  # noqa: E402
from supervisor.runtime import WorkerEvent  # noqa: E402


def _fixture_init(session="s-1", model="claude-opus-4-7", version=1):
    return {"type": "system", "subtype": "init", "session_id": session, "model": model, "stream_json_version": version}


def _fixture_text(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _fixture_tool(name="Bash", input=None, tid="t-1"):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": input or {"command": "ls"}}]}}


def _fixture_tool_result(tid="t-1", is_error=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "is_error": is_error}]}}


def _fixture_result(subtype="success", input_tokens=10, output_tokens=5):
    return {"type": "result", "subtype": subtype, "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}


class FakeProc:
    """StreamReader-backed stand-in for asyncio.subprocess.Process."""
    def __init__(self, lines, returncode=0):
        reader = asyncio.StreamReader()
        for line in lines:
            reader.feed_data((line + "\n").encode("utf-8"))
        reader.feed_eof()
        self.stdout = reader
        self.returncode = returncode

    async def wait(self):
        return self.returncode


async def _drain(state):
    events = []
    while True:
        item = await state.queue.get()
        if item is None:
            break
        events.append(item)
    return events


class TestSchemaAssertion(unittest.TestCase):
    def test_accepts_current_version(self):
        SJA.StreamJsonRuntime._assert_schema(_fixture_init(version=1))

    def test_rejects_wrong_first_event(self):
        with self.assertRaises(SJA.SchemaMismatchError):
            SJA.StreamJsonRuntime._assert_schema(_fixture_text("hi"))

    def test_rejects_version_mismatch(self):
        with self.assertRaises(SJA.SchemaMismatchError):
            SJA.StreamJsonRuntime._assert_schema(_fixture_init(version=999))


class TestEventMapping(unittest.TestCase):
    def setUp(self):
        self.state = SJA._TaskState(task_id="tid")

    def test_system_init_maps_to_message_start(self):
        evs = SJA.StreamJsonRuntime._to_events(self.state, _fixture_init(session="sess-42"))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, "message_start")
        self.assertEqual(evs[0].payload["session"], "sess-42")

    def test_assistant_text_maps_to_text_delta(self):
        evs = SJA.StreamJsonRuntime._to_events(self.state, _fixture_text("hello world"))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, "text_delta")
        self.assertEqual(evs[0].payload["text"], "hello world")

    def test_assistant_tool_use_appends_to_invocations(self):
        evs = SJA.StreamJsonRuntime._to_events(self.state, _fixture_tool(name="Read", input={"file_path": "/x"}))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, "tool_use_start")
        self.assertEqual(evs[0].payload["name"], "Read")
        self.assertEqual(len(self.state.tool_calls), 1)

    def test_user_tool_result_maps_to_tool_use_stop(self):
        evs = SJA.StreamJsonRuntime._to_events(self.state, _fixture_tool_result(tid="t-9", is_error=True))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, "tool_use_stop")
        self.assertTrue(evs[0].payload["is_error"])

    def test_result_maps_to_message_stop_and_snapshots_usage(self):
        evs = SJA.StreamJsonRuntime._to_events(self.state, _fixture_result(input_tokens=123, output_tokens=45))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].kind, "message_stop")
        self.assertEqual(self.state.usage_snapshot, {"input_tokens": 123, "output_tokens": 45})
        self.assertEqual(self.state.terminal, "completed")

    def test_result_failure_marks_terminal_failed(self):
        SJA.StreamJsonRuntime._to_events(self.state, _fixture_result(subtype="error_max_turns"))
        self.assertEqual(self.state.terminal, "failed")

    def test_multi_block_assistant_message_emits_all_events(self):
        """Regression (audit H1): text+tool_use in one assistant message
        must both surface — silent drop of the tool_use lets policy bypass."""
        bundled = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I will run it."},
            {"type": "tool_use", "id": "tu-1", "name": "Bash", "input": {"command": "ls"}},
        ]}}
        evs = SJA.StreamJsonRuntime._to_events(self.state, bundled)
        kinds = [e.kind for e in evs]
        self.assertEqual(kinds, ["text_delta", "tool_use_start"])
        self.assertEqual(len(self.state.tool_calls), 1)
        self.assertEqual(self.state.tool_calls[0]["name"], "Bash")

    def test_thinking_plus_text_in_one_message(self):
        bundled = {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "hmm"},
            {"type": "text", "text": "answer"},
        ]}}
        evs = SJA.StreamJsonRuntime._to_events(self.state, bundled)
        self.assertEqual([e.kind for e in evs], ["thinking_start", "text_delta"])


class TestReaderReplay(unittest.IsolatedAsyncioTestCase):
    async def test_full_happy_path_replay(self):
        lines = [json.dumps(m) for m in (
            _fixture_init(),
            _fixture_text("starting"),
            _fixture_tool(name="Read", tid="t-1"),
            _fixture_tool_result(tid="t-1"),
            _fixture_text("done"),
            _fixture_result(),
        )]
        runtime = SJA.StreamJsonRuntime()
        state = SJA._TaskState(task_id="tid-happy", proc=FakeProc(lines))
        await runtime._reader(state)
        events = await _drain(state)
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, ["message_start", "text_delta", "tool_use_start", "tool_use_stop", "text_delta", "message_stop"])
        self.assertEqual(state.terminal, "completed")
        self.assertEqual(state.usage_snapshot, {"input_tokens": 10, "output_tokens": 5})

    async def test_reader_raises_on_schema_mismatch(self):
        lines = [json.dumps(_fixture_init(version=42)), json.dumps(_fixture_text("x"))]
        runtime = SJA.StreamJsonRuntime()
        state = SJA._TaskState(task_id="tid-bad", proc=FakeProc(lines))
        with self.assertRaises(SJA.SchemaMismatchError):
            await runtime._reader(state)
        self.assertEqual(state.terminal, "failed")

    async def test_truncated_stream_still_closes_queue(self):
        lines = [json.dumps(_fixture_init()), json.dumps(_fixture_text("mid"))]
        runtime = SJA.StreamJsonRuntime()
        state = SJA._TaskState(task_id="tid-trunc", proc=FakeProc(lines, returncode=1))
        await runtime._reader(state)
        events = await _drain(state)
        self.assertEqual(len(events), 2)
        self.assertEqual(state.terminal, "failed")

    async def test_non_json_first_line_fails_handshake(self):
        """Regression (audit H2): garbage before system/init must not slip
        past the schema check — previous impl silently skipped it."""
        lines = ["not json at all", json.dumps(_fixture_init()), json.dumps(_fixture_text("x"))]
        runtime = SJA.StreamJsonRuntime()
        state = SJA._TaskState(task_id="tid-garbage", proc=FakeProc(lines))
        with self.assertRaises(SJA.SchemaMismatchError):
            await runtime._reader(state)
        self.assertEqual(state.terminal, "failed")

    async def test_hook_preamble_before_init_is_tolerated(self):
        """Regression: real `claude -p --output-format stream-json` emits
        system/hook_started + system/hook_response events BEFORE system/init.
        The handshake must skip them and validate on the first system/init.
        """
        preamble_hook_started = {"type": "system", "subtype": "hook_started", "hook_id": "h1"}
        preamble_hook_response = {"type": "system", "subtype": "hook_response", "hook_id": "h1", "exit_code": 0}
        lines = [
            json.dumps(preamble_hook_started),
            json.dumps(preamble_hook_response),
            json.dumps(_fixture_init()),
            json.dumps(_fixture_text("hi")),
            json.dumps(_fixture_result()),
        ]
        runtime = SJA.StreamJsonRuntime()
        state = SJA._TaskState(task_id="tid-hook", proc=FakeProc(lines))
        await runtime._reader(state)
        events = await _drain(state)
        # Hook events are not in WorkerEvent surface (no mapping), first event
        # we see is message_start from system/init.
        self.assertEqual([e.kind for e in events], ["message_start", "text_delta", "message_stop"])
        self.assertEqual(state.terminal, "completed")

    async def test_multi_block_message_reader_replay(self):
        """Regression (audit H1 end-to-end): reader emits every event."""
        bundled = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "plan"},
            {"type": "tool_use", "id": "tu-9", "name": "Read", "input": {"file_path": "/x"}},
        ]}}
        lines = [json.dumps(_fixture_init()), json.dumps(bundled), json.dumps(_fixture_result())]
        runtime = SJA.StreamJsonRuntime()
        state = SJA._TaskState(task_id="tid-multi", proc=FakeProc(lines))
        await runtime._reader(state)
        events = await _drain(state)
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, ["message_start", "text_delta", "tool_use_start", "message_stop"])
        self.assertEqual(len(state.tool_calls), 1)


if __name__ == "__main__":
    unittest.main()
