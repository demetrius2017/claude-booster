#!/usr/bin/env python3
"""UserPromptSubmit hook — inject /compact advisory when one-shot marker exists.

Purpose:
    When compact_advisor.py (PostToolUse hook) has detected that context is large,
    it writes a marker file ~/.claude/.compact_recommended_<session_id>.  This hook
    checks for that marker on every user prompt; if found, it injects a one-line
    reminder into Claude's context via additionalContext, then deletes the marker
    so the reminder fires exactly once per session crossing the threshold.

Contract:
    stdin  — UserPromptSubmit JSON: {session_id, prompt, cwd, ...}
    stdout — JSON {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                    "additionalContext": "<reminder text>"}}  when marker exists,
             otherwise silent (no stdout)
    exit   — 0 always (never blocks the prompt)

Bypass:
    CLAUDE_BOOSTER_SKIP_COMPACT_ADVISOR=1  → exit 0 immediately, no output

Files:
    ~/.claude/.compact_recommended_<session_id>  — one-shot marker (read + deleted here)
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


_SKIP = os.environ.get("CLAUDE_BOOSTER_SKIP_COMPACT_ADVISOR", "")

_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def main() -> int:
    if _SKIP:
        return 0

    # Parse stdin — malformed JSON is a silent no-op
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except Exception:
        return 0

    if not isinstance(data, dict):
        return 0

    session_id = data.get("session_id", "")
    if not session_id:
        return 0

    # Defense-in-depth: session_id must be a valid UUID to be used in a filesystem path
    if not _SESSION_ID_RE.match(session_id):
        return 0

    marker = Path.home() / ".claude" / f".compact_recommended_{session_id}"

    if not marker.exists():
        return 0

    # Read token estimate from marker
    try:
        token_count_str = marker.read_text(encoding="utf-8").strip()
        token_count = int(token_count_str)
    except Exception:
        token_count = 120000  # fallback if unreadable

    # Delete marker (one-shot: will not repeat after this)
    try:
        marker.unlink()
    except Exception:
        pass  # best-effort; even if delete fails, we still inject once

    advisory = (
        f"⚠ Auto-advisory: context ≈ {token_count:,} tokens (>120k). "
        "Run /compact before the next non-trivial task to keep cache costs down. "
        "(one-shot reminder; will not repeat)"
    )

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": advisory,
        }
    }

    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
