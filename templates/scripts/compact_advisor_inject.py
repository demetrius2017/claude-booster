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


def _log_event(event: str, **fields: object) -> None:
    """Append one JSONL record to ~/.claude/logs/compact_advisor.jsonl.
    Best-effort: any failure swallowed silently — logging must not break the hook."""
    try:
        import datetime as _dt
        log_dir = Path.home() / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {"ts": _dt.datetime.now(_dt.timezone.utc).isoformat(), "event": event, **fields}
        with open(log_dir / "compact_advisor.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def main() -> int:
    if _SKIP:
        _log_event("env_skip")
        return 0

    # Parse stdin — malformed JSON is a silent no-op
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        data = json.loads(raw)
    except Exception:
        _log_event("invalid_input", reason="malformed_json")
        return 0

    if not isinstance(data, dict):
        _log_event("invalid_input", reason="malformed_json")
        return 0

    session_id = data.get("session_id", "")
    if not session_id:
        _log_event("invalid_input", reason="missing_field")
        return 0

    # Defense-in-depth: session_id must be a valid UUID to be used in a filesystem path
    if not _SESSION_ID_RE.match(session_id):
        _log_event("invalid_input", reason="invalid_uuid")
        return 0

    marker = Path.home() / ".claude" / f".compact_recommended_{session_id}"

    if not marker.exists():
        _log_event("no_marker", session_id=session_id)
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
    _log_event("injected", session_id=session_id, token_count=token_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
