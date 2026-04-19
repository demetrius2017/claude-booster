#!/usr/bin/env python3
"""
PreToolUse hook: require a TaskCreate call in the recent transcript before
allowing Edit/Write/NotebookEdit. Enforces the plan-before-implement gate.

Contract:
  stdin  — PreToolUse JSON: tool_name, tool_input, transcript_path
  stdout — (nothing on success; reason on block)
  stderr — feedback to Claude when blocking
  exit   — 0 allow, 2 block

Bypass:
  - file_path matches allowlist (docs/, reports/, audits/, tests/, .claude/, *.md, *.txt, CLAUDE.md)
  - env CLAUDE_BOOSTER_SKIP_TASK_GATE=1
  - recent transcript contains literal marker "[no-task]"

Env/Files:
  CLAUDE_BOOSTER_SKIP_TASK_GATE — escape hatch
  ~/.claude/logs/require_task.jsonl — decisions log (append-only)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ALLOWLIST_PATTERNS = [
    r"/docs/", r"/doc/", r"/reports/", r"/audits/", r"/tests/", r"/test/",
    r"/\.claude/", r"\.md$", r"\.txt$", r"README", r"CLAUDE\.md$",
    r"/scratch/", r"/tmp/", r"\.log$",
]
BYPASS_MARKER = "[no-task]"
TRANSCRIPT_TAIL_LINES = 1200
LOG_PATH = Path.home() / ".claude" / "logs" / "require_task.jsonl"


def _log(decision: str, **extra: object) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.utcnow().isoformat() + "Z", "decision": decision, **extra}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _is_allowlisted(file_path: str) -> str | None:
    for pat in ALLOWLIST_PATTERNS:
        if re.search(pat, file_path):
            return pat
    return None


def _read_tail(path: str, n: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return "".join(lines[-n:])
    except (OSError, UnicodeDecodeError):
        return ""


def _has_task_create(tail: str) -> bool:
    return bool(re.search(r'"name"\s*:\s*"TaskCreate"', tail))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in {"Edit", "Write", "NotebookEdit"}:
        return 0

    tool_input = payload.get("tool_input") or {}
    file_path = (
        tool_input.get("file_path")
        or tool_input.get("notebook_path")
        or tool_input.get("path")
        or ""
    )
    transcript_path = payload.get("transcript_path", "")

    if os.environ.get("CLAUDE_BOOSTER_SKIP_TASK_GATE") == "1":
        _log("allow", reason="env-bypass", tool=tool_name, file=file_path)
        return 0

    pat = _is_allowlisted(file_path)
    if pat:
        _log("allow", reason=f"allowlist:{pat}", tool=tool_name, file=file_path)
        return 0

    tail = _read_tail(transcript_path, TRANSCRIPT_TAIL_LINES) if transcript_path else ""

    if BYPASS_MARKER in tail:
        _log("allow", reason="marker", tool=tool_name, file=file_path)
        return 0

    if _has_task_create(tail):
        _log("allow", reason="task-found", tool=tool_name, file=file_path)
        return 0

    _log("deny", tool=tool_name, file=file_path)
    print(
        "require_task gate: no TaskCreate found in this session's transcript.\n"
        f"File: {file_path}\n"
        "Before editing source code, call TaskCreate to describe:\n"
        "  - what you are about to change\n"
        "  - why (intent / user goal)\n"
        "  - expected verification (how we'll know it worked)\n"
        "Then proceed with the Edit/Write.\n\n"
        "Bypass options (use sparingly, only for trivial changes):\n"
        "  - add [no-task] to your assistant message (marker)\n"
        "  - edit files under docs/, reports/, tests/, .claude/, or *.md/*.txt\n"
        "  - set env CLAUDE_BOOSTER_SKIP_TASK_GATE=1\n",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
