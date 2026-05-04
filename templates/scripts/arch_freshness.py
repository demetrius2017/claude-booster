#!/usr/bin/env python3
"""PostToolUse hook: warn (non-blocking) when source files are edited but ARCHITECTURE.md
has not been updated in the same session.

Purpose:
    When an agent edits or writes a source file during a session, remind it to keep
    ARCHITECTURE.md current if the change may affect system architecture or dependencies.
    The warning is a nudge, not a gate — PostToolUse exit codes are ignored by the
    harness, so this hook CANNOT block anything.

    The warning fires at most ONCE per session (tracked via a state file) to avoid
    noisy repetition on every source edit.

Contract:
    stdin  — PostToolUse JSON payload from Claude Code harness:
               {tool_name, tool_input.file_path, cwd, agent_id, agent_type,
                session_id, transcript_path, …}
    stderr — human-readable warning (non-blocking) when ARCHITECTURE.md is stale
    exit   — always 0 (PostToolUse exit code is ignored by harness)

    ARCHITECTURE.md is located via find_upward() starting from the cwd.
    If no ARCHITECTURE.md exists in the project tree, the hook skips silently
    (nothing to keep fresh).

    The once-per-session guard uses:
        <project_root>/.claude/.arch_freshness_warned
    containing the current session_id.  Any session mismatch triggers a fresh warning.

CLI / Examples:
    # Simulate an edit that should trigger the warning:
    echo '{
      "tool_name": "Edit",
      "tool_input": {"file_path": "/proj/backend/nav.py"},
      "cwd": "/proj",
      "session_id": "abc123",
      "transcript_path": "/tmp/transcript.jsonl"
    }' | python3 arch_freshness.py

    # Bypass via env:
    CLAUDE_BOOSTER_SKIP_ARCH_GATE=1 python3 arch_freshness.py < payload.json

Limitations:
    - Transcript scan is JSONL line-by-line (compact format); it reads the whole
      transcript, not a tail, because ARCHITECTURE.md may have been updated early.
    - Allowlist is path-fragment / suffix based, not semantic.
    - The state file is per-project-root, not per-user.  Two concurrent sessions
      editing the same project share the once-per-session guard — only the first
      session that fires the warning will write to the state file.

ENV/Files:
    - Reads  : stdin (hook JSON)
               session transcript JSONL (to check for ARCHITECTURE.md edits)
               <project_root>/.claude/.arch_freshness_warned  (session guard)
    - Writes : <project_root>/.claude/.arch_freshness_warned  (session guard)
               ~/.claude/logs/arch_freshness_decisions.jsonl  (append-only)
    - ENV    : CLAUDE_BOOSTER_SKIP_ARCH_GATE=1  — bypass the hook entirely
               CLAUDE_HOME                       — override ~/.claude base dir
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------
# _gate_common import — same two-step pattern as dep_guard.py
# --------------------------------------------------------------------------

try:
    from _gate_common import (
        DECISION_ALLOW,
        append_jsonl,
        find_upward,
        is_subagent_context,
        iso_now,
        project_root_from,
    )
except ImportError:
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from _gate_common import (  # type: ignore[no-redef]
        DECISION_ALLOW,
        append_jsonl,
        find_upward,
        is_subagent_context,
        iso_now,
        project_root_from,
    )

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

ARCH_LOG_NAME = "arch_freshness_decisions.jsonl"

# Tools that produce file edits — the only ones this hook intercepts.
EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

# Paths allowlisted from the warning (these files are documentation themselves
# or are test/scratch artifacts — editing them doesn't imply architectural change).
_ALLOWLIST_SUFFIXES = (".md", ".txt")
_ALLOWLIST_DIR_FRAGMENTS = (
    "docs/",
    "reports/",
    "tests/",
    ".claude/",
    "scratch/",
    "/tmp/",
)

# State file name inside the project's .claude/ directory.
_WARNED_STATE_FILENAME = ".arch_freshness_warned"

# --------------------------------------------------------------------------
# Allowlist helpers
# --------------------------------------------------------------------------


def _is_allowlisted(file_path: str) -> bool:
    """Return True if file_path matches any allowlisted pattern (skip hook)."""
    for suffix in _ALLOWLIST_SUFFIXES:
        if file_path.endswith(suffix):
            return True
    normalised = file_path.replace("\\", "/")
    for fragment in _ALLOWLIST_DIR_FRAGMENTS:
        if fragment in normalised:
            return True
    return False


# --------------------------------------------------------------------------
# Once-per-session guard (in-memory + state file)
# --------------------------------------------------------------------------

# In-memory session guard: tracks session_ids that have already warned in THIS process.
# This ensures once-per-session even across different project roots / tempdirs.
_WARNED_SESSIONS: set[str] = set()


def _warned_state_path(cwd: str) -> Optional[Path]:
    """Return path to the session-guard state file.

    Prefers <project_root>/.claude/.arch_freshness_warned if a project root
    (marked by .git/ or .claude/) exists. Falls back to <cwd>/.claude/ if not.
    Returns None only if cwd itself is None or invalid.
    """
    root = project_root_from(cwd)
    if root is not None:
        return root / ".claude" / _WARNED_STATE_FILENAME

    # Fallback: use the cwd itself as the context root (useful for isolated tests).
    if cwd:
        return Path(cwd) / ".claude" / _WARNED_STATE_FILENAME

    return None


def _already_warned(session_id: str, state_path: Optional[Path]) -> bool:
    """Return True if we already emitted the warning for this session_id.

    Checks in-memory guard first (fast path for same session across different projects),
    then falls back to filesystem state file for persistence across process invocations.
    """
    if not session_id:
        return False

    # In-memory guard: this process has already warned for this session.
    if session_id in _WARNED_SESSIONS:
        return True

    # Filesystem guard: another process warned for this session in this project.
    if state_path is not None:
        try:
            stored = state_path.read_text(encoding="utf-8").strip()
            if stored == session_id:
                return True
        except OSError:
            pass

    return False


def _mark_warned(session_id: str, state_path: Optional[Path]) -> None:
    """Mark session as warned in both in-memory and filesystem guards."""
    if not session_id:
        return

    # In-memory guard.
    _WARNED_SESSIONS.add(session_id)

    # Filesystem guard for cross-process persistence.
    if state_path is not None:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(session_id, encoding="utf-8")
        except OSError:
            pass  # Fail-soft: in-memory guard is enough for this process.


# --------------------------------------------------------------------------
# ARCHITECTURE.md existence check
# --------------------------------------------------------------------------


def _find_arch_md(cwd: str) -> Optional[Path]:
    """Return Path to ARCHITECTURE.md if it exists in the project tree, else None."""
    return find_upward(cwd, "ARCHITECTURE.md")


# --------------------------------------------------------------------------
# Transcript scan for ARCHITECTURE.md edits
# --------------------------------------------------------------------------


def _arch_was_edited_in_session(transcript_path: str) -> bool:
    """Return True if ARCHITECTURE.md was Edit/Written in this session's transcript.

    Scans every line of the transcript JSONL for Edit or Write tool_use blocks
    whose file_path contains "ARCHITECTURE.md".  Reads the full transcript
    (not just a tail) because the arch update may have happened early in the session.
    """
    if not transcript_path:
        return False
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return False

    for raw in lines:
        raw = raw.strip()
        if not raw or raw[0] != "{":
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        msg = obj.get("message") or {}
        # Tool calls appear in assistant messages as content blocks with type "tool_use".
        if msg.get("role") != "assistant":
            continue

        for block in msg.get("content", []) or []:
            if block.get("type") != "tool_use":
                continue
            tool_name: str = block.get("name") or ""
            if tool_name not in EDIT_TOOLS:
                continue
            tool_input: dict = block.get("input") or {}
            fp: str = tool_input.get("file_path") or ""
            if "ARCHITECTURE.md" in fp:
                return True

    return False


# --------------------------------------------------------------------------
# Warning message
# --------------------------------------------------------------------------


def _build_warning(file_path: str) -> str:
    return (
        f"[arch_freshness] WARNING: '{file_path}' was edited but ARCHITECTURE.md"
        " has NOT been updated in this session.\n"
        "If this change affects system architecture or dependencies, update"
        " ARCHITECTURE.md before closing the session.\n"
        "Or run /architecture --update to refresh it automatically.\n"
        "(Bypass: CLAUDE_BOOSTER_SKIP_ARCH_GATE=1)"
    )


# --------------------------------------------------------------------------
# Main logic
# --------------------------------------------------------------------------


def main() -> int:  # noqa: C901 (complexity acceptable for a hook entry-point)
    # Read and parse stdin — fail-open on bad payload.
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        raw = ""

    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}

    tool_name: str = data.get("tool_name") or ""
    tool_input: dict = data.get("tool_input") or {}
    cwd: str = data.get("cwd") or ""
    session_id: str = data.get("session_id") or ""
    transcript_path: str = data.get("transcript_path") or ""

    # Only intercept Edit / Write / NotebookEdit.
    if tool_name not in EDIT_TOOLS:
        return 0

    file_path: str = tool_input.get("file_path") or ""
    if not file_path:
        return 0

    # Build base log record shared by all outcomes.
    base_record: dict = {
        "ts": iso_now(),
        "gate": "arch_freshness",
        "tool_name": tool_name,
        "file_path": file_path,
        "cwd": cwd,
        "session_id": session_id,
    }

    # Sub-agent bypass — arch freshness is a Lead-level concern.
    if is_subagent_context(data):
        append_jsonl(ARCH_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "sub-agent context (auto-skip)",
        })
        return 0

    # Env bypass.
    if os.environ.get("CLAUDE_BOOSTER_SKIP_ARCH_GATE") == "1":
        append_jsonl(ARCH_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "env CLAUDE_BOOSTER_SKIP_ARCH_GATE=1",
        })
        return 0

    # Allowlist: docs, scratch, tests, .claude/, /tmp/, *.md, *.txt — skip silently.
    if _is_allowlisted(file_path):
        append_jsonl(ARCH_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "allowlisted path",
        })
        return 0

    # If ARCHITECTURE.md doesn't exist in the project tree, nothing to keep fresh.
    arch_path = _find_arch_md(cwd)
    if arch_path is None:
        append_jsonl(ARCH_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "no ARCHITECTURE.md found in project tree (skip)",
        })
        return 0

    # Once-per-session guard: if we already warned this session, stay quiet.
    state_path = _warned_state_path(cwd)
    if _already_warned(session_id, state_path):
        append_jsonl(ARCH_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "warning already emitted this session (once-per-session guard)",
        })
        return 0

    # Scan transcript: if ARCHITECTURE.md was already edited this session, no warning.
    if _arch_was_edited_in_session(transcript_path):
        append_jsonl(ARCH_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "ARCHITECTURE.md already edited in this session",
        })
        return 0

    # Emit warning to stderr and mark warned for this session.
    warning = _build_warning(file_path)
    sys.stderr.write(warning + "\n")
    _mark_warned(session_id, state_path)
    append_jsonl(ARCH_LOG_NAME, {
        **base_record,
        "decision": "warn",
        "reason": "source file edited but ARCHITECTURE.md not updated this session",
        "arch_md_path": str(arch_path),
        "state_file": str(state_path) if state_path else None,
    })

    # PostToolUse: always exit 0 — harness ignores exit codes anyway.
    return 0


if __name__ == "__main__":
    sys.exit(main())
