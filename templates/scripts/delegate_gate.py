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
  stdin  — PreToolUse JSON {tool_name, tool_input, cwd}
  stderr — feedback on block
  exit   — 0 allow, 2 block

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

Bypass:
  env CLAUDE_BOOSTER_SKIP_DELEGATE_GATE=1
  file <project_root>/.claude/.delegate_mode=off
  path allowlist match (reports/ audits/ *.md .claude/ etc.)

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
    try:
        cwd = Path(cwd_hint) if cwd_hint else Path.cwd()
    except (FileNotFoundError, OSError):
        return Path.home()
    for p in [cwd, *cwd.parents]:
        if (p / ".git").exists() or (p / ".claude").is_dir():
            return p
    return cwd


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
    return (
        f"delegate_gate: direct-action budget exhausted "
        f"({counter}/{BUDGET} used on {tool!r}, counter resets on Agent/TaskCreate/supervisor-spawn).\n"
        f"The Lead orchestrates; delegate via Agent(type=Explore|Plan|general-purpose) "
        f"or `/supervise <task>` (→ python3 {root}/.claude/scripts/supervisor/supervisor.py <prompt>).\n"
        f"Bypass once:  CLAUDE_BOOSTER_SKIP_DELEGATE_GATE=1 <your command>\n"
        f"Disable per-repo: echo off > {root}/.claude/.delegate_mode"
    )


def main() -> int:
    if os.environ.get("CLAUDE_BOOSTER_SKIP_DELEGATE_GATE") == "1":
        return 0
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or ""

    root = _project_root(cwd)
    if _mode_disabled(root):
        return 0

    # Delegation signal → reset counter, allow.
    if tool in DELEGATION_TOOLS:
        _write_counter(root, 0)
        return 0
    if tool == "Bash":
        cmd = tool_input.get("command") or ""
        if _bash_is_supervisor_spawn(cmd):
            _write_counter(root, 0)
            return 0

    # Non-action tool → free.
    if tool not in ACTIONS:
        return 0

    # Meta-file path (docs/reports/.claude/*.md) → free.
    if _path_allowlisted(tool_input):
        return 0

    counter = _read_counter(root)
    new_counter = counter + 1
    if new_counter > BUDGET:
        sys.stderr.write(_feedback(root, tool, new_counter) + "\n")
        return 2
    _write_counter(root, new_counter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
