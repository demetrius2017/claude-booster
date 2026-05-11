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

try:
    from _gate_common import append_jsonl, iso_now
except ImportError:
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from _gate_common import append_jsonl, iso_now  # type: ignore[no-redef]

_SKIP = os.environ.get("CLAUDE_BOOSTER_SKIP_COMPACT_ADVISOR", "")

_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def main() -> int:
    if _SKIP:
        append_jsonl("compact_advisor.jsonl", {"ts": iso_now(), "event": "env_skip"})
        return 0

    # Parse stdin — malformed JSON is a silent no-op
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except Exception:
        append_jsonl("compact_advisor.jsonl", {"ts": iso_now(), "event": "invalid_input", "reason": "malformed_json"})
        return 0

    if not isinstance(data, dict):
        append_jsonl("compact_advisor.jsonl", {"ts": iso_now(), "event": "invalid_input", "reason": "malformed_json"})
        return 0

    session_id = data.get("session_id", "")
    if not session_id:
        append_jsonl("compact_advisor.jsonl", {"ts": iso_now(), "event": "invalid_input", "reason": "missing_field"})
        return 0

    # Defense-in-depth: session_id must be a valid UUID to be used in a filesystem path
    if not _SESSION_ID_RE.match(session_id):
        append_jsonl("compact_advisor.jsonl", {"ts": iso_now(), "event": "invalid_input", "reason": "invalid_uuid"})
        return 0

    marker = Path.home() / ".claude" / f".compact_recommended_{session_id}"

    if not marker.exists():
        append_jsonl("compact_advisor.jsonl", {"ts": iso_now(), "event": "no_marker", "session_id": session_id})
        return 0

    # Read token estimate from marker
    try:
        token_count_str = marker.read_text(encoding="utf-8").strip()
        token_count = int(token_count_str)
    except Exception:
        token_count = 120000  # fallback if unreadable

    # Delete marker — one-shot semantics rely on this succeeding.
    # Rare failure modes (read-only FS, race with concurrent inject): we still
    # inject the advisory this turn, but log to stderr so SRE can diagnose
    # "why did the advisory fire twice" later.
    try:
        marker.unlink()
    except Exception as exc:
        sys.stderr.write(
            f"compact_advisor_inject: marker.unlink failed for session {session_id}: {exc}\n"
        )

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
    append_jsonl("compact_advisor.jsonl", {"ts": iso_now(), "event": "injected", "session_id": session_id, "token_count": token_count})
    return 0


if __name__ == "__main__":
    sys.exit(main())
