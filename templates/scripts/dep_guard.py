#!/usr/bin/env python3
"""PreToolUse hook: block edits on critical components until dependency review is shown.

Purpose:
    Before an agent edits or writes a file that is marked `critical: true` in
    the project's dep_manifest.json, verify that the current session transcript
    contains at least one of the recognised evidence patterns confirming the
    agent has consulted the dependency graph.  This is a procedural check only:
    the hook does NOT verify understanding, only that the lookup happened.

Contract:
    stdin  — PreToolUse JSON payload from Claude Code harness:
               {tool_name, tool_input.file_path, cwd, agent_id, agent_type,
                session_id, transcript_path, …}
    stderr — human-readable block reason on exit 2
    exit   — 0 allow, 2 block, 1 fail-open (programming / unexpected error)

    Critical components are read from:
        <project>/.claude/dep_manifest.json
        .components.<name>.critical == true AND .components.<name>.file matches

    A component's file is "matched" when the absolute path being edited ends
    with the manifest's relative `file` field, or when the basenames match.

CLI / Examples:
    # Simulate an allowed edit (dep_manifest not present → fail-open):
    echo '{"tool_name":"Edit","tool_input":{"file_path":"/proj/backend/domain/nav.py"},
           "cwd":"/proj","session_id":"test"}' | python3 dep_guard.py

    # Authorise via explicit transcript marker:
    # Include one of the following in an assistant message:
    #   [dep-reviewed], dep_manifest, dependency table, ARCHITECTURE.md,
    #   downstream:, affected:, feeds:, called_by:

    # Bypass via environment variable:
    CLAUDE_BOOSTER_SKIP_DEP_GUARD=1 python3 dep_guard.py < payload.json

Limitations:
    - Scans only the last 30 assistant text-content blocks in the transcript.
      Retries up to 4 times (~350 ms total) because Claude Code may buffer
      the latest assistant message at hook-fire time (same strategy as
      verify_gate.py).
    - Pattern matching is keyword-based, not semantic: the agent is trusted to
      have read the manifest if it merely mentions a recognised keyword.
    - file_path matching uses suffix and basename heuristics; does NOT resolve
      symlinks or canonical paths.
    - Bypass via env is intentionally coarse (process-level), not per-file.

ENV/Files:
    - Reads  : stdin (hook JSON)
               <project>/.claude/dep_manifest.json
               session transcript JSONL (evidence scan)
    - Writes : ~/.claude/logs/dep_guard_decisions.jsonl (append-only)
    - ENV    : CLAUDE_BOOSTER_SKIP_DEP_GUARD=1  — bypass the gate entirely
               CLAUDE_HOME                       — override ~/.claude base dir
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# _gate_common import — same two-step pattern as financial_dml_guard.py
# --------------------------------------------------------------------------

try:
    from _gate_common import (
        DECISION_ALLOW,
        DECISION_BLOCK,
        append_jsonl,
        find_upward,
        is_subagent_context,
        iso_now,
    )
except ImportError:
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from _gate_common import (  # type: ignore[no-redef]
        DECISION_ALLOW,
        DECISION_BLOCK,
        append_jsonl,
        find_upward,
        is_subagent_context,
        iso_now,
    )

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

DEP_GUARD_LOG_NAME = "dep_guard_decisions.jsonl"

# Tools that produce file edits — the only ones this hook intercepts.
EDIT_TOOLS = frozenset({"Edit", "Write", "NotebookEdit"})

# Paths allowlisted from the guard (suffixes / directory prefixes that are
# never critical by definition — docs, scratch, tests, config dirs).
_ALLOWLIST_SUFFIXES = (".md", ".txt")
_ALLOWLIST_DIR_FRAGMENTS = (
    "docs/",
    "reports/",
    "tests/",
    ".claude/",
    "scratch/",
    "/tmp/",
)

# Evidence patterns in transcript assistant text.  Case-insensitive.
# Any single match is sufficient — we check procedural lookup, not depth.
_EVIDENCE_PATTERNS = re.compile(
    r"dep_manifest"
    r"|dependency table"
    r"|ARCHITECTURE\.md"
    r"|downstream[:\s]"
    r"|affected[:\s]"
    r"|feeds[:\s]"
    r"|called_by[:\s]"
    r"|\[dep-reviewed\]",
    re.IGNORECASE,
)

# How many assistant blocks to scan (tail of transcript).
TRANSCRIPT_SCAN_LIMIT = 30

# --------------------------------------------------------------------------
# Allowlist helpers
# --------------------------------------------------------------------------


def _is_allowlisted(file_path: str) -> bool:
    """Return True if file_path matches any allowlisted pattern (skip guard)."""
    # Check file extension suffixes.
    for suffix in _ALLOWLIST_SUFFIXES:
        if file_path.endswith(suffix):
            return True
    # Normalise path separators so both / and \ work.
    normalised = file_path.replace("\\", "/")
    for fragment in _ALLOWLIST_DIR_FRAGMENTS:
        if fragment in normalised:
            return True
    return False


# --------------------------------------------------------------------------
# dep_manifest loading
# --------------------------------------------------------------------------


def _load_critical_components(
    manifest_path: Path,
) -> Dict[str, dict]:
    """Parse dep_manifest.json and return only critical components.

    Returns:
        {component_name: component_dict} for all components with critical=true.
        Empty dict on parse error.
    """
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {}

    components: Dict[str, dict] = data.get("components") or {}
    return {
        name: comp
        for name, comp in components.items()
        if isinstance(comp, dict) and comp.get("critical") is True
    }


# --------------------------------------------------------------------------
# File-path matching
# --------------------------------------------------------------------------


def _normalise(path: str) -> str:
    """Normalise a path string to forward-slash, no trailing slash."""
    return path.replace("\\", "/").rstrip("/")


def _file_matches_component(file_path: str, component_file: str) -> bool:
    """Return True if the edited file_path corresponds to component_file.

    component_file is a manifest-relative path like "horizon/nav/calculator.py"
    (possibly with a ::function suffix that we strip).  file_path may be
    absolute or relative.

    Two match strategies (either is sufficient):
      1. file_path ends with the manifest relpath (suffix match on /).
      2. Basenames match (last path component, ignoring ::function suffix).
    """
    # Strip ::function suffix from manifest entry if present.
    comp_file = component_file.split("::")[0].strip()

    fp = _normalise(file_path)
    cf = _normalise(comp_file)

    # Strategy 1: suffix match — handles absolute paths and relative-looking paths.
    # Ensure we match on a directory boundary (not a partial filename).
    if fp == cf:
        return True
    if fp.endswith("/" + cf):
        return True

    # Strategy 2: basename match — coarser but handles renamed project roots.
    fp_base = fp.rsplit("/", 1)[-1]
    cf_base = cf.rsplit("/", 1)[-1]
    if fp_base and cf_base and fp_base == cf_base:
        return True

    return False


def _find_critical_match(
    file_path: str,
    critical_components: Dict[str, dict],
) -> Optional[Tuple[str, dict]]:
    """Return the first (component_name, component_dict) whose file matches file_path.

    Returns None if no critical component matches.
    """
    for name, comp in critical_components.items():
        comp_file: str = comp.get("file") or ""
        if not comp_file:
            continue
        if _file_matches_component(file_path, comp_file):
            return name, comp
    return None


# --------------------------------------------------------------------------
# Transcript evidence scan
# --------------------------------------------------------------------------


def _tail_jsonl(path: str, n: int) -> List[str]:
    """Return the last ``n`` lines of the JSONL file. Empty list on error."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            # 128 KB is ample for 30 assistant messages; avoid reading huge files.
            chunk = min(size, 128 * 1024)
            fh.seek(size - chunk)
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[-n:]


def _scan_lines_for_evidence(lines: List[str]) -> bool:
    """Scan a list of JSONL lines for dependency review evidence.

    Returns True if any recognised evidence pattern appears in the last
    TRANSCRIPT_SCAN_LIMIT assistant text blocks, False otherwise.
    """
    assistant_text_count = 0
    # Iterate newest-first (reversed) so we stop after 30 relevant blocks.
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw or raw[0] != "{":
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue

        msg = obj.get("message") or {}
        if msg.get("role") != "assistant":
            continue

        for block in msg.get("content", []) or []:
            if block.get("type") != "text":
                continue
            text = block.get("text") or ""
            assistant_text_count += 1
            if _EVIDENCE_PATTERNS.search(text):
                return True
            if assistant_text_count >= TRANSCRIPT_SCAN_LIMIT:
                return False

    return False


def _evidence_in_transcript(transcript_path: str) -> bool:
    """Return True if any recognised evidence pattern appears in the last
    TRANSCRIPT_SCAN_LIMIT assistant text blocks.

    Retries up to 4 times with short backoff because Claude Code may buffer
    the latest assistant message at the moment a PreToolUse hook fires — a
    single read can miss a block that is about to land on disk.  Total
    worst-case wait: ~350 ms (identical strategy to verify_gate.py).
    """
    if not transcript_path:
        return False

    # Retry loop — mirrors verify_gate.py §"Retry up to 3 times with short backoff".
    for delay in (0.0, 0.05, 0.15, 0.3):
        if delay:
            time.sleep(delay)
        # Read more lines than the scan limit so filtering down to text blocks
        # gives us enough coverage.
        lines = _tail_jsonl(transcript_path, n=200)
        if _scan_lines_for_evidence(lines):
            return True

    return False


# --------------------------------------------------------------------------
# Block message builder
# --------------------------------------------------------------------------


def _build_block_message(
    file_path: str,
    component_name: str,
    component: dict,
) -> str:
    called_by: List[str] = component.get("called_by") or []
    dependents_str = ", ".join(called_by) if called_by else "(none listed)"

    lines = [
        f"dep_guard: editing critical file '{file_path}' (component: {component_name}).",
        "",
        "Before editing, review dep_manifest.json to understand what depends on",
        "this function. Include one of the following in your response to proceed:",
        "  'downstream:', 'affected:', dep_manifest, [dep-reviewed], ARCHITECTURE.md",
        "",
        f"  Dependents (called_by): {dependents_str}",
    ]
    notes: str = component.get("notes") or ""
    if notes:
        lines += ["", f"  Notes     : {notes}"]
    lines += [
        "",
        "Or set CLAUDE_BOOSTER_SKIP_DEP_GUARD=1 in the environment to bypass.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main logic
# --------------------------------------------------------------------------


def main() -> int:
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
        # No file_path in payload — can't assess; fail-open.
        return 0

    # Build base log record shared by all outcomes.
    base_record: dict = {
        "ts": iso_now(),
        "gate": "dep_guard",
        "tool_name": tool_name,
        "file_path": file_path,
        "cwd": cwd,
        "session_id": session_id,
    }

    # Allowlist: docs, scratch, tests, .claude/, /tmp/, *.md, *.txt — always pass.
    if _is_allowlisted(file_path):
        append_jsonl(DEP_GUARD_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "allowlisted path",
        })
        return 0

    # Sub-agent bypass — agents do their own scoped work; guard targets Lead only.
    if is_subagent_context(data):
        append_jsonl(DEP_GUARD_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "sub-agent context (auto-skip)",
        })
        return 0

    # Env bypass.
    if os.environ.get("CLAUDE_BOOSTER_SKIP_DEP_GUARD") == "1":
        append_jsonl(DEP_GUARD_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "env CLAUDE_BOOSTER_SKIP_DEP_GUARD=1",
        })
        return 0

    # Locate dep_manifest.json — fail-open if not found.
    manifest_path = find_upward(cwd, ".claude/dep_manifest.json")
    if manifest_path is None:
        append_jsonl(DEP_GUARD_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "no dep_manifest.json found (fail-open)",
        })
        return 0

    # Load critical components.
    critical_components = _load_critical_components(manifest_path)
    if not critical_components:
        # Manifest exists but no critical components — nothing to guard.
        append_jsonl(DEP_GUARD_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "manifest has no critical components",
        })
        return 0

    # Check if the edited file matches any critical component.
    match = _find_critical_match(file_path, critical_components)
    if match is None:
        append_jsonl(DEP_GUARD_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "file not in critical components",
        })
        return 0

    component_name, component = match

    # File IS critical — check transcript for evidence of dependency review.
    if _evidence_in_transcript(transcript_path):
        append_jsonl(DEP_GUARD_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "dependency review evidence found in transcript",
            "component": component_name,
        })
        return 0

    # No evidence — block.
    msg = _build_block_message(file_path, component_name, component)
    sys.stderr.write(msg + "\n")
    append_jsonl(DEP_GUARD_LOG_NAME, {
        **base_record,
        "decision": DECISION_BLOCK,
        "reason": "critical file, no dependency review evidence in transcript",
        "component": component_name,
        "called_by": component.get("called_by") or [],
    })
    return 2


if __name__ == "__main__":
    sys.exit(main())
