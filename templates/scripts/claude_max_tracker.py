#!/usr/bin/env python3
"""
Purpose:
    StopSession hook -- parses completed session JSONL, sums token usage,
    persists to claude_max_usage table, updates model_balancer.json weekly_max_pct.

Contract:
    Reads JSON from stdin ({session_id, transcript_path, cwd}).
    Exits 0 always. Silent on errors.
    Skips if CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE=1.

CLI:
    echo '{...stop_event...}' | python3 claude_max_tracker.py     # hook mode
    python3 claude_max_tracker.py --weekly-usage                  # print rolling 7d totals
    python3 claude_max_tracker.py --update-balancer               # write pct to model_balancer.json

ENV:
    CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE=1  -- skip all processing, exit 0.

Files:
    ~/.claude/rolling_memory.db  -- target SQLite database (claude_max_usage table).
    ~/.claude/model_balancer.json  -- updated with live weekly_max_pct.
    ~/.claude/logs/claude_max_tracker.log  -- rotating log.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path.home() / ".claude" / "rolling_memory.db"
BALANCER_PATH = Path.home() / ".claude" / "model_balancer.json"
LOGS_DIR = Path.home() / ".claude" / "logs"


def _get_connection() -> sqlite3.Connection:
    """Open DB connection, ensure claude_max_usage table exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    # Create table if missing (script may run before rolling_memory v8 migration)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS claude_max_usage (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT    NOT NULL,
            ts_utc      TEXT    NOT NULL,
            input_tokens          INTEGER NOT NULL DEFAULT 0,
            cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens         INTEGER NOT NULL DEFAULT 0,
            project_root TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_claude_max_session
            ON claude_max_usage (session_id);
        CREATE INDEX IF NOT EXISTS idx_claude_max_ts
            ON claude_max_usage (ts_utc);
    """)
    return conn


def _load_balancer_json() -> dict:
    """Load model_balancer.json. Returns empty dict on any error."""
    try:
        if BALANCER_PATH.exists():
            return json.loads(BALANCER_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _compute_weekly_pct(conn: sqlite3.Connection) -> "float | None":
    """Return weekly_max_pct (0..1) if weekly_tokens_cap is configured, else None."""
    cfg = _load_balancer_json()
    cap = cfg.get("weekly_tokens_cap", 0)
    if not cap or cap <= 0:
        return None
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens + cache_creation_tokens + output_tokens), 0) "
        "FROM claude_max_usage WHERE ts_utc >= datetime('now', '-7 days')"
    ).fetchone()
    total = row[0] if row else 0
    return min(1.0, total / cap)


def _update_balancer_json(conn: sqlite3.Connection) -> "tuple[bool, float]":
    """
    Read model_balancer.json, compute weekly_max_pct, write back atomically.

    Returns (updated: bool, pct: float). updated=False when cap not configured.
    """
    pct = _compute_weekly_pct(conn)
    if pct is None:
        return False, 0.0

    try:
        data = _load_balancer_json()
        if "inputs_snapshot" not in data or not isinstance(data.get("inputs_snapshot"), dict):
            data["inputs_snapshot"] = {}
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data["inputs_snapshot"]["claude_max_weekly_used_pct"] = pct
        data["inputs_snapshot"]["weekly_pct_source"] = "live"
        data["inputs_snapshot"]["weekly_pct_updated_at"] = now_iso

        tmp = BALANCER_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, BALANCER_PATH)
    except Exception:
        pass

    return True, pct


def _parse_transcript(transcript_path: str) -> "tuple[int, int, int]":
    """
    Parse a session JSONL file and sum token usage from all assistant turns.

    Returns (input_tokens, cache_creation_tokens, output_tokens).
    Lines where type != 'assistant' or usage is absent are skipped.
    """
    input_tokens = 0
    cache_creation_tokens = 0
    output_tokens = 0

    try:
        path = Path(transcript_path)
        if not path.exists():
            return 0, 0, 0
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                usage = obj.get("message", {}).get("usage")
                if not isinstance(usage, dict):
                    continue
                input_tokens += int(usage.get("input_tokens", 0) or 0)
                cache_creation_tokens += int(usage.get("cache_creation_input_tokens", 0) or 0)
                output_tokens += int(usage.get("output_tokens", 0) or 0)
    except Exception:
        pass

    return input_tokens, cache_creation_tokens, output_tokens


def main() -> None:
    """StopSession hook mode: read event from stdin, process, exit 0."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except Exception:
        return

    try:
        transcript_path = event.get("transcript_path", "")
        session_id = event.get("session_id", "")
        cwd = event.get("cwd", "")

        if not session_id or not transcript_path:
            return

        input_tokens, cache_creation_tokens, output_tokens = _parse_transcript(transcript_path)

        conn = _get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO claude_max_usage
                    (session_id, ts_utc, input_tokens, cache_creation_tokens, output_tokens, project_root)
                VALUES (?, datetime('now'), ?, ?, ?, ?)
                """,
                (session_id, input_tokens, cache_creation_tokens, output_tokens, cwd or None),
            )
            conn.commit()

            _update_balancer_json(conn)

            # Prune rows older than 14 days
            conn.execute(
                "DELETE FROM claude_max_usage WHERE ts_utc < datetime('now', '-14 days')"
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def cmd_weekly_usage() -> None:
    """Print rolling 7-day token usage summary."""
    try:
        conn = _get_connection()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as sessions,
                    COALESCE(SUM(input_tokens), 0) as input_tokens,
                    COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                    COALESCE(SUM(output_tokens), 0) as output_tokens,
                    COALESCE(SUM(input_tokens + cache_creation_tokens + output_tokens), 0) as total
                FROM claude_max_usage
                WHERE ts_utc >= datetime('now', '-7 days')
                """
            ).fetchone()
            sessions, inp, cache, out, total = row

            cfg = _load_balancer_json()
            cap = cfg.get("weekly_tokens_cap", 0)

            print("Rolling 7-day token usage (claude_max_usage table):")
            print(f"  sessions: {sessions}")
            print(f"  input_tokens: {inp}")
            print(f"  cache_creation_tokens: {cache}")
            print(f"  output_tokens: {out}")
            print(f"  total_tokens: {total}")

            if cap and cap > 0:
                pct = _compute_weekly_pct(conn)
                pct_display = f"{pct * 100:.2f}%" if pct is not None else "N/A"
                print(f"  weekly_tokens_cap: {cap}")
                print(f"  weekly_max_pct: {pct_display} (live)")
            else:
                print("  weekly_tokens_cap: not set")
                print("  weekly_max_pct: fallback to snapshot (cap not configured)")
        finally:
            conn.close()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_update_balancer() -> None:
    """Write live weekly_max_pct to model_balancer.json."""
    try:
        conn = _get_connection()
        try:
            updated, pct = _update_balancer_json(conn)
        finally:
            conn.close()

        if updated:
            print(f"Updated model_balancer.json: weekly_max_pct={pct * 100:.2f}% (live)")
        else:
            print("Cap not configured — no update made")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    if os.environ.get("CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE", "") == "1":
        sys.exit(0)

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--weekly-usage":
            cmd_weekly_usage()
        elif arg == "--update-balancer":
            cmd_update_balancer()
        else:
            print(f"Unknown argument: {arg}", file=sys.stderr)
            sys.exit(1)
    else:
        main()
