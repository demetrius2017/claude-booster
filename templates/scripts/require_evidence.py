#!/usr/bin/env python3
"""
TaskCompleted hook: require verification evidence in recent transcript before
a task can be marked completed.

Evidence markers (any one satisfies the gate):
  - curl + HTTP status (1xx-5xx)
  - pytest / PASSED / FAILED / "N passed"
  - psql / sqlite3 / SELECT + "N rows"
  - Chrome DevTools: list_console_messages / list_network_requests output
  - docker ps / kubectl get with output
  - HTTP/1.1, HTTP/2 in output
  - exit=N markers
  - SCREENSHOT: / evidence file path mentions

Contract:
  stdin  — TaskCompleted JSON (transcript_path, task info)
  stderr — feedback when blocking
  exit   — 0 allow, 2 block (Claude sees feedback and must verify first)

Bypass:
  env CLAUDE_BOOSTER_SKIP_EVIDENCE_GATE=1
  marker [no-evidence] in recent transcript (e.g. for doc-only tasks)
  task description contains "docs:" / "chore:" / "wip:" prefix

Env/Files:
  ~/.claude/logs/require_evidence.jsonl — decision log
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

EVIDENCE_PATTERNS = [
    r"HTTP/\d",
    r"\bcurl\b.*\b[1-5]\d{2}\b",
    r"\b\d+\s+passed\b",
    r"\bPASSED\b", r"\bFAILED\b",
    r"\b\d+\s+rows?\b",
    r"\bexit=?\s*\d+\b",
    r"list_console_messages",
    r"list_network_requests",
    r"take_screenshot",
    r"pytest",
    r"docker\s+ps",
    r"kubectl\s+get",
    r"psql.*SELECT",
    r"sqlite3.*SELECT",
    r"SCREENSHOT:",
]
BYPASS_MARKER = "[no-evidence]"
DOC_PREFIXES = ("docs:", "chore:", "wip:", "research:", "note:")
TRANSCRIPT_TAIL_LINES = 1500
LOG_PATH = Path.home() / ".claude" / "logs" / "require_evidence.jsonl"


def _log(decision: str, **extra: object) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.utcnow().isoformat() + "Z", "decision": decision, **extra}
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_tail(path: str, n: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return "".join(lines[-n:])
    except (OSError, UnicodeDecodeError):
        return ""


def _has_evidence(tail: str) -> str | None:
    for pat in EVIDENCE_PATTERNS:
        m = re.search(pat, tail, flags=re.IGNORECASE)
        if m:
            return pat
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if os.environ.get("CLAUDE_BOOSTER_SKIP_EVIDENCE_GATE") == "1":
        _log("allow", reason="env-bypass")
        return 0

    task = payload.get("task") or {}
    desc = (task.get("description") or task.get("title") or "").strip().lower()

    if any(desc.startswith(p) for p in DOC_PREFIXES):
        _log("allow", reason="doc-prefix", desc=desc[:80])
        return 0

    transcript_path = payload.get("transcript_path", "")
    tail = _read_tail(transcript_path, TRANSCRIPT_TAIL_LINES) if transcript_path else ""

    if BYPASS_MARKER in tail:
        _log("allow", reason="marker")
        return 0

    pat = _has_evidence(tail)
    if pat:
        _log("allow", reason=f"evidence:{pat}", desc=desc[:80])
        return 0

    _log("deny", desc=desc[:80])
    print(
        "require_evidence gate: task cannot be marked completed without verification evidence.\n"
        f"Task: {desc[:120]}\n\n"
        "Acceptable evidence (at least one required in recent transcript):\n"
        "  - curl with HTTP status code (e.g. `HTTP/1.1 200`)\n"
        "  - pytest output (`5 passed`, `PASSED`, `FAILED`)\n"
        "  - SQL query with rowcount (`3 rows`)\n"
        "  - Chrome DevTools / Claude-in-Chrome console or network inspection\n"
        "  - `docker ps` / `kubectl get` output\n"
        "  - `exit=N` from a run command\n"
        "  - screenshot path (`SCREENSHOT: /path/to/x.png`)\n\n"
        "Bypass for non-code tasks:\n"
        "  - prefix task description with `docs:`, `chore:`, `wip:`, `research:`, or `note:`\n"
        "  - add [no-evidence] marker to your message\n"
        "  - set env CLAUDE_BOOSTER_SKIP_EVIDENCE_GATE=1\n\n"
        "Do not mark the task completed until you have run the real verification.\n",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
