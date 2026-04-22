#!/usr/bin/env python3
"""Tests for gate_stats.py reader.

Populates a tempdir with known JSONL rows and asserts the stdout
summary contains the expected counts. Covers the time-window filter
by mixing a 30-day-old row with recent rows and running ``--since 7d``.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
GATE_STATS = REPO_ROOT / "templates" / "scripts" / "gate_stats.py"


def _iso(dt: _dt.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_jsonl(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _run_stats(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE_STATS), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


class GateStatsTests(unittest.TestCase):
    def _populate(self, logdir: pathlib.Path) -> None:
        now = _dt.datetime.utcnow()
        recent = _iso(now - _dt.timedelta(minutes=5))

        delegate_rows = []
        for _ in range(5):
            delegate_rows.append({"ts": recent, "gate": "delegate", "decision": "allow",
                                  "reason": "x", "cwd": "/repo/a", "tool_name": "Bash"})
        for _ in range(3):
            delegate_rows.append({"ts": recent, "gate": "delegate", "decision": "block",
                                  "reason": "budget", "cwd": "/repo/a", "tool_name": "Bash"})
        for _ in range(2):
            delegate_rows.append({"ts": recent, "gate": "delegate", "decision": "auto_skip",
                                  "reason": "sub-agent", "cwd": "/repo/b", "tool_name": "Bash",
                                  "agent_id": "sub"})

        ask_rows = []
        for _ in range(10):
            ask_rows.append({"ts": recent, "gate": "ask", "decision": "allow",
                             "reason": "no match", "cwd": "/repo/x",
                             "matched_pattern": "", "message_excerpt": "ok"})
        for _ in range(4):
            ask_rows.append({"ts": recent, "gate": "ask", "decision": "block",
                             "reason": "forbidden", "cwd": "/repo/x",
                             "matched_pattern": "Apply patch?", "message_excerpt": "Apply patch?"})

        _write_jsonl(logdir / "delegate_gate_decisions.jsonl", delegate_rows)
        _write_jsonl(logdir / "ask_gate_decisions.jsonl", ask_rows)
        _write_jsonl(logdir / "gate_bypass_attempts.jsonl", [
            {"ts": recent, "gate": "delegate", "decision": "bypass_honoured"},
            {"ts": recent, "gate": "delegate", "decision": "bypass_refused"},
            {"ts": recent, "gate": "ask", "decision": "bypass_refused"},
        ])

    def test_stats_counts(self):
        with tempfile.TemporaryDirectory() as td:
            logdir = pathlib.Path(td)
            self._populate(logdir)
            res = _run_stats(["--logdir", str(logdir), "--since", "1d", "--gate", "all"])
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            out = res.stdout

            # Delegate section assertions
            self.assertIn("=== delegate_gate — last 1d ===", out)
            self.assertIn("total invocations: 10", out)
            self.assertIn("allow:          5", out)
            self.assertIn("block:          3", out)
            self.assertIn("auto_skip:      2", out)
            self.assertIn("honoured=1", out)
            self.assertIn("refused=1", out)

            # Ask section assertions
            self.assertIn("=== ask_gate — last 1d ===", out)
            self.assertIn("total invocations: 14", out)
            self.assertIn("allow:          10", out)
            self.assertIn("block:          4", out)
            self.assertIn("Apply patch?", out)  # top matched patterns

    def test_stats_time_filter_excludes_old_rows(self):
        with tempfile.TemporaryDirectory() as td:
            logdir = pathlib.Path(td)
            now = _dt.datetime.utcnow()
            recent = _iso(now - _dt.timedelta(minutes=5))
            old = _iso(now - _dt.timedelta(days=30))
            rows = [
                {"ts": recent, "gate": "delegate", "decision": "allow", "cwd": "/r"},
                {"ts": recent, "gate": "delegate", "decision": "allow", "cwd": "/r"},
                {"ts": old, "gate": "delegate", "decision": "block", "cwd": "/r"},
                {"ts": old, "gate": "delegate", "decision": "block", "cwd": "/r"},
                {"ts": old, "gate": "delegate", "decision": "block", "cwd": "/r"},
            ]
            _write_jsonl(logdir / "delegate_gate_decisions.jsonl", rows)
            # Empty ask log
            _write_jsonl(logdir / "ask_gate_decisions.jsonl", [])

            res = _run_stats(["--logdir", str(logdir), "--since", "7d", "--gate", "delegate"])
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn("total invocations: 2", res.stdout)
            self.assertIn("allow:          2", res.stdout)
            self.assertIn("block:          0", res.stdout)

    def test_subagent_bypass_attempts_line(self):
        with tempfile.TemporaryDirectory() as td:
            logdir = pathlib.Path(td)
            now = _dt.datetime.utcnow()
            recent = _iso(now - _dt.timedelta(minutes=5))
            delegate_rows = [
                # Two auto_skip rows flagged as attempted_bypass.
                {"ts": recent, "gate": "delegate", "decision": "auto_skip",
                 "attempted_bypass": True, "cwd": "/repo/a", "agent_id": "s1"},
                {"ts": recent, "gate": "delegate", "decision": "auto_skip",
                 "attempted_bypass": True, "cwd": "/repo/a", "agent_id": "s2"},
                # One clean auto_skip (no bypass).
                {"ts": recent, "gate": "delegate", "decision": "auto_skip",
                 "cwd": "/repo/a", "agent_id": "s3"},
            ]
            ask_rows = [
                {"ts": recent, "gate": "ask", "decision": "auto_skip",
                 "attempted_bypass": True, "cwd": "/repo/x", "agent_id": "s4"},
            ]
            _write_jsonl(logdir / "delegate_gate_decisions.jsonl", delegate_rows)
            _write_jsonl(logdir / "ask_gate_decisions.jsonl", ask_rows)
            _write_jsonl(logdir / "gate_bypass_attempts.jsonl", [])
            res = _run_stats(["--logdir", str(logdir), "--since", "1d", "--gate", "all"])
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn("sub-agent bypass attempts: 2", res.stdout)  # delegate
            self.assertIn("sub-agent bypass attempts: 1", res.stdout)  # ask

    def test_empty_logdir(self):
        with tempfile.TemporaryDirectory() as td:
            res = _run_stats(["--logdir", td, "--since", "1h", "--gate", "all"])
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn("total invocations: 0", res.stdout)


if __name__ == "__main__":
    unittest.main()
