#!/usr/bin/env python3
"""PostToolUse hook — advisory: write a one-shot marker when context is large.

Purpose:
    Estimate context size after every tool call by stat-ing the session transcript.
    When the estimated token count crosses the threshold (default 120 000) and no
    marker for this session exists yet, write a marker file so that the next
    UserPromptSubmit hook can inject a one-line /compact reminder into the prompt.

    This replaces self-discipline with deterministic automation: Lead no longer has
    to remember to check context size; the harness signals proactively.

Contract:
    stdin  — PostToolUse JSON: {session_id, transcript_path, cwd, ...}
    stdout — silent (advisory; nothing emitted to Claude's context)
    exit   — 0 always (never blocks tool use)

Bypass:
    CLAUDE_BOOSTER_SKIP_COMPACT_ADVISOR=1  → exit 0 immediately, no-op

Files:
    ~/.claude/.compact_recommended_<session_id>  — one-shot marker (content = token estimate)
    CLAUDE_BOOSTER_COMPACT_THRESHOLD             — env override for token threshold (default 120000)
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path


try:
    _THRESHOLD = int(os.environ.get("CLAUDE_BOOSTER_COMPACT_THRESHOLD", "120000"))
except ValueError:
    _THRESHOLD = 120000  # malformed env var → fall back silently to default
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
    transcript_path = data.get("transcript_path", "")

    if not session_id or not transcript_path:
        return 0

    # Defense-in-depth: session_id must be a valid UUID to be used in a filesystem path
    if not _SESSION_ID_RE.match(session_id):
        return 0

    marker = Path.home() / ".claude" / f".compact_recommended_{session_id}"

    # One-shot: if marker already exists, nothing to do
    if marker.exists():
        return 0

    # Estimate tokens via transcript file size (bytes // 4 ≈ tokens)
    try:
        size_bytes = os.stat(transcript_path).st_size
    except OSError:
        return 0

    estimated_tokens = size_bytes // 4

    if estimated_tokens < _THRESHOLD:
        return 0

    # Write marker atomically to avoid partial writes / race conditions
    try:
        marker_dir = marker.parent
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=marker_dir,
            delete=False,
            prefix=".compact_tmp_",
            suffix=f"_{session_id}",
        ) as tmp:
            tmp.write(str(estimated_tokens))
            tmp_path = tmp.name
        os.replace(tmp_path, marker)
    except Exception:
        # Best-effort advisory: never raise, never fail the hook
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
