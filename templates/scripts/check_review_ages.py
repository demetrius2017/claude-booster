#!/usr/bin/env python3
"""Surface overdue or malformed [UNDER REVIEW] tags in rules prose AND DB rows.

Purpose
-------
Scenario #4 (reports/scenario_planning_2026-04-18.md): [UNDER REVIEW] tags
without resolution dates metastasize into universal distrust. This script
enforces the canonical tag format on load and surfaces tags whose
``resolve by`` date has passed — so /start can bring attention to them
rather than silently preserving a stale warning.

As of Q1 (2026-04-18, schema v5), supersession state also lives in
``rolling_memory.db`` as ``agent_memory.status='under_review'`` rows with
``resolve_by_date`` set. This script scans both sources by default.

Contract
--------
Input  : --path FILE (default: all ~/.claude/rules/*.md for prose mode)
         --source {prose,db,both} (default: both)
         --today YYYY-MM-DD (override, for tests)
Output : Lines ``<origin>:<locator>:<STATUS>:<detail>``.
         origin  = absolute path (prose) or ``rolling_memory.db`` (db).
         STATUS ∈ {OVERDUE, MALFORMED, READ_ERROR}.
Exit   : 0 if no findings, 1 if any OVERDUE/MALFORMED/READ_ERROR hits.

CLI
---
    python ~/.claude/scripts/check_review_ages.py
    python ~/.claude/scripts/check_review_ages.py --source db
    python ~/.claude/scripts/check_review_ages.py --source prose
    python ~/.claude/scripts/check_review_ages.py --today 2026-06-01

Canonical format (prose)
------------------------
Real tags are always wrapped in markdown bold and use one of these forms:

    **[UNDER REVIEW since <ref> — "<reason>"; resolve by YYYY-MM-DD]**
    **[UNDER REVIEW since <ref>; resolve by YYYY-MM-DD]**  (short form,
                                                            reason in surrounding prose)

Where <ref> = audit_YYYY-MM-DD_* / consilium_YYYY-MM-DD_* / similar citation.
The leading ``**`` requirement distinguishes real tags from prose examples
(e.g. backtick-wrapped ``[UNDER REVIEW]`` in documentation).

DB schema (v5, Q1)
------------------
``agent_memory.status = 'under_review'`` with ``resolve_by_date`` ISO date.
Source: consilium_2026-04-18_memory_rearchitecture.md §Q1.

Limitations
-----------
- Plain ``**[UNDER REVIEW]**`` without ``since``/``resolve by`` is MALFORMED (prose).
- DB rows with status='under_review' but NULL resolve_by_date are MALFORMED.
- A tag with ``resolve by`` matching today's date is treated as OK (not overdue).
- Tags not wrapped in ``**...**`` are ignored — use markdown-bold for any
  tag that should be enforced.

ENV / Files
-----------
- Reads  : ~/.claude/rules/*.md (or --path target) AND
           ~/.claude/rolling_memory.db (read-only)
- Writes : nothing
"""

from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import re
import sqlite3
import sys

RULES_DIR = pathlib.Path.home() / ".claude" / "rules"
DB_PATH = pathlib.Path.home() / ".claude" / "rolling_memory.db"

_CANONICAL = re.compile(
    r"\*\*\[UNDER REVIEW\s+since\s+([^\];—]+?)(?:\s*—\s*\"([^\"]+)\")?\s*;\s*resolve by\s+(\d{4}-\d{2}-\d{2})\s*\]"
)
_ANY_TAG = re.compile(r"\*\*\[UNDER REVIEW[^\]]*\]")


def _scan_prose_file(path: pathlib.Path, today: _dt.date) -> list[str]:
    findings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}:0:READ_ERROR:{exc}"]
    for lineno, line in enumerate(text.splitlines(), start=1):
        for tag_match in _ANY_TAG.finditer(line):
            tag = tag_match.group(0)
            canon = _CANONICAL.search(tag)
            if not canon:
                findings.append(f"{path}:{lineno}:MALFORMED:{tag}")
                continue
            try:
                resolve_by = _dt.date.fromisoformat(canon.group(3))
            except ValueError:
                findings.append(f"{path}:{lineno}:MALFORMED:bad-date:{tag}")
                continue
            if resolve_by < today:
                ref = canon.group(1).strip()
                reason = canon.group(2) or "<no reason>"
                delta = (today - resolve_by).days
                findings.append(
                    f"{path}:{lineno}:OVERDUE:{delta}d past resolve_by={resolve_by} ref={ref} reason=\"{reason}\""
                )
    return findings


def _scan_db(today: _dt.date) -> list[str]:
    """Scan agent_memory for rows with status='under_review'.

    OVERDUE   : resolve_by_date < today.
    MALFORMED : status='under_review' but resolve_by_date IS NULL.

    Silently returns [] if the DB does not exist yet (fresh install) or
    is missing the v5 columns (pre-migration state) — those are infra
    bugs surfaced by other canaries, not tag-hygiene issues.
    """
    if not DB_PATH.exists():
        return []
    try:
        uri = f"file:{DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        return [f"rolling_memory.db:0:READ_ERROR:{exc}"]
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_memory)").fetchall()}
        if "status" not in cols or "resolve_by_date" not in cols:
            return []
        rows = conn.execute(
            "SELECT id, memory_type, category, resolve_by_date, substr(content, 1, 80) AS snippet "
            "FROM agent_memory "
            "WHERE status='under_review' AND active=1"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return [f"rolling_memory.db:0:READ_ERROR:{exc}"]
    finally:
        conn.close()

    findings: list[str] = []
    for r in rows:
        locator = f"id={r['id']} type={r['memory_type']} category={r['category'] or '<none>'}"
        rb = r["resolve_by_date"]
        if not rb:
            findings.append(f"rolling_memory.db:{locator}:MALFORMED:status=under_review but resolve_by_date IS NULL snippet=\"{r['snippet']}\"")
            continue
        try:
            resolve_by = _dt.date.fromisoformat(rb)
        except ValueError:
            findings.append(f"rolling_memory.db:{locator}:MALFORMED:bad-date={rb} snippet=\"{r['snippet']}\"")
            continue
        if resolve_by < today:
            delta = (today - resolve_by).days
            findings.append(
                f"rolling_memory.db:{locator}:OVERDUE:{delta}d past resolve_by={resolve_by} snippet=\"{r['snippet']}\""
            )
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=pathlib.Path, default=None,
                    help="Single prose file to scan (default: all ~/.claude/rules/*.md)")
    ap.add_argument("--source", choices=("prose", "db", "both"), default="both",
                    help="Which source to check (default: both)")
    ap.add_argument("--today", type=str, default=None,
                    help="Override today in ISO YYYY-MM-DD (for tests)")
    args = ap.parse_args()

    today = _dt.date.fromisoformat(args.today) if args.today else _dt.date.today()

    all_findings: list[str] = []

    if args.source in ("prose", "both"):
        if args.path:
            prose_targets = [args.path]
        else:
            prose_targets = sorted(RULES_DIR.glob("*.md"))
        for p in prose_targets:
            all_findings.extend(_scan_prose_file(p, today))

    if args.source in ("db", "both"):
        all_findings.extend(_scan_db(today))

    if all_findings:
        for f in all_findings:
            print(f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
