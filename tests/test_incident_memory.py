#!/usr/bin/env python3
"""Focused tests for incident report indexing and retrieval.

These tests import the template scripts directly, but force HOME, DB_PATH, and
PROJECTS_ROOT into a temporary sandbox before any DB work. They must never touch
the installed ~/.claude/rolling_memory.db or the real ~/Projects tree.
"""
from __future__ import annotations

import importlib
import types
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "templates" / "scripts"

passed = 0
failed = 0


def _ok(label: str) -> None:
    global passed
    passed += 1
    print(f"[PASS] {label}")


def _fail(label: str, detail: str = "") -> None:
    global failed
    failed += 1
    msg = f"[FAIL] {label}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)
    raise AssertionError(msg)


def _load_modules(home: Path):
    os.environ["HOME"] = str(home)
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    for name in ("index_reports", "rolling_memory"):
        sys.modules.pop(name, None)
    stuck_loop_key = types.ModuleType("stuck_loop_key")
    stuck_loop_key.STOPWORDS = frozenset()
    stuck_loop_key.make_stuck_loop_key = lambda *_args, **_kwargs: "test-key"
    stuck_loop_key.extract_first_step_body = lambda _content: ""
    sys.modules["stuck_loop_key"] = stuck_loop_key
    rolling_memory = importlib.import_module("rolling_memory")
    index_reports = importlib.import_module("index_reports")
    rolling_memory.DB_PATH = home / ".claude" / "rolling_memory.db"
    rolling_memory.BACKUP_PATH = rolling_memory.DB_PATH.with_suffix(".db.bak")
    index_reports.PROJECTS_ROOT = home / "Projects"
    return rolling_memory, index_reports


def _load_memory_post_tool():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    sys.modules.pop("memory_post_tool", None)
    return importlib.import_module("memory_post_tool")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """SELECT memory_type, content, priority, category, source,
                      idempotency_key, preserve, metadata_json, created_at
               FROM agent_memory
               WHERE active = 1
               ORDER BY memory_type, source"""
        ).fetchall()
    finally:
        conn.close()


def test_incident_indexing_and_regression() -> None:
    label = "incident indexing, dedupe, preserve, malformed type, consilium/audit regression"
    home = Path(tempfile.mkdtemp(prefix="cb_incident_memory_"))
    try:
        rolling_memory, index_reports = _load_modules(home)
        reports = home / "Projects" / "app" / "reports"
        _write(
            reports / "incident_2026-06-01_api.md",
            """---
type: incident
name: API outage after deploy
severity: high
preserve: false
---
# Incident body

Production API returned HTTP 500 after deploy.
""",
        )
        _write(
            reports / "postdeploy_failure.md",
            """---
type: incident
name: Background jobs double-fired
severity: critical
---
Workers created duplicate external jobs.
""",
        )
        _write(
            reports / "malformed_type.md",
            """---
type: [incident]
severity: critical
---
This must not become an incident.
""",
        )
        _write(
            reports / "incident_conflicting_audit.md",
            """---
type: audit
severity: high
---
This incident filename must not be indexed as audit.
""",
        )
        _write(reports / "consilium_legacy.md", "# Legacy consilium\n\nDecision kept.")
        _write(reports / "audit_legacy.md", "# Legacy audit\n\nRisk found.")

        first = index_reports.index_all(dry_run=False)
        second = index_reports.index_all(dry_run=False)
        rows = _rows(rolling_memory.DB_PATH)

        incidents = [r for r in rows if r["memory_type"] == "incident"]
        consilia = [r for r in rows if r["memory_type"] == "consilium"]
        audits = [r for r in rows if r["memory_type"] == "audit"]

        errors: list[str] = []
        if first != (4, 2, 0):
            errors.append(f"first index tuple={first}, expected (4, 2, 0)")
        if second != (4, 2, 0):
            errors.append(f"second index tuple={second}, expected (4, 2, 0)")
        if len(rows) != 4:
            errors.append(f"active rows={len(rows)}, expected 4 after idempotent reindex")
        if len(incidents) != 2:
            errors.append(f"incidents={len(incidents)}, expected 2")
        if any(r["preserve"] != 1 for r in incidents):
            errors.append("incident preserve flag was not forced to 1")
        if any(r["priority"] != 95 for r in incidents):
            errors.append("incident priority was not 95")
        if not all('"severity"' in r["metadata_json"] for r in incidents):
            errors.append("incident severity metadata missing")
        if any("malformed_type" in r["source"] for r in rows):
            errors.append("malformed non-string type was indexed")
        if any("incident_conflicting_audit" in r["source"] for r in rows):
            errors.append("conflicting filename/frontmatter type was indexed")
        if len(consilia) != 1 or len(audits) != 1:
            errors.append(f"consilium/audit regression: consilia={len(consilia)} audits={len(audits)}")
        if not all(Path(r["source"]).is_absolute() for r in rows):
            errors.append("source paths are not canonical absolute paths")
        if not all(r["idempotency_key"].startswith("report:/") for r in rows):
            errors.append("idempotency key is not canonical report:<absolute-path>")

        if errors:
            _fail(label, "; ".join(errors))
        else:
            _ok(label)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_memory_post_tool_report_regex_includes_incidents() -> None:
    label = "memory_post_tool auto-index regex includes incident reports"
    memory_post_tool = _load_memory_post_tool()
    pattern = memory_post_tool._REPORT_WRITE_PATTERN

    errors: list[str] = []
    for path in (
        "/Users/dmitrijnazarov/Projects/app/reports/consilium_2026-06-01_topic.md",
        "/Users/dmitrijnazarov/Projects/app/reports/audit_2026-06-01_topic.md",
        "/Users/dmitrijnazarov/Projects/app/reports/incident_2026-06-01_outage.md",
    ):
        if not pattern.search(path):
            errors.append(f"expected match for {path}")
    for path in (
        "/Users/dmitrijnazarov/Projects/app/reports/handover_2026-06-01.md",
        "/Users/dmitrijnazarov/Projects/app/incidents/incident_2026-06-01_outage.md",
    ):
        if pattern.search(path):
            errors.append(f"unexpected match for {path}")

    if errors:
        _fail(label, "; ".join(errors))
    else:
        _ok(label)


def test_start_context_and_context_ordering() -> None:
    label = "start-context incident register and context ordering"
    home = Path(tempfile.mkdtemp(prefix="cb_incident_context_"))
    try:
        rolling_memory, index_reports = _load_modules(home)
        reports = home / "Projects" / "app" / "reports"
        _write(
            reports / "incident_medium.md",
            """---
type: incident
name: Medium newer incident
severity: medium
---
Newer but lower severity.
""",
        )
        _write(
            reports / "incident_critical.md",
            """---
type: incident
name: Critical older incident
severity: critical
---
Older but more severe.
""",
        )
        _write(
            reports / "incident_high.md",
            """---
type: incident
name: High middle incident
severity: high
---
Middle severity.
""",
        )
        _write(reports / "consilium_context.md", "# Context consilium\n\nDecision.")
        _write(reports / "audit_context.md", "# Context audit\n\nAudit.")
        other_reports = home / "Projects" / "other" / "reports"
        _write(
            other_reports / "incident_other_critical.md",
            """---
type: incident
name: Other critical incident
severity: critical
---
Unrelated critical incident.
""",
        )
        _write(
            other_reports / "incident_query_low.md",
            """---
type: incident
name: Query-matching low incident
severity: low
---
needlecache query-specific unrelated incident.
""",
        )

        indexed = index_reports.index_all(dry_run=False)
        if indexed != (7, 0, 0):
            _fail(label, f"index tuple={indexed}, expected (7, 0, 0)")
            return

        conn = sqlite3.connect(str(rolling_memory.DB_PATH))
        try:
            conn.execute(
                "UPDATE agent_memory SET created_at = ? WHERE source LIKE ?",
                ("2026-06-01T00:00:00Z", "%incident_critical.md"),
            )
            conn.execute(
                "UPDATE agent_memory SET created_at = ? WHERE source LIKE ?",
                ("2026-06-03T00:00:00Z", "%incident_high.md"),
            )
            conn.execute(
                "UPDATE agent_memory SET created_at = ? WHERE source LIKE ?",
                ("2026-06-05T00:00:00Z", "%incident_medium.md"),
            )
            conn.execute(
                "UPDATE agent_memory SET created_at = ? WHERE source LIKE ?",
                ("2026-06-06T00:00:00Z", "%incident_other_critical.md"),
            )
            conn.execute(
                "UPDATE agent_memory SET created_at = ? WHERE source LIKE ?",
                ("2026-06-07T00:00:00Z", "%incident_query_low.md"),
            )
            conn.commit()
        finally:
            conn.close()

        start = rolling_memory.build_start_context(scope=str(home / "Projects" / "app"), limit=10)
        query_start = rolling_memory.build_start_context(
            scope=str(home / "Projects" / "app"),
            query="needlecache",
            limit=10,
        )
        rolling_memory.memorize(
            "A deploy failed because cache invalidation was skipped.",
            memory_type="error_lesson",
            category="deploy-cicd",
            scope="global",
        )
        context = rolling_memory.build_context(scope="global", token_budget=4000)

        errors: list[str] = []
        if "=== INCIDENT REGISTER ===" not in start:
            errors.append("start-context missing INCIDENT REGISTER")
        if "=== KNOWLEDGE BASE" not in start:
            errors.append("start-context missing KNOWLEDGE BASE regression block")
        critical_pos = start.find("Critical older incident")
        high_pos = start.find("High middle incident")
        medium_pos = start.find("Medium newer incident")
        other_critical_pos = start.find("Other critical incident")
        if not (0 <= critical_pos < high_pos < medium_pos):
            errors.append("incident severity sort is not critical > high > medium")
        if not (0 <= medium_pos < other_critical_pos):
            errors.append("current-project incidents did not outrank unrelated critical incident")
        query_low_pos = query_start.find("Query-matching low incident")
        query_other_critical_pos = query_start.find("Other critical incident")
        if not (0 <= query_low_pos < query_other_critical_pos):
            errors.append("query-matching incident did not outrank unrelated non-query incident")
        incident_context_pos = context.find("=== INCIDENT WARNINGS ===")
        error_context_pos = context.find("=== ERROR LESSONS ===")
        if not (0 <= incident_context_pos < error_context_pos):
            errors.append("context does not place INCIDENT WARNINGS before ERROR LESSONS")
        if "incident/app" not in context:
            errors.append("context incident line missing category")

        if errors:
            _fail(label, "; ".join(errors))
        else:
            _ok(label)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def main() -> int:
    test_incident_indexing_and_regression()
    test_memory_post_tool_report_regex_includes_incidents()
    test_start_context_and_context_ordering()
    print(f"\nSummary: passed={passed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
