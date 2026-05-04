#!/usr/bin/env python3
"""PreToolUse hook: block DML on protected database tables.

Purpose:
    Enforce "fix the producer, not the data" — when an agent tries to issue
    UPDATE, DELETE, or TRUNCATE against a table listed in the project's
    dep_manifest.json (append_only_tables / data_patches_forbidden) or
    protected_tables.txt, this hook blocks the Bash call at the harness layer
    and explains which function to call instead.

    INSERT on append-only tables is allowed (they're insert-permitted; only
    mutation is forbidden). All DML on unlisted tables passes through.

Contract:
    stdin  — PreToolUse JSON payload from Claude Code harness:
               {tool_name, tool_input.command, cwd, agent_id, agent_type,
                session_id, transcript_path, …}
    stderr — human-readable block reason on exit 2
    exit   — 0 allow, 2 block, 1 fail-open (programming error)

    Protected tables are read from (first found wins):
      1. <project>/docs/dep_manifest.json
             .append_only_tables   — list of table names
             .data_patches_forbidden — list of "table.column" strings
      2. <project>/.claude/protected_tables.txt
             one table name per line; # lines and blank lines ignored

CLI / Examples:
    # Simulate a blocked UPDATE:
    echo '{
      "tool_name": "Bash",
      "tool_input": {"command": "psql $DB -c \\"UPDATE accounts SET balance=0\\""},
      "cwd": "/Users/me/Projects/myproject",
      "session_id": "test-session"
    }' | python3 financial_dml_guard.py

    # Authorise one-off via marker in transcript (add to assistant message):
    # [dml-authorized]

    # Authorise via env:
    CLAUDE_BOOSTER_DML_ALLOWED=1 python3 financial_dml_guard.py < payload.json

Limitations:
    - Detects only inline SQL in the Bash command string. DML inside a .py /
      .sql file that is *executed* by the command is not inspected.
    - Case-insensitive matching for DML keywords and table names.
    - Regex-based SQL extraction — not a full parser. Handles the documented
      patterns (psql -c, sqlite3, heredoc, echo pipe) but not obfuscated SQL.
    - Table name extraction stops at the first word after the DML keyword
      (before WHERE / SET / JOIN). Schema-qualified names (public.accounts)
      are matched by their base name (accounts) and the full qualified form.
    - Bypass via transcript requires [dml-authorized] in last 50 assistant
      text blocks; blocks further than 50 messages back are not checked.

ENV/Files:
    - Reads  : stdin (hook JSON)
               <project>/docs/dep_manifest.json      (protected table config)
               <project>/.claude/protected_tables.txt   (fallback table list)
               session transcript JSONL (bypass marker check)
    - Writes : ~/.claude/logs/financial_dml_guard_decisions.jsonl (append-only)
    - ENV    : CLAUDE_BOOSTER_DML_ALLOWED=1   — bypass the gate entirely
               CLAUDE_HOME                    — override ~/.claude base dir
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# --------------------------------------------------------------------------
# _gate_common import — same two-step pattern as delegate_gate.py
# --------------------------------------------------------------------------

try:
    from _gate_common import (
        DECISION_ALLOW,
        DECISION_BLOCK,
        append_jsonl,
        find_upward,
        is_subagent_context,
        iso_now,
        redact_secrets,
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
        redact_secrets,
    )

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

DML_LOG_NAME = "financial_dml_guard_decisions.jsonl"

# DML operations that are blocked on protected tables.
# INSERT is intentionally absent — append-only means you may insert, not mutate.
BLOCKED_OPS = {"UPDATE", "DELETE", "TRUNCATE"}

# Quick pre-filter: if none of these appear in the command, exit 0 immediately.
QUICK_FILTER_RE = re.compile(r"\b(?:UPDATE|DELETE|TRUNCATE)\b", re.IGNORECASE)

# Capture the operation and the table name that follows it.
# Handles:
#   UPDATE <table>  SET ...
#   DELETE FROM <table>  [WHERE ...]
#   TRUNCATE [TABLE] <table>
# Table name is the next identifier token after the keyword (and optional FROM/TABLE).
_DML_TABLE_RE = re.compile(
    r"\b(UPDATE|DELETE|TRUNCATE)\b"                 # op
    r"(?:\s+(?:FROM|TABLE)\b)?"                     # optional FROM / TABLE
    r"\s+"
    r"([A-Za-z_][A-Za-z0-9_.]*)",                  # table name or schema.table
    re.IGNORECASE,
)

# --------------------------------------------------------------------------
# Transcript parsing (bypass marker check)
# --------------------------------------------------------------------------

_MARKER_RE = re.compile(r"\[dml-authorized\]", re.IGNORECASE)


def _tail_jsonl(path: str, n: int = 50) -> List[str]:
    """Return the last ``n`` lines of the JSONL file. Empty list on error."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            chunk = min(size, 128 * 1024)  # 128 KB is ample for 50 messages
            fh.seek(size - chunk)
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[-n:]


def _marker_in_transcript(transcript_path: str) -> bool:
    """Return True if [dml-authorized] appears in the last 50 assistant messages."""
    if not transcript_path:
        return False
    lines = _tail_jsonl(transcript_path, n=50)
    for raw in lines:
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
            if block.get("type") == "text":
                text = block.get("text") or ""
                if _MARKER_RE.search(text):
                    return True
    return False


# --------------------------------------------------------------------------
# Protected table loading
# --------------------------------------------------------------------------

def _load_from_manifest(manifest_path: Path) -> Tuple[Set[str], Dict[str, str]]:
    """Parse dep_manifest.json.

    Returns:
        (protected_tables, producer_hints)
        protected_tables — lower-cased table names that must not be mutated
        producer_hints   — {table_lower: hint_text} for the block message
    """
    protected: Set[str] = set()
    hints: Dict[str, str] = {}
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return protected, hints

    # append_only_tables: ["accounts", "fills", ...]
    for table in data.get("append_only_tables") or []:
        if isinstance(table, str) and table.strip():
            t = table.strip().lower()
            protected.add(t)
            # Check for producer hint keyed the same way
            producer = (data.get("producers") or {}).get(table) or ""
            if producer:
                hints[t] = producer

    # data_patches_forbidden: ["orders.filled_quantity", "fills.commission", ...]
    for col_ref in data.get("data_patches_forbidden") or []:
        if isinstance(col_ref, str) and "." in col_ref:
            table = col_ref.split(".")[0].strip().lower()
            if table:
                protected.add(table)
                producer = (data.get("producers") or {}).get(col_ref.split(".")[0].strip()) or ""
                if producer and table not in hints:
                    hints[table] = producer

    return protected, hints


def _load_from_txt(txt_path: Path) -> Set[str]:
    """Parse protected_tables.txt — one name per line, # for comments."""
    protected: Set[str] = set()
    try:
        for line in txt_path.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line:
                protected.add(line.lower())
    except OSError:
        pass
    return protected


def _resolve_protected_tables(cwd: str) -> Tuple[FrozenSet[str], Dict[str, str]]:
    """Find and load protected table config for the current project.

    Returns (frozenset of lower-cased table names, producer_hints dict).
    Empty frozenset if no config found (fail-open).
    """
    manifest = find_upward(cwd, "docs/dep_manifest.json")
    if manifest is not None:
        tables, hints = _load_from_manifest(manifest)
        return frozenset(tables), hints

    txt = find_upward(cwd, ".claude/protected_tables.txt")
    if txt is not None:
        return frozenset(_load_from_txt(txt)), {}

    # No config found — no tables to protect.
    return frozenset(), {}


# --------------------------------------------------------------------------
# SQL extraction from Bash command string
# --------------------------------------------------------------------------

def _extract_sql_fragments(command: str) -> List[str]:
    """Pull SQL text out of a Bash command string.

    Handles:
      - psql ... -c "SQL"  /  psql ... -c 'SQL'
      - sqlite3 db.db "SQL"  /  sqlite3 db.db 'SQL'
      - echo "SQL" | psql  (echo body)
      - heredoc: psql << EOF\\nSQL\\nEOF
      - Any double- or single-quoted string containing a DML keyword

    Returns a list of candidate SQL fragments to check.
    """
    fragments: List[str] = []

    # The whole command is always a candidate (catches unquoted inline SQL
    # and anything we don't pattern-match below).
    fragments.append(command)

    # psql/sqlite3 -c 'SQL' or -c "SQL"
    for m in re.finditer(
        r"""\s-[cC]\s+(?:(['\"])(.+?)\1)""",
        command,
        re.DOTALL,
    ):
        fragments.append(m.group(2))

    # echo "SQL" | psql  — grab the echo argument
    for m in re.finditer(
        r"""(?i)\becho\s+(?:(['\"])(.+?)\1|(\S+))""",
        command,
        re.DOTALL,
    ):
        fragments.append(m.group(2) or m.group(3) or "")

    # sqlite3 dbpath "SQL"  — second positional argument in quotes
    for m in re.finditer(
        r"""(?i)\bsqlite3\S*\s+\S+\s+(?:(['\"])(.+?)\1)""",
        command,
        re.DOTALL,
    ):
        fragments.append(m.group(2))

    # Heredoc body: anything between the opening word and EOF
    for m in re.finditer(
        r"""<<\s*['\"]?(\w+)['\"]?\s*\n(.+?)^\1$""",
        command,
        re.DOTALL | re.MULTILINE,
    ):
        fragments.append(m.group(2))

    return [f for f in fragments if f.strip()]


def _extract_dml_ops(command: str) -> List[Tuple[str, str]]:
    """Return list of (op_upper, table_lower) pairs found in the command."""
    results: List[Tuple[str, str]] = []
    for fragment in _extract_sql_fragments(command):
        for m in _DML_TABLE_RE.finditer(fragment):
            op = m.group(1).upper()
            raw_table = m.group(2).strip()
            # Handle schema.table — extract base name AND keep qualified name
            if "." in raw_table:
                parts = raw_table.split(".")
                results.append((op, raw_table.lower()))           # qualified
                results.append((op, parts[-1].lower()))           # base name
            else:
                results.append((op, raw_table.lower()))
    return results


# --------------------------------------------------------------------------
# Block message builder
# --------------------------------------------------------------------------

def _build_block_message(
    op: str,
    table: str,
    producer_hint: str,
) -> str:
    lines = [
        f"financial_dml_guard: BLOCKED {op} on protected table '{table}'.",
        "",
        "This table is protected under the 'fix the producer, not the data' policy.",
        f"  Operation : {op}",
        f"  Table     : {table}",
    ]
    if producer_hint:
        lines.append(f"  Fix via   : {producer_hint}")
    lines += [
        "",
        "To authorise this one-off mutation deliberately, add '[dml-authorized]' to",
        "your next assistant message before rerunning the command.",
        "Or set CLAUDE_BOOSTER_DML_ALLOWED=1 in the environment.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main logic
# --------------------------------------------------------------------------

def main() -> int:
    # Read and parse stdin (fail-open on bad payload — gating unknown stdin
    # is worse than letting a corrupt message through)
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

    # Only intercept Bash calls.
    if tool_name != "Bash":
        return 0

    command: str = tool_input.get("command") or ""

    # Quick pre-filter — skip if no DML keyword present at all (<1 ms).
    if not QUICK_FILTER_RE.search(command):
        return 0

    # Build base log record (shared by all outcomes below).
    base_record: dict = {
        "ts": iso_now(),
        "gate": "financial_dml_guard",
        "tool_name": tool_name,
        "cwd": cwd,
        "session_id": session_id,
        "command_excerpt": redact_secrets(command[:300]),
    }

    # Sub-agent bypass — delegation already happened; the guard's scope is
    # the Lead making direct DML calls, not agents doing their job.
    if is_subagent_context(data):
        append_jsonl(DML_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "sub-agent context (auto-skip)",
        })
        return 0

    # Env bypass.
    if os.environ.get("CLAUDE_BOOSTER_DML_ALLOWED") == "1":
        append_jsonl(DML_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "env CLAUDE_BOOSTER_DML_ALLOWED=1",
        })
        return 0

    # Load protected tables. If no config found: fail-open immediately.
    protected_tables, producer_hints = _resolve_protected_tables(cwd)
    if not protected_tables:
        append_jsonl(DML_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "no protected table config found (fail-open)",
        })
        return 0

    # Extract (op, table) pairs from the command.
    dml_ops = _extract_dml_ops(command)
    if not dml_ops:
        # DML keyword present but couldn't parse a table name — let it through.
        append_jsonl(DML_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "DML keyword present but no table name extracted",
        })
        return 0

    # Find the first hit against protected tables.
    first_hit: Optional[Tuple[str, str]] = None
    for op, table in dml_ops:
        if op in BLOCKED_OPS and table in protected_tables:
            first_hit = (op, table)
            break

    if first_hit is None:
        # No protected table touched — allow.
        append_jsonl(DML_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "no protected tables in DML",
            "tables_checked": [t for _, t in dml_ops],
        })
        return 0

    op, table = first_hit

    # Check transcript bypass marker before blocking.
    if _marker_in_transcript(transcript_path):
        append_jsonl(DML_LOG_NAME, {
            **base_record,
            "decision": DECISION_ALLOW,
            "reason": "[dml-authorized] marker found in transcript",
            "op": op,
            "table": table,
        })
        return 0

    # Block.
    producer_hint = producer_hints.get(table, "")
    msg = _build_block_message(op, table, producer_hint)
    sys.stderr.write(msg + "\n")
    append_jsonl(DML_LOG_NAME, {
        **base_record,
        "decision": DECISION_BLOCK,
        "reason": f"{op} on protected table '{table}'",
        "op": op,
        "table": table,
        "producer_hint": producer_hint,
    })
    return 2


if __name__ == "__main__":
    sys.exit(main())
