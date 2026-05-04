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
  - recent transcript contains literal marker "[no-impact-review]" (skips content check only)

Gate (two-stage):
  1. Presence check: at least one TaskCreate must appear in the last
     TRANSCRIPT_TAIL_LINES lines of the transcript.
  2. Content check: the most recent TaskCreate's description must contain
     at least one impact-analysis field (affected:, dependencies:, impact:,
     dependents:) — unless the task title/description starts with a
     docs/chore/ci/style/refactor/test prefix, or [no-impact-review] is
     present in the transcript.
     Fail-open: if the description cannot be parsed, the gate allows through
     rather than blocking existing valid sessions.

Env/Files:
  CLAUDE_BOOSTER_SKIP_TASK_GATE — escape hatch (bypasses both stages)
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
IMPACT_BYPASS_MARKER = "[no-impact-review]"
TRANSCRIPT_TAIL_LINES = 1200
LOG_PATH = Path.home() / ".claude" / "logs" / "require_task.jsonl"

IMPACT_FIELD_RE = re.compile(
    r"(?i)\b(?:affected|dependencies|dependents|impact)\s*:", re.MULTILINE
)
DOCS_CHORE_PREFIX_RE = re.compile(
    r"(?i)^(?:docs?|chore|ci|style|refactor|test)\s*[:/]"
)


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


def _check_task_content(tail: str) -> tuple[bool, str]:
    """Check that the most recent TaskCreate description contains an impact field.

    Returns (True, "") if the check passes or is not applicable.
    Returns (False, reason) if the description is present but lacks an impact field.
    Fails-open (returns True) whenever the description cannot be extracted.
    """
    # Find all TaskCreate tool_use blocks by scanning JSONL lines.
    last_description: str | None = None
    last_title: str | None = None

    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        # Messages may be top-level objects or nested under a "content" key
        # that holds a list of content blocks.  Walk both shapes.
        content_blocks: list = []
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                content_blocks = content
            elif isinstance(msg.get("name"), str):
                # The line itself may already be a tool_use block
                content_blocks = [msg]

        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "TaskCreate":
                inp = block.get("input") or {}
                if isinstance(inp, dict):
                    last_description = inp.get("description") or inp.get("body") or ""
                    last_title = inp.get("title") or inp.get("subject") or inp.get("name") or ""

    # Fail-open: if we found no description to inspect, let the gate pass.
    if last_description is None:
        return True, ""

    combined = f"{last_title or ''} {last_description or ''}".strip()

    # Docs/chore-prefixed tasks are exempt from the impact-field requirement.
    if DOCS_CHORE_PREFIX_RE.search(combined):
        return True, ""

    if IMPACT_FIELD_RE.search(combined):
        return True, ""

    return (
        False,
        "TaskCreate description lacks an impact-analysis field.\n"
        "Add at least one of: affected:, dependencies:, impact:, dependents:\n"
        "Example:\n"
        "  impact: modifies require_task gate logic — affects all Edit/Write hooks\n"
        "  affected: ~/.claude/hooks, Claude_Booster sessions\n"
        "  dependencies: transcript_path must be present in PreToolUse payload\n\n"
        "Bypass (sparingly): add [no-impact-review] to your assistant message.",
    )


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
        # Stage 2: content check — verify the most recent TaskCreate includes
        # an impact-analysis field, unless the impact bypass marker is present.
        if IMPACT_BYPASS_MARKER not in tail:
            ok, reason = _check_task_content(tail)
            if not ok:
                _log("deny", reason="missing-impact-field", tool=tool_name, file=file_path)
                print(reason, file=sys.stderr)
                return 2
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
