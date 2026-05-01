#!/usr/bin/env python3
"""
PreCompact hook: block auto-compaction during PLAN phase so architectural
discussion isn't summarized away.

Contract:
  stdin  — PreCompact JSON (cwd, trigger)
  stderr — feedback when blocking
  exit   — 0 allow, 2 block

Bypass:
  env CLAUDE_BOOSTER_SKIP_COMPACT_GATE=1
  trigger == "manual" (user explicitly ran /compact)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _project_root(cwd_hint: str) -> Path:
    try:
        cwd = Path(cwd_hint) if cwd_hint else Path.cwd()
    except (FileNotFoundError, OSError):
        return Path.home()
    for p in [cwd, *cwd.parents]:
        if (p / ".git").exists() or (p / ".claude").exists():
            return p
    return cwd


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    if os.environ.get("CLAUDE_BOOSTER_SKIP_COMPACT_GATE") == "1":
        return 0

    trigger = (payload.get("trigger") or payload.get("source") or "").lower()
    if trigger == "manual":
        return 0

    cwd = payload.get("cwd", "")
    root = _project_root(cwd)
    f = root / ".claude" / ".phase"
    if not f.exists():
        return 0

    try:
        phase = f.read_text(encoding="utf-8").strip().upper()
    except OSError:
        return 0

    if phase != "PLAN":
        return 0

    print(
        "preserve_plan_context: auto-compaction blocked while phase=PLAN.\n"
        "Planning context (architectural discussion, trade-offs, consilium output) "
        "should not be summarized mid-design.\n\n"
        "Options:\n"
        "  - advance phase (`python3 ~/.claude/scripts/phase.py set IMPLEMENT`) then compaction will proceed next trigger\n"
        "  - run `/compact` manually with explicit preservation instructions\n"
        "  - set env CLAUDE_BOOSTER_SKIP_COMPACT_GATE=1 for this session\n",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
