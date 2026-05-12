#!/usr/bin/env python3
"""
Purpose:
    PostToolUse hook — captures per-tool-call latency + token usage into the
    model_metrics table of ~/.claude/rolling_memory.db.

Contract:
    Reads a JSON event from stdin.  Silently exits 0 on: empty stdin,
    malformed JSON, missing required fields, DB error, or env var
    CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE=1.

    For Task/Agent tools: inserts a row with provider=anthropic, timing and
    token data extracted from event["tool_response"]["usage"].

    For Bash tools: inserts a row only when the command invokes codex_worker.sh
    or "codex exec -m"; provider=codex-cli, duration_ms=NULL.

CLI:
    echo '<json-event>' | python3 model_metric_capture.py

Limitations:
    Requires sqlite3 (stdlib).  No retry on DB lock — timeout=2.0 s then silent.

ENV:
    CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE=1  — skip all processing, exit 0.

Files:
    ~/.claude/rolling_memory.db  — target SQLite database (model_metrics table).
"""

import json
import os
import re
import sqlite3
import sys

DB_PATH = os.path.expanduser("~/.claude/rolling_memory.db")

# Provider name constants — must match templates/scripts/model_balancer.py
# (kept local to avoid hot-path import of model_balancer on every tool call).
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_CODEX = "codex-cli"

# ts_utc uses SQL datetime('now') so format matches the comparison bound
# `datetime('now','-14 days')` used by model_balancer._query_metrics.
# Python isoformat() emits "2026-05-12T15:30:00+00:00" which lexically
# diverges from SQLite's "2026-05-12 15:30:00" — same row, different sort.
INSERT_SQL = """
INSERT INTO model_metrics
    (ts_utc, provider, model, task_category, duration_ms, num_turns,
     per_turn_ms, tokens_in, tokens_out, success, session_id, project_root)
VALUES
    (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
"""


def _task_category(subagent_type: str, description: str) -> str:
    """Derive task_category from subagent_type and description."""
    desc = description.lower()
    if subagent_type == "Explore" or "explore" in desc or "recon" in desc:
        return "recon"
    if "worker" in desc or "verifier" in desc:
        return "coding"
    if subagent_type == "Plan" or "plan" in desc:
        return "hard"
    if "audit" in desc or "consilium" in desc:
        return "hard"
    return "medium"


def _parse_codex_model(command: str) -> str:
    """
    Extract model name from a codex command string.
    Tries -m <MODEL> flag first, then first positional arg after script name.
    """
    # Try explicit -m flag
    m = re.search(r"-m\s+(\S+)", command)
    if m:
        return m.group(1)
    # Try positional arg: codex_worker.sh <model> ...
    script_m = re.search(r"codex_worker\.sh\s+(\S+)", command)
    if script_m:
        return script_m.group(1)
    return "unknown"


def _get_project_root() -> str:
    try:
        return os.getcwd()
    except OSError:
        return ""


def handle_event(event: dict) -> bool:
    """
    Process a single parsed event dict.
    Returns True if a row was inserted, False otherwise.
    """
    tool_name = event.get("tool_name", "")
    session_id = event.get("session_id", "")
    project_root = _get_project_root()

    if tool_name in ("Task", "Agent"):
        tool_response = event.get("tool_response") or {}
        usage = tool_response.get("usage")
        if not usage:
            return False

        duration_ms = usage.get("duration_ms")
        if duration_ms is None:
            return False

        num_turns = usage.get("num_turns", 1) or 1
        tokens_in = usage.get("input_tokens")
        tokens_out = usage.get("output_tokens")

        tool_input = event.get("tool_input") or {}
        model = tool_input.get("model") or "inherit"
        description = tool_input.get("description") or ""
        subagent_type = tool_input.get("subagent_type") or ""

        category = _task_category(subagent_type, description)
        per_turn_ms = int(duration_ms / max(num_turns, 1))

        _insert_row(PROVIDER_ANTHROPIC, model, category,
                    duration_ms, num_turns, per_turn_ms,
                    tokens_in, tokens_out, session_id, project_root)
        return True

    if tool_name == "Bash":
        tool_input = event.get("tool_input") or {}
        command = tool_input.get("command") or ""
        if "codex_worker.sh" in command or "codex exec -m" in command:
            model = _parse_codex_model(command)
            _insert_row(PROVIDER_CODEX, model, "medium",
                        None, None, None,
                        None, None, session_id, project_root)
            return True

    return False


def _insert_row(provider, model, task_category,
                duration_ms, num_turns, per_turn_ms,
                tokens_in, tokens_out, session_id, project_root):
    # isolation_level=None → autocommit; PRAGMA synchronous=NORMAL trades
    # one fsync per commit for ~3-8ms savings per PostToolUse invocation.
    conn = sqlite3.connect(DB_PATH, timeout=2.0, isolation_level=None)
    try:
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(INSERT_SQL, (
            provider, model, task_category,
            duration_ms, num_turns, per_turn_ms,
            tokens_in, tokens_out,
            session_id, project_root,
        ))
    finally:
        conn.close()


def main():
    if os.environ.get("CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE") == "1":
        return

    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return

    if not isinstance(event, dict):
        return

    try:
        handle_event(event)
    except Exception:
        pass


if __name__ == "__main__":
    main()
