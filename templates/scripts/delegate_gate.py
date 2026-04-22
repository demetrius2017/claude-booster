#!/usr/bin/env python3
"""
PreToolUse hook: enforce "delegate, don't do" via a 1-action budget.

Purpose:
  The Lead (main Claude session) is supposed to orchestrate agents, not do
  the substantive work itself. pipeline.md says so, but soft rules get
  ignored. This hook enforces the same rule at the harness layer: main
  Claude may perform at most 1 "action" tool call per delegation window,
  after which it MUST delegate (Agent / TaskCreate / /supervise) before
  doing another direct action.

Contract:
  stdin  — PreToolUse JSON {tool_name, tool_input, cwd, agent_id, agent_type,
           session_id}
  stderr — feedback on block
  exit   — 0 allow, 2 block

Sub-agent auto-skip:
  Claude Code v2.1.114+ passes ``agent_id`` and ``agent_type`` fields in the
  hook stdin JSON for sub-agent sessions. If ``agent_id`` is a non-empty
  string the gate's purpose (force the Lead to delegate) is already
  satisfied — delegation has happened. We auto-skip (exit 0) and log the
  event for post-hoc surveillance.

State:
  <project_root>/.claude/.delegate_counter — integer count of action calls
  since the last delegation signal. Plain-text, single line.

"Actions" (counted, budget = 1):
  Bash, Edit, Write, NotebookEdit

"Reads" (NOT counted — free, unlimited):
  Read, Grep, Glob, WebSearch, WebFetch, ToolSearch, and any other tool
  not in the actions list.

"Delegation signals" (reset counter to 0, then allow):
  Agent       — main Claude spawned a sub-agent (Explore/Plan/general-purpose)
  TaskCreate  — TaskCreate is the orchestrator's planning primitive
  Bash invoking `python3 ~/.claude/scripts/supervisor/supervisor.py *`
    (or any path ending in /supervisor/supervisor.py) — /supervise worker spawn
  Bash invoking `mcp__pal__*` via shell is impossible — PAL is its own tool
    but since it runs a deep Claude-like analysis, it counts as delegation.

Bypass (LEAD ONLY — sub-agents cannot self-disable):
  env CLAUDE_BOOSTER_SKIP_DELEGATE_GATE=1
  file <project_root>/.claude/.delegate_mode=off (honoured only in Lead
       context; if a sub-agent writes this file to self-bypass, the gate
       refuses and logs the attempt to
       ~/.claude/logs/gate_bypass_attempts.jsonl)
  path allowlist match (reports/ audits/ *.md .claude/ etc.)

  The stderr block message deliberately does NOT mention the bypass file
  path — sub-agents read the same stderr and adopt it as a fix-recipe.
  README keeps the documentation for human Leads.

Decision telemetry:
  Every invocation appends one JSON line to
  ~/.claude/logs/delegate_gate_decisions.jsonl with fields
  {ts, gate, decision, reason, agent_id, agent_type, tool_name, cwd,
  project, session_id, counter, budget}. Fail-soft: log failures are
  swallowed — the gate's primary job is gating, not logging.

Limitations:
  - Per-project state in the repo, so parallel sessions on the same repo
    share the same counter (race-prone but state is idempotent).
  - Agent-spawn subprocesses run in their own tool context — the inner
    Claude's tool calls hit THEIR own hooks, not this one.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

try:
    from _gate_common import (
        BYPASS_LOG_NAME,
        DECISION_ALLOW,
        DECISION_AUTO_SKIP,
        DECISION_BLOCK,
        DECISION_BYPASS_HONOURED,
        DECISION_BYPASS_REFUSED,
        DELEGATE_LOG_NAME,
        append_jsonl,
        is_subagent_context,
        iso_now,
        project_root_from,
    )
except ImportError:
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from _gate_common import (  # type: ignore[no-redef]
        BYPASS_LOG_NAME,
        DECISION_ALLOW,
        DECISION_AUTO_SKIP,
        DECISION_BLOCK,
        DECISION_BYPASS_HONOURED,
        DECISION_BYPASS_REFUSED,
        DELEGATE_LOG_NAME,
        append_jsonl,
        is_subagent_context,
        iso_now,
        project_root_from,
    )

BUDGET = int(os.environ.get("CLAUDE_BOOSTER_DELEGATE_BUDGET", "1"))
STATE_FILE_REL = ".claude/.delegate_counter"
MODE_FILE_REL = ".claude/.delegate_mode"

# Tools that count against the budget when called directly by main Claude.
ACTIONS = {"Bash", "Edit", "Write", "NotebookEdit"}

# Tools that reset the counter (delegation happened).
DELEGATION_TOOLS = {"Agent", "TaskCreate"}

# Supervisor-spawn patterns for Bash — also counts as delegation.
SUPERVISOR_BASH_PATTERNS = [
    re.compile(r"python3?\s+[^\s]*\.claude/scripts/supervisor/supervisor\.py\b"),
    re.compile(r"python3?\s+-m\s+supervisor\.supervisor\b"),
]

ALLOWLIST_PATHS = [
    r"/docs/", r"/doc/", r"/reports/", r"/audits/", r"/tests/", r"/test/",
    r"/\.claude/", r"\.md$", r"\.txt$", r"README", r"CLAUDE\.md$",
    r"/scratch/", r"/tmp/", r"\.log$",
]


def _project_root(cwd_hint: str) -> Path:
    found = project_root_from(cwd_hint)
    if found is not None:
        return found
    # Fallback: honour the hint path even if no marker was found, else $HOME.
    try:
        return Path(cwd_hint) if cwd_hint else Path.cwd()
    except (FileNotFoundError, OSError):
        return Path.home()


def _read_counter(root: Path) -> int:
    path = root / STATE_FILE_REL
    if not path.exists():
        return 0
    try:
        return max(0, int(path.read_text().strip()))
    except (ValueError, OSError):
        return 0


def _write_counter(root: Path, value: int) -> None:
    path = root / STATE_FILE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(f"{value}\n")
    except OSError:
        pass


def _mode_disabled(root: Path) -> bool:
    path = root / MODE_FILE_REL
    if not path.exists():
        return False
    try:
        return path.read_text().strip().lower() == "off"
    except OSError:
        return False


def _path_allowlisted(tool_input: dict) -> bool:
    for key in ("file_path", "path", "notebook_path"):
        v = tool_input.get(key)
        if not v:
            continue
        s = str(v)
        for pat in ALLOWLIST_PATHS:
            if re.search(pat, s):
                return True
    return False


def _bash_is_supervisor_spawn(cmd: str) -> bool:
    return any(p.search(cmd) for p in SUPERVISOR_BASH_PATTERNS)


def _feedback(root: Path, tool: str, counter: int) -> str:
    # Deliberately no reference to the bypass file (.delegate_mode) or any
    # 'echo off' recipe — sub-agents read stderr and will adopt any hint
    # as a fix. Human Leads get the documentation in the README.
    return (
        f"delegate_gate: direct-action budget exhausted "
        f"({counter}/{BUDGET} used on {tool!r}, counter resets on Agent/TaskCreate/supervisor-spawn).\n"
        f"The Lead orchestrates; delegate via Agent(type=Explore|Plan|general-purpose) "
        f"or `/supervise <task>` (→ python3 {root}/.claude/scripts/supervisor/supervisor.py <prompt>)."
    )


def _build_base_record(data: dict, root: Path) -> dict:
    return {
        "ts": iso_now(),
        "gate": "delegate",
        "agent_id": data.get("agent_id") or "",
        "agent_type": data.get("agent_type") or "",
        "tool_name": data.get("tool_name") or "",
        "cwd": data.get("cwd") or "",
        "project": root.name if root else "",
        "session_id": data.get("session_id") or "",
        "budget": BUDGET,
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        raw = ""
    # Fail-closed on malformed/non-dict hook payload. An empty dict would
    # make `tool_name=""` → "not in ACTIONS" → allow, letting corrupted or
    # adversarial stdin silently bypass the gate.
    parse_ok = True
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
        parse_ok = False
    if not isinstance(data, dict):
        data = {}
        parse_ok = False
    if not parse_ok:
        partial = {
            "ts": iso_now(),
            "gate": "delegate",
            "decision": DECISION_BLOCK,
            "reason": "invalid hook payload (malformed or non-dict stdin)",
        }
        append_jsonl(DELEGATE_LOG_NAME, partial)
        sys.stderr.write("delegate_gate: malformed hook payload, blocking fail-closed\n")
        return 2

    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or ""
    is_subagent = is_subagent_context(data)

    root = _project_root(cwd)
    base = _build_base_record(data, root)
    base["counter"] = _read_counter(root)

    # Sub-agent: delegation has already happened, the gate's job is done.
    # But if the sub-agent ALSO wrote .delegate_mode=off to self-bypass,
    # log the refused attempt for surveillance before returning.
    if is_subagent:
        attempted_bypass = _mode_disabled(root)
        if attempted_bypass:
            append_jsonl(BYPASS_LOG_NAME, {
                **base,
                "decision": DECISION_BYPASS_REFUSED,
                "reason": "sub-agent cannot disable gate",
                "bypass_file": str(root / MODE_FILE_REL),
            })
        auto_skip_rec = {
            **base,
            "decision": DECISION_AUTO_SKIP,
            "reason": "sub-agent context (agent_id/agent_type set)",
        }
        if attempted_bypass:
            auto_skip_rec["attempted_bypass"] = True
        append_jsonl(DELEGATE_LOG_NAME, auto_skip_rec)
        return 0

    if os.environ.get("CLAUDE_BOOSTER_SKIP_DELEGATE_GATE") == "1":
        append_jsonl(DELEGATE_LOG_NAME, {
            **base,
            "decision": DECISION_ALLOW,
            "reason": "env CLAUDE_BOOSTER_SKIP_DELEGATE_GATE=1",
        })
        return 0

    if _mode_disabled(root):
        append_jsonl(BYPASS_LOG_NAME, {
            **base,
            "decision": DECISION_BYPASS_HONOURED,
            "reason": "lead context honoured .delegate_mode=off",
            "bypass_file": str(root / MODE_FILE_REL),
        })
        append_jsonl(DELEGATE_LOG_NAME, {
            **base,
            "decision": DECISION_BYPASS_HONOURED,
            "reason": ".delegate_mode=off (lead)",
            "attempted_bypass": True,
        })
        return 0

    if tool in DELEGATION_TOOLS:
        _write_counter(root, 0)
        append_jsonl(DELEGATE_LOG_NAME, {
            **base,
            "decision": DECISION_ALLOW,
            "reason": f"delegation signal {tool!r} resets counter",
        })
        return 0
    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        if _bash_is_supervisor_spawn(cmd):
            _write_counter(root, 0)
            append_jsonl(DELEGATE_LOG_NAME, {
                **base,
                "decision": DECISION_ALLOW,
                "reason": "supervisor spawn resets counter",
            })
            return 0

    if tool not in ACTIONS:
        append_jsonl(DELEGATE_LOG_NAME, {
            **base,
            "decision": DECISION_ALLOW,
            "reason": f"tool {tool!r} not in ACTIONS (free)",
        })
        return 0

    if _path_allowlisted(tool_input):
        append_jsonl(DELEGATE_LOG_NAME, {
            **base,
            "decision": DECISION_ALLOW,
            "reason": "path allowlist match",
        })
        return 0

    counter = _read_counter(root)
    new_counter = counter + 1
    if new_counter > BUDGET:
        sys.stderr.write(_feedback(root, tool, new_counter) + "\n")
        append_jsonl(DELEGATE_LOG_NAME, {
            **base,
            "decision": DECISION_BLOCK,
            "reason": f"budget exhausted ({new_counter}/{BUDGET})",
            "counter": new_counter,
        })
        return 2

    _write_counter(root, new_counter)
    append_jsonl(DELEGATE_LOG_NAME, {
        **base,
        "decision": DECISION_ALLOW,
        "reason": f"within budget ({new_counter}/{BUDGET})",
        "counter": new_counter,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
