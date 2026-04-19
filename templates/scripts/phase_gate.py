#!/usr/bin/env python3
"""
PreToolUse hook: allow Edit/Write/NotebookEdit only in phase=IMPLEMENT
(or for allowlisted paths — docs/reports/tests/.claude/*.md).

Contract:
  stdin  — PreToolUse JSON (tool_name, tool_input, cwd)
  stderr — feedback on block
  exit   — 0 allow, 2 block

Bypass:
  env CLAUDE_BOOSTER_SKIP_PHASE_GATE=1
  allowlist path match
  phase == IMPLEMENT

Phase is read from <project_root>/.claude/.phase (see phase.py).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ALLOWLIST = [
    r"/docs/", r"/doc/", r"/reports/", r"/audits/", r"/tests/", r"/test/",
    r"/\.claude/", r"\.md$", r"\.txt$", r"README", r"CLAUDE\.md$",
    r"/scratch/", r"/tmp/", r"\.log$",
]
EDIT_PHASE = "IMPLEMENT"
DEFAULT_PHASE = "RECON"


def _project_root(cwd_hint: str) -> Path:
    try:
        cwd = Path(cwd_hint) if cwd_hint else Path.cwd()
    except (FileNotFoundError, OSError):
        return Path.home()
    for p in [cwd, *cwd.parents]:
        if (p / ".git").exists() or (p / ".claude").exists():
            return p
    return cwd


def _read_phase(root: Path) -> str:
    f = root / ".claude" / ".phase"
    if not f.exists():
        return DEFAULT_PHASE
    try:
        v = f.read_text(encoding="utf-8").strip().upper()
        return v or DEFAULT_PHASE
    except OSError:
        return DEFAULT_PHASE


def _allowlisted(path: str) -> bool:
    return any(re.search(p, path) for p in ALLOWLIST)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    tool = payload.get("tool_name", "")
    if tool not in {"Edit", "Write", "NotebookEdit"}:
        return 0

    if os.environ.get("CLAUDE_BOOSTER_SKIP_PHASE_GATE") == "1":
        return 0

    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
    if _allowlisted(path):
        return 0

    cwd = payload.get("cwd", "")
    root = _project_root(cwd)
    phase = _read_phase(root)

    if phase == EDIT_PHASE:
        return 0

    print(
        f"phase_gate: current phase is {phase}; code edits only allowed in {EDIT_PHASE}.\n"
        f"File: {path}\n"
        f"Project root: {root}\n\n"
        "Advance to IMPLEMENT when ready:\n"
        "  python3 ~/.claude/scripts/phase.py set IMPLEMENT\n\n"
        "Phases: RECON -> PLAN -> IMPLEMENT -> AUDIT -> VERIFY -> MERGE\n"
        "  RECON     read-only exploration\n"
        "  PLAN      design + TaskCreate + consilium if needed\n"
        "  IMPLEMENT code edits allowed\n"
        "  AUDIT     review + PAL second opinion\n"
        "  VERIFY    curl/pytest/DevTools evidence\n"
        "  MERGE     push after user acceptance\n\n"
        "Bypass: CLAUDE_BOOSTER_SKIP_PHASE_GATE=1, or docs/reports/tests/.md edits\n",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
