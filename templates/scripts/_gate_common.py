#!/usr/bin/env python3
"""Shared helpers for delegate_gate.py and ask_gate.py.

Purpose:
    Both gates share byte-identical logging, timestamp, and path-walking
    primitives. Keeping them in two files lets silent drift happen: a typo
    in "bypass_honoured" on one side, a differently-cached mkdir on the
    other. This module is the single source of truth.

Contract:
    logs_dir() -> Path
        Returns the gate-log directory. Honours $CLAUDE_HOME (tests set
        this), falls back to ~/.claude. Computed per-call (not cached)
        because env overrides in subprocess tests must take effect.

    iso_now() -> str
        UTC timestamp as "YYYY-MM-DDTHH:MM:SSZ".

    append_jsonl(log_name: str, record: dict) -> None
        Appends one JSON line to logs_dir()/log_name. Fail-soft on OSError
        — gating must not fail because logging fails. Uses default=str so
        non-serialisable fields (e.g. Path) don't raise. The parent dir's
        mkdir is cached per-process (_LOG_DIR_READY) to eliminate the
        redundant syscall on the hot path.

    walk_up_to(start, predicate) -> Optional[Path]
        Walks [start, *start.parents], returns first path where
        predicate(p) is truthy. Catches OSError on the initial resolution.

    project_root_from(cwd_hint) -> Optional[Path]
        Thin wrapper: walks to the nearest ancestor with .git/ or .claude/.

    find_upward(cwd_hint, relpath) -> Optional[Path]
        Walks ancestors looking for p / relpath on disk.

Limitations:
    - Python 3.8+ compat: no `X | Y`, no `dict[str, int]`.
    - Log-dir mkdir cache is per-process. If a test deletes the dir and
      re-fires within the same process, the second call won't recreate
      the dir. Fine for our subprocess-based tests; worth knowing for
      future in-process callers.

ENV/Files:
    - Reads  : env $CLAUDE_HOME (optional)
    - Writes : <logs_dir>/<log_name> (append)
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Callable, Optional, Set


# ---- Shared log-file names (single source of truth) --------------------

DELEGATE_LOG_NAME = "delegate_gate_decisions.jsonl"
ASK_LOG_NAME = "ask_gate_decisions.jsonl"
BYPASS_LOG_NAME = "gate_bypass_attempts.jsonl"


# ---- Decision constants (prevents silent typo drift) -------------------

DECISION_ALLOW = "allow"
DECISION_BLOCK = "block"
DECISION_AUTO_SKIP = "auto_skip"
DECISION_BYPASS_HONOURED = "bypass_honoured"
DECISION_BYPASS_REFUSED = "bypass_refused"


# ---- Logging primitives ------------------------------------------------

# Per-process cache of log dirs we've already mkdir'd. Hot-path gates can
# fire thousands of times per session; the mkdir syscall shows up on
# traces when we don't cache it.
_LOG_DIR_READY: Set[Path] = set()


def logs_dir() -> Path:
    base = os.environ.get("CLAUDE_HOME")
    if base:
        return Path(base) / "logs"
    return Path.home() / ".claude" / "logs"


def iso_now() -> str:
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def append_jsonl(log_name: str, record: dict) -> None:
    """Append one JSON line to logs_dir()/log_name. Fail-soft."""
    try:
        d = logs_dir()
        if d not in _LOG_DIR_READY:
            d.mkdir(parents=True, exist_ok=True)
            _LOG_DIR_READY.add(d)
        path = d / log_name
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        # Gating must not fail because logging fails. Any non-OSError is
        # a programming bug (bad record shape) and is allowed to surface.
        pass


# ---- Path walking ------------------------------------------------------

def walk_up_to(start: Path, predicate: Callable[[Path], bool]) -> Optional[Path]:
    """Walk [start, *start.parents]; return first path where predicate is truthy."""
    try:
        resolved = start if isinstance(start, Path) else Path(start)
    except (TypeError, OSError):
        return None
    try:
        chain = [resolved, *resolved.parents]
    except (OSError, ValueError):
        return None
    for p in chain:
        try:
            if predicate(p):
                return p
        except OSError:
            continue
    return None


def _cwd_from_hint(cwd_hint: Optional[str]) -> Optional[Path]:
    try:
        if cwd_hint:
            return Path(cwd_hint)
        return Path.cwd()
    except (FileNotFoundError, OSError):
        return None


def project_root_from(cwd_hint: Optional[str]) -> Optional[Path]:
    """Nearest ancestor containing .git/ or .claude/. None if hint invalid."""
    cwd = _cwd_from_hint(cwd_hint)
    if cwd is None:
        return None
    return walk_up_to(
        cwd,
        lambda p: (p / ".git").exists() or (p / ".claude").is_dir(),
    )


def find_upward(cwd_hint: Optional[str], relpath: str) -> Optional[Path]:
    """Walk ancestors from cwd_hint looking for (ancestor / relpath) on disk."""
    cwd = _cwd_from_hint(cwd_hint)
    if cwd is None:
        return None
    hit = walk_up_to(cwd, lambda p: (p / relpath).exists())
    return (hit / relpath) if hit is not None else None


# ---- Sub-agent detection (multi-signal, defence-in-depth) --------------

def is_subagent_context(data: dict) -> bool:
    """Return True if hook stdin shows a sub-agent context.

    Claude Code v2.1.114+ passes BOTH ``agent_id`` and ``agent_type`` for
    sub-agent sessions. We check either one — if the harness ever renames
    one field, the other still carries the signal and sub-agents remain
    auto-skipped (the original delegate-budget incident is not reopened).
    """
    if not isinstance(data, dict):
        return False
    aid = data.get("agent_id")
    if isinstance(aid, str) and aid:
        return True
    atype = data.get("agent_type")
    if isinstance(atype, str) and atype:
        return True
    return False


# ---- Secret redaction (for log-record message excerpts) ----------------

# Matches common secret-bearing prefixes (api_key / token / secret /
# password / bearer), an optional separator (``=``, ``:``, whitespace or
# ``  ``), and the contiguous token that follows. Case-insensitive.
# JWT-ish ``eyJ...`` prefix is matched as a standalone token — JWTs rarely
# have a separator because they already carry one internally. Must run
# BEFORE ``[:200]`` truncation so we don't split a token mid-match.
_SECRET_RE = re.compile(
    r"(?i)(?:"
    r"(?:api[_-]?key|token|secret|password|bearer)[\s:=]*[\w\-\.]+"
    r"|eyJ[A-Za-z0-9_\-]+\.[\w\-\.]+"
    r")",
)


def redact_secrets(s: str) -> str:
    """Return ``s`` with runs matching the secret regex replaced by ``<redacted>``.

    Contract:
        - Non-str input → "" (defensive — we never propagate TypeError from
          a logging helper).
        - No match → input returned unchanged.
        - Match → the ENTIRE matched run (prefix + value) becomes
          ``<redacted>``. Callers truncate afterwards with ``[:200]``.
    """
    if not isinstance(s, str) or not s:
        return "" if not isinstance(s, str) else s
    return _SECRET_RE.sub("<redacted>", s)
