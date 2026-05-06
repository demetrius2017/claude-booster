#!/usr/bin/env python3
"""Agent-health telemetry: five anti-theater signals surfaced at /start.

Purpose:
    Consilium 2026-04-18 §Q3 (telemetry aggregator) addresses scenario §5.1
    measurement gap: forcing functions decay into theater without observability.
    This script inspects the last N handover files and the rolling_memory DB
    and prints five concrete signals that SessionStart can append to the
    existing knowledge-base context. Run as a CLI, not an MCP server — per
    consilium, MCP is reconsidered on 2026-06-18 with telemetry data.

Contract:
    --project PATH   : project root to scan (default: $(git rev-parse
                       --show-toplevel) else cwd). reports/ directory must
                       exist underneath.
    --window DAYS    : sliding window for cadence + N/A ratio (default: 30).
    --limit N        : inspect the last N handovers (default: 10).
    --json           : emit JSON envelope instead of prose.

Output (prose):
    === AGENT HEALTH (last 10 handovers, last 4 weeks) ===
    Evidence artifacts (curl/psql/sqlite/python): 7/10 handovers ✓ (target ≥8/10)
    N/A ratio (verification fields): 2/10 (20%) ✓ (target <30%)
    Overdue [UNDER REVIEW] tags: 0 ✓
    Superseded rows / stale citations: 0 / 0 ✓
    Session cadence: 3 handovers / 30d (steady)

Output (JSON):
    {
      "cmd": "telemetry",
      "project": "Claude_Booster",
      "window_days": 30,
      "handovers_inspected": 10,
      "signals": {
        "evidence_density": {"value": 7, "denominator": 10, "ok": false, "target": "≥8/10"},
        "na_ratio":          {"ratio": 0.20, "ok": true, "target": "<0.30"},
        "overdue_tags":      {"count": 0, "ok": true, "target": 0},
        "stale_citations":   {"superseded_rows": 0, "cited": 0, "ok": true},
        "cadence":           {"handovers_in_window": 3, "note": "steady"}
      },
      "exit_status": "ok"
    }

Limitations:
    - Signal #5 (topic repetition) is NOT implemented — it needs a
      per-project topic vocabulary the script can't infer reliably on v1.
      Substituted with "session cadence" (handovers in window) as a
      simpler thrashing/stagnation proxy.
    - "Blocked handovers" metric (from consilium example) requires Q2
      enforcement hook to land first; returns 0 until then (noted below).
    - Signal #2 "N/A ratio" uses a heuristic: any line containing
      ``(?i)(?:\\bN/?A\\b|nothing to verify|skipped)`` inside sections
      named like "verification", "evidence", "verify", "ver.evidence".

ENV/Files:
    - Reads   : <project>/reports/handover_*.md, ~/.claude/rolling_memory.db
    - Writes  : nothing
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import math
import os
import pathlib
import re
import sqlite3
import subprocess
import sys

# Reuse the gates' own CLAUDE_HOME-honouring logs_dir() so telemetry and
# the enforcement path look at the SAME file. Without this a test (or any
# install) that sets CLAUDE_HOME would have telemetry read ~/.claude/logs
# while the gates write to $CLAUDE_HOME/logs — silent miss of bypass
# attempts is the exact scenario we're trying to surface.
try:
    from _gate_common import logs_dir as _gate_logs_dir
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    try:
        from _gate_common import logs_dir as _gate_logs_dir  # type: ignore[no-redef]
    except ImportError:
        _gate_logs_dir = None  # type: ignore[assignment]

DB_PATH = pathlib.Path.home() / ".claude" / "rolling_memory.db"
RULES_DIR = pathlib.Path.home() / ".claude" / "rules"
AUTOMEMORY_ROOT = pathlib.Path.home() / ".claude" / "projects"
BYPASS_LOG_NAME = "gate_bypass_attempts.jsonl"


def _logs_dir() -> pathlib.Path:
    """Resolve the gate log directory at call time (env-sensitive)."""
    if _gate_logs_dir is not None:
        return pathlib.Path(_gate_logs_dir())
    base = os.environ.get("CLAUDE_HOME")
    if base:
        return pathlib.Path(base) / "logs"
    return pathlib.Path.home() / ".claude" / "logs"

# How many recent rows of gate_bypass_attempts.jsonl count toward the
# "last 10 sessions" surveillance signal. One bypass event ≈ one session
# firing a gate with an off-mode file present, so N=10 is a reasonable
# proxy. Re-validate once gate telemetry has a month of data.
BYPASS_RECENT_N = 10

# Per audit_2026-04-18_startup_token_budget §Measurement: fixed always-on
# overhead from Claude Code harness + deferred tool names + MCP skill list +
# built-in tool schemas, measured 2026-04-18. Re-validate quarterly.
_BASE_OVERHEAD_TOKENS = 14_925  # Claude Code (6000) + deferred/MCP (1675) + tool schemas (4250) + session hook (620) + other (~2380)
_CHARS_PER_TOKEN = 3.2  # conservative estimate; re-validate with tiktoken when available

# Evidence markers — shell/db/browser commands that prove the work was
# actually run somewhere, not just reasoned about. Keep narrow: general-
# purpose tokens like "python" would false-positive every Python file.
# All patterns are case-insensitive (handover prose mixes `SELECT`/`select`,
# `PRAGMA`/`pragma`, `HTTP/1.1`/`http/1.1`, `Screenshot`/`screenshot`).
EVIDENCE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bcurl\b",
        r"\bpsql\b",
        r"\bsqlite3\b",
        r"\bSELECT\s",
        r"\bdocker\b",
        r"\bkubectl\b",
        r"\bDevTools\b",
        r"\bScreenshot\b",
        r"\bHTTP/\d",
        r"\bexit\s*=\s*\d",
        r"\bPRAGMA\b",
        r"\bnpm run\b",
        r"\bpnpm\b",
        r"\bwget\b",
        r"\bpytest\b",
    )
]

# N/A / skip markers inside verification sections. The regex deliberately
# does NOT match "N/A" in a bullet's body outside a verification block —
# that's a legitimate "not applicable" like "API: N/A (no runtime)".
NA_MARKERS = re.compile(
    r"(?i)(?:\bN\s*/\s*A\b|\bnot applicable\b|\bnothing to verify\b|\bskipped\b)",
)

# Detect a "Verification" section heading.
VER_HEADING = re.compile(
    r"(?im)^(?:##+|\*\*)\s*(?:verification|evidence|verify|proof|smoke\s+tests?|tests?)\b",
)

HANDOVER_DATE = re.compile(r"handover_(\d{4}-\d{2}-\d{2})_")


def _project_root(explicit: str | None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return pathlib.Path(out.stdout.strip())
    except FileNotFoundError:
        pass
    try:
        return pathlib.Path(os.getcwd())
    except OSError:
        return pathlib.Path.home()


def _load_handovers(project: pathlib.Path, limit: int | None) -> list[pathlib.Path]:
    """Return handover files sorted newest-first. ``limit=None`` returns all."""
    reports = project / "reports"
    if not reports.is_dir():
        return []
    files = sorted(
        reports.glob("handover_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files if limit is None else files[:limit]


# Heading marker for "next section starts here" — anything at the start of
# a line that looks like a Markdown heading (##, ###, #### …) or a bolded
# pseudo-heading (`**Label**`). Used to bound verification-section slices.
_NEXT_HEADING = re.compile(r"(?m)^(?:#{2,}\s|\*\*[^*]{1,80}\*\*\s*$)")


def _evidence_density(files: list[pathlib.Path]) -> dict:
    """How many of the inspected handovers contain ≥1 evidence marker?

    Threshold uses ``ceil(80%)`` so that e.g. 4/6 (≈66%) does NOT pass.
    """
    with_evidence = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(p.search(text) for p in EVIDENCE_PATTERNS):
            with_evidence += 1
    total = len(files)
    required = 0 if total == 0 else max(1, math.ceil(total * 0.8))
    ok = with_evidence >= required
    return {
        "value": with_evidence,
        "denominator": total,
        "required": required,
        "ok": ok,
        "target": "≥80% of handovers",
    }


def _na_ratio(files: list[pathlib.Path]) -> dict:
    """Fraction of *verified* handovers whose verification section contains N/A.

    Denominator is the number of handovers that actually have a Verification /
    Evidence heading — files without such a section are excluded, otherwise a
    heading-format drift would silently make the metric look healthier.
    Body of the section is cut at the next ``##+`` heading, not a fixed
    character count, so long verification blocks are fully considered and
    N/A strings from unrelated later sections don't leak in.
    """
    na_hits = 0
    with_verification = 0
    scanned = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        scanned += 1
        ver_match = VER_HEADING.search(text)
        if not ver_match:
            continue
        with_verification += 1
        remainder = text[ver_match.end():]
        next_h = _NEXT_HEADING.search(remainder)
        body = remainder[: next_h.start()] if next_h else remainder
        if NA_MARKERS.search(body):
            na_hits += 1
    ratio = (na_hits / with_verification) if with_verification else 0.0
    return {
        "hits": na_hits,
        "with_verification": with_verification,
        "scanned": scanned,
        "ratio": round(ratio, 2),
        "ok": ratio < 0.30,
        "target": "<0.30",
        "note": None if with_verification else "no verification sections found",
    }


def _overdue_tags(today: _dt.date) -> dict:
    """Count of under_review DB rows past resolve_by_date."""
    if not DB_PATH.exists():
        return {"count": 0, "ok": True, "target": 0, "note": "DB absent"}
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    except sqlite3.OperationalError as exc:
        return {"count": -1, "ok": False, "target": 0, "error": str(exc)}
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_memory)").fetchall()}
        if "status" not in cols or "resolve_by_date" not in cols:
            return {"count": 0, "ok": True, "target": 0, "note": "pre-v5 schema"}
        row = conn.execute(
            "SELECT COUNT(*) FROM agent_memory "
            "WHERE status='under_review' AND active=1 "
            "AND resolve_by_date IS NOT NULL "
            "AND resolve_by_date < ?",
            (today.isoformat(),),
        ).fetchone()
        count = int(row[0])
        return {"count": count, "ok": count == 0, "target": 0}
    finally:
        conn.close()


def _stale_citations(files: list[pathlib.Path]) -> dict:
    """Count of superseded rows + how many are cited (by source path) in recent handovers."""
    if not DB_PATH.exists():
        return {"superseded_rows": 0, "cited": 0, "ok": True, "note": "DB absent"}
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        return {"superseded_rows": -1, "cited": -1, "ok": False, "error": str(exc)}
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_memory)").fetchall()}
        if "status" not in cols:
            return {"superseded_rows": 0, "cited": 0, "ok": True, "note": "pre-v5 schema"}
        rows = conn.execute(
            "SELECT id, source FROM agent_memory WHERE status='superseded' AND active=1"
        ).fetchall()
    finally:
        conn.close()

    superseded_rows = len(rows)
    if superseded_rows == 0:
        return {"superseded_rows": 0, "cited": 0, "ok": True}

    handover_text = ""
    for f in files:
        try:
            handover_text += f.read_text(encoding="utf-8") + "\n"
        except OSError:
            continue

    cited = 0
    for r in rows:
        src = r["source"] or ""
        if src and src in handover_text:
            cited += 1

    return {
        "superseded_rows": superseded_rows,
        "cited": cited,
        "ok": cited == 0,
        "target": "0 stale citations",
    }


def _token_budget(project: pathlib.Path) -> dict:
    """Estimate always-on startup token cost (R1 from startup-budget audit).

    Sums:
        * ~/.claude/rules/*.md (conditionally loaded by Claude Code).
        * ~/.claude/projects/<proj>/memory/*.md (auto-memory for this proj).
        * Fixed harness overhead (tool schemas, MCP, session hook, etc.).

    Returns rough token counts + headroom vs 1M context. Numbers are an
    estimate — re-validate with tiktoken quarterly.
    """
    def _bytes_of(glob_path: pathlib.Path, pattern: str) -> int:
        total = 0
        if not glob_path.is_dir():
            return 0
        for p in glob_path.rglob(pattern):
            try:
                total += p.stat().st_size
            except OSError:
                continue
        return total

    rules_bytes = _bytes_of(RULES_DIR, "*.md")
    rules_tokens = int(rules_bytes / _CHARS_PER_TOKEN)

    # Auto-memory dir is namespaced by sanitized cwd — Claude Code replaces
    # both `/` and `_` with `-` (observed in ~/.claude/projects/ naming).
    memory_tokens = 0
    sanitized = str(project.resolve()).replace("/", "-").replace("_", "-")
    if sanitized.startswith("-"):
        candidate = AUTOMEMORY_ROOT / sanitized / "memory"
        if candidate.is_dir():
            memory_tokens = int(_bytes_of(candidate, "*.md") / _CHARS_PER_TOKEN)

    always_on = _BASE_OVERHEAD_TOKENS + rules_tokens + memory_tokens
    context_window = 1_000_000
    headroom_pct = 100 - (always_on * 100 // context_window)
    return {
        "rules_tokens": rules_tokens,
        "memory_tokens": memory_tokens,
        "base_overhead_tokens": _BASE_OVERHEAD_TOKENS,
        "always_on_tokens": always_on,
        "context_window": context_window,
        "headroom_pct": headroom_pct,
    }


def _cadence(files: list[pathlib.Path], today: _dt.date, window_days: int) -> dict:
    """Count handovers whose filename date falls within the window."""
    cutoff = today - _dt.timedelta(days=window_days)
    count = 0
    for f in files:
        m = HANDOVER_DATE.search(f.name)
        if not m:
            continue
        try:
            d = _dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if cutoff <= d <= today:
            count += 1
    if count == 0:
        note = "stagnant (no handovers in window)"
        ok = False
    elif count > 10:
        note = "thrashing (>10 handovers in window)"
        ok = False
    else:
        note = "steady"
        ok = True
    return {"handovers_in_window": count, "window_days": window_days, "note": note, "ok": ok}


def _delegate_bypass_file(project: pathlib.Path) -> dict:
    """Check whether the delegate_gate bypass file (.delegate_mode) exists and is active.

    The bypass file can be placed silently before the gate is installed,
    resulting in zero log entries in gate_bypass_attempts.jsonl while
    enforcement is completely disabled.  This signal surfaces the file
    directly so it cannot hide from telemetry.

    Returns:
        exists (bool)  : whether .claude/.delegate_mode exists under project.
        value  (str)   : stripped content of the file, or "" if absent.
        ok     (bool)  : False when the file exists AND its content is "off"
                         (case-insensitive). True in all other cases.
    """
    bypass_file = project / ".claude" / ".delegate_mode"
    if not bypass_file.is_file():
        return {"exists": False, "value": "", "ok": True}
    try:
        raw = bypass_file.read_text(encoding="utf-8")
    except OSError as exc:
        # Can't read → treat conservatively as "possibly active bypass".
        return {"exists": True, "value": "", "ok": False, "note": f"unreadable: {exc}"}
    value = raw.strip()
    if not value:
        # Empty file is not a valid "off" signal — treat as ok.
        return {"exists": True, "value": value, "ok": True}
    ok = value.lower() != "off"
    return {"exists": True, "value": value, "ok": ok}


def _gate_bypass_attempts(limit: int = BYPASS_RECENT_N) -> dict:
    """Inspect the tail of ``gate_bypass_attempts.jsonl`` and report counts.

    Returns a dict with ``recent`` (rows in the inspected window),
    ``refused`` (rows with decision=='bypass_refused'), and ``ok`` (True
    when no refused rows recently — any refused attempt flips to ⚠).
    Missing log file → zero counts, ok=True (treat as "no bypasses seen").
    """
    bypass_log_file = _logs_dir() / BYPASS_LOG_NAME
    if not bypass_log_file.is_file():
        return {"recent": 0, "refused": 0, "ok": True, "note": "log absent"}
    # O(1) memory tail: deque(maxlen=limit) drops oldest lines as we read.
    # The entire file is NOT held in memory, which matters once the log
    # grows past tens of thousands of rows.
    try:
        with open(bypass_log_file, "r", encoding="utf-8") as f:
            if limit and limit > 0:
                tail_lines = list(collections.deque(f, maxlen=limit))
            else:
                tail_lines = list(f)
    except OSError as exc:
        return {"recent": 0, "refused": 0, "ok": True, "note": f"log unreadable: {exc}"}

    tail: list[dict] = []
    for line in tail_lines:
        line = line.strip()
        if not line:
            continue
        try:
            tail.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    refused = sum(1 for r in tail if r.get("decision") == "bypass_refused")
    return {
        "recent": len(tail),
        "refused": refused,
        "ok": refused == 0,
        "target": "0 refused attempts in last 10",
    }


def _prose_line(label: str, text: str, ok: bool) -> str:
    marker = "✓" if ok else "⚠"
    return f"{label}: {text} {marker}"


def render_prose(project: str, signals: dict, inspected: int, window: int, budget: dict | None = None) -> str:
    lines = [f"=== AGENT HEALTH — project={project} (last {inspected} handovers, {window}d window) ==="]
    if budget is not None:
        lines.append(
            f"Startup token budget: {budget['always_on_tokens']:,} tok always-on "
            f"(rules {budget['rules_tokens']:,} + memory {budget['memory_tokens']:,} "
            f"+ base {budget['base_overhead_tokens']:,}) — "
            f"headroom {budget['headroom_pct']}% of 1M"
        )

    ev = signals["evidence_density"]
    lines.append(
        _prose_line(
            "Evidence artifacts (curl/psql/sqlite/HTTP/etc.)",
            f"{ev['value']}/{ev['denominator']} handovers (target {ev['target']})",
            ev["ok"],
        )
    )

    na = signals["na_ratio"]
    pct = int(na["ratio"] * 100)
    if na.get("note"):
        na_text = f"{na['note']} ({na['scanned']} handovers scanned)"
    else:
        na_text = (
            f"{na['hits']}/{na['with_verification']} verified ({pct}%) "
            f"(target {na['target']})"
        )
    lines.append(_prose_line("N/A ratio in verification sections", na_text, na["ok"]))

    ot = signals["overdue_tags"]
    if ot.get("error"):
        overdue_text = f"DB error: {ot['error']}"
    else:
        note = f" ({ot['note']})" if ot.get("note") else ""
        overdue_text = f"{ot['count']}{note}"
    lines.append(
        _prose_line("Overdue [UNDER REVIEW] tags", overdue_text, ot["ok"])
    )

    sc = signals["stale_citations"]
    if sc.get("error"):
        stale_text = f"DB error: {sc['error']}"
    else:
        note = f" ({sc['note']})" if sc.get("note") else ""
        stale_text = f"{sc['superseded_rows']} / {sc['cited']}{note}"
    lines.append(
        _prose_line("Superseded rows / stale citations", stale_text, sc["ok"])
    )

    ca = signals["cadence"]
    lines.append(
        _prose_line(
            "Session cadence",
            f"{ca['handovers_in_window']} handovers / {ca['window_days']}d — {ca['note']}",
            ca["ok"],
        )
    )

    by = signals.get("gate_bypass")
    if by is not None:
        if by.get("note"):
            bypass_text = f"{by['recent']} recent ({by['note']})"
        else:
            bypass_text = (
                f"{by['recent']} in last {BYPASS_RECENT_N} sessions "
                f"({by['refused']} refused)"
            )
        lines.append(_prose_line("Gate bypass attempts", bypass_text, by["ok"]))

    dbf = signals.get("delegate_bypass_file")
    if dbf is not None:
        if not dbf["exists"]:
            dbf_text = "not present"
        elif dbf.get("note"):
            dbf_text = f"present ({dbf['note']})"
        elif dbf["ok"]:
            dbf_text = f"present (value={dbf['value']!r}, not 'off')"
        else:
            dbf_text = f"ACTIVE — contains 'off' (enforcement disabled)"
        lines.append(
            _prose_line("Delegate gate bypass file (.delegate_mode)", dbf_text, dbf["ok"])
        )

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=None, help="Project root (default: git toplevel or cwd)")
    ap.add_argument("--window", type=int, default=30, help="Cadence/N-A window in days")
    ap.add_argument("--limit", type=int, default=10, help="How many recent handovers to inspect")
    ap.add_argument("--today", default=None, help="Override today ISO YYYY-MM-DD (for tests)")
    ap.add_argument("--json", action="store_true", help="Emit JSON envelope instead of prose")
    args = ap.parse_args()

    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()
    project = _project_root(args.project)
    # Cadence needs the full handover list — truncating to --limit would
    # cap the "thrashing" alert at the limit value (default 10), making it
    # unreachable. Text-inspection signals use the truncated subset.
    all_files = _load_handovers(project, limit=None)
    recent_files = all_files[: args.limit]

    signals = {
        "evidence_density": _evidence_density(recent_files),
        "na_ratio": _na_ratio(recent_files),
        "overdue_tags": _overdue_tags(today),
        "stale_citations": _stale_citations(recent_files),
        "cadence": _cadence(all_files, today, args.window),
        "gate_bypass": _gate_bypass_attempts(),
        "delegate_bypass_file": _delegate_bypass_file(project),
    }
    budget = _token_budget(project)
    files = recent_files

    any_bad = any(not sig.get("ok", True) for sig in signals.values())
    exit_status = "warn" if any_bad else "ok"

    if args.json:
        payload = {
            "cmd": "telemetry",
            "project": project.name,
            "project_path": str(project),
            "window_days": args.window,
            "handovers_inspected": len(files),
            "signals": signals,
            "startup_budget": budget,
            "exit_status": exit_status,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_prose(project.name, signals, len(files), args.window, budget))

    # Exit 0 always — telemetry is informational, not blocking. A non-zero
    # code here would break SessionStart hook output pipelines.
    return 0


if __name__ == "__main__":
    sys.exit(main())
