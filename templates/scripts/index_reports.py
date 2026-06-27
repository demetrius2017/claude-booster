#!/usr/bin/env python3
"""Ingest consilium/audit/incident reports into rolling_memory as searchable rows.

Purpose
-------
Walks ``~/Projects/*/reports/*.md`` plus a shallow ``audits/**`` fallback,
parses YAML frontmatter, and upserts each consilium/audit/incident file via
``rolling_memory.memorize(..., idempotency_key="report:<abspath>")``. Reports
are stored as first-class memory rows so ``/start`` and ``search()`` can rank
them alongside directives, feedback, and error lessons.

Contract
--------
Input  : none — walks filesystem.
Output : stdout log of inserted/updated/skipped rows.
Exit   : 0 on success, non-zero on fatal error. Individual parse failures are
         logged but do not abort the run.

CLI
---
    python ~/.claude/scripts/index_reports.py              # real ingestion
    python ~/.claude/scripts/index_reports.py --dry-run    # parse + report, no DB write

Limitations
-----------
- Reports without YAML frontmatter are still indexed *if* the filename begins
  with ``consilium_``, ``audit_``, or ``incident_``; the memory_type is inferred from the
  prefix. Files that have neither valid frontmatter nor a matching prefix are
  skipped with a warning. This fallback lets us index legacy reports that
  predate the frontmatter convention.
- Incident reports are always indexed with ``preserve=True`` and
  ``memory_type='incident'``. They are not error lessons.
- Bodies are truncated to 8000 chars (leaves headroom for FTS rank weights).
- Idempotency is keyed on absolute path, so renaming a report creates a new row.
- Report discovery is fixed-depth: ``~/Projects/*/reports/*``,
  ``~/Projects/*/*/reports/*``, and ``~/Projects/*/audits/*/audit_report.md``.
  A project nested more than two levels under ``~/Projects`` is silently missed.

ENV / Files
-----------
- Reads  : ``~/Projects/*/reports/*.md``, ``~/Projects/*/audits/**/*.md``
- Writes : ``~/.claude/rolling_memory.db`` (via rolling_memory.memorize)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import yaml

# Make rolling_memory importable regardless of cwd.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import rolling_memory  # noqa: E402

logger = logging.getLogger("index_reports")

PROJECTS_ROOT = Path.home() / "Projects"
BODY_MAX_CHARS = 8000
FRONTMATTER_DELIM = "---"


def _iter_report_files() -> list[Path]:
    """Return all markdown report files that may carry report frontmatter."""
    if not PROJECTS_ROOT.is_dir():
        return []
    patterns = [
        "*/reports/*.md",
        "*/*/reports/*.md",
        "*/reports/consilium_*.md",
        "*/reports/audit_*.md",
        "*/reports/incident_*.md",
        "*/*/reports/consilium_*.md",
        "*/*/reports/audit_*.md",
        "*/*/reports/incident_*.md",
    ]
    found: list[Path] = []
    for pat in patterns:
        found.extend(PROJECTS_ROOT.glob(pat))
    # Some projects use audits/<topic>/audit_report.md — pick those up too.
    found.extend(PROJECTS_ROOT.glob("*/audits/*/audit_report.md"))
    # Dedupe while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in found:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(rp)
    return sorted(unique)


def _split_frontmatter(text: str) -> tuple[Optional[dict], str]:
    """Return (frontmatter_dict, body) or (None, text) if no valid frontmatter."""
    if not text.startswith(FRONTMATTER_DELIM):
        return None, text
    # Find the closing delimiter on its own line.
    lines = text.splitlines()
    if len(lines) < 2 or lines[0].strip() != FRONTMATTER_DELIM:
        return None, text
    close_idx: Optional[int] = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIM:
            close_idx = i
            break
    if close_idx is None:
        return None, text
    fm_text = "\n".join(lines[1:close_idx])
    body = "\n".join(lines[close_idx + 1:])
    try:
        data = yaml.safe_load(fm_text) or {}
        if not isinstance(data, dict):
            return None, text
        return data, body
    except yaml.YAMLError as exc:
        logger.warning("malformed frontmatter in report: %s", exc)
        return None, text


def _infer_type_from_name(path: Path) -> Optional[str]:
    name = path.name
    if name.startswith("consilium"):
        return "consilium"
    if name.startswith("audit"):
        return "audit"
    if name.startswith("incident"):
        return "incident"
    return None


def _project_category(path: Path) -> str:
    """Return the project directory name closest to the report file.

    For nested layouts like ``~/Projects/umbrella/subproject/reports/x.md``
    we want ``subproject`` (the immediate parent of ``reports/``), not the
    top-level ``umbrella`` directory. For layouts using
    ``audits/<topic>/audit_report.md`` we walk past the ``audits`` segment
    the same way.
    """
    try:
        rel = path.resolve().relative_to(PROJECTS_ROOT.resolve())
    except ValueError:
        return ""
    parts = rel.parts
    for marker in ("reports", "audits"):
        if marker in parts:
            idx = parts.index(marker)
            if idx > 0:
                return parts[idx - 1]
    return parts[0] if parts else ""


def _string_field(value: object) -> str:
    """Coerce a YAML frontmatter field to a stripped string.

    Returns ``""`` for None, lists, dicts, ints, or anything else that isn't
    a string. Prevents ``AttributeError`` when a report file has a malformed
    field like ``type: [audit]`` or ``description: 123``.
    """
    return value.strip() if isinstance(value, str) else ""


def _frontmatter_type(fm: dict, path: Path) -> str:
    """Return a valid memory type from YAML frontmatter, or ``""``.

    Non-string ``type`` values are rejected rather than coerced. Filename
    fallback still handles legacy consilium/audit/incident reports.
    """
    if "type" not in fm:
        return ""
    raw_type = fm.get("type")
    if not isinstance(raw_type, str):
        logger.warning("malformed non-string frontmatter type in %s — ignoring", path)
        return ""
    ftype = raw_type.strip().lower()
    return ftype if ftype in ("consilium", "audit", "incident") else ""


def build_row(path: Path) -> Optional[dict]:
    """Parse a report file into the kwargs for rolling_memory.memorize()."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("cannot read %s: %s", path, exc)
        return None

    fm, body = _split_frontmatter(text)
    inferred_type = _infer_type_from_name(path)
    memory_type = None
    description = ""
    name = path.stem
    fm_preserve: Optional[bool] = None

    if fm is not None:
        ftype = _frontmatter_type(fm, path)
        if inferred_type and ftype and inferred_type != ftype:
            logger.warning(
                "conflicting report type for %s: filename implies %r, frontmatter says %r — skipping",
                path,
                inferred_type,
                ftype,
            )
            return None
        if ftype:
            memory_type = ftype
        description = _string_field(fm.get("description"))
        fm_name = _string_field(fm.get("name"))
        if fm_name:
            name = fm_name
        raw_preserve = fm.get("preserve")
        if isinstance(raw_preserve, bool):
            fm_preserve = raw_preserve
        elif isinstance(raw_preserve, str):
            fm_preserve = raw_preserve.strip().lower() in ("true", "yes", "1")

    if memory_type is None:
        memory_type = inferred_type
    if memory_type is None:
        logger.warning("no memory_type for %s — skipping", path)
        return None

    # Phase 2c: preserve defaults to True for consilium/audit even when the
    # frontmatter field is missing (e.g. legacy files, malformed YAML).
    # Incidents are also source-of-truth rows and are always preserved; an
    # incident report must not be consolidated away or evicted.
    # Explicit `preserve: false` in frontmatter still overrides consilium/audit.
    # Incidents are always preserved because they are post-deploy production
    # safety records. This also covers the YAML colon-quote bug class that
    # already cost us one fix commit.
    if memory_type == "incident":
        preserve = True
    elif fm_preserve is None:
        preserve = memory_type in ("consilium", "audit")
    else:
        preserve = fm_preserve

    severity = ""
    if fm is not None:
        severity = _string_field(fm.get("severity")).lower()
    if severity not in ("critical", "high", "medium", "low"):
        severity = "unknown"

    # Build the indexed content: title line + optional description + truncated body.
    pieces: list[str] = [f"# {name}"]
    if description:
        pieces.append(description)
    pieces.append(body.strip())
    content = "\n\n".join(p for p in pieces if p)
    if len(content) > BODY_MAX_CHARS:
        content = content[:BODY_MAX_CHARS] + "\n[...truncated for FTS indexing...]"

    return {
        "content": content,
        "memory_type": memory_type,
        "priority": 95 if memory_type == "incident" else 70,
        "scope": "global",
        "category": _project_category(path),
        "source": str(path.resolve()),
        "idempotency_key": f"report:{path.resolve()}",
        "preserve": preserve,
        "metadata": {"severity": severity} if memory_type == "incident" else {},
    }


def index_all(dry_run: bool = False) -> tuple[int, int, int]:
    """Ingest all discovered reports. Returns (indexed, skipped, errors)."""
    # --dry-run must not touch the DB at all. init_db() can run a schema
    # migration (v1→v2 ALTER, v2→v3 FTS rebuild) which would mutate the file.
    if not dry_run:
        rolling_memory.init_db()
    files = _iter_report_files()
    indexed = skipped = errors = 0

    for path in files:
        try:
            kwargs = build_row(path)
        except Exception as exc:  # noqa: BLE001 — per-file isolation
            errors += 1
            logger.exception("build_row failed for %s", path)
            print(f"[ERR]  {path.name} — parse failure: {exc}")
            continue
        if kwargs is None:
            skipped += 1
            continue
        if dry_run:
            print(
                f"[DRY] {kwargs['memory_type']:9s} "
                f"cat={kwargs['category']:25s} "
                f"len={len(kwargs['content']):5d} "
                f"{path.name}"
            )
            indexed += 1
            continue
        try:
            row_id = rolling_memory.memorize(**kwargs)
            if row_id is None:
                # memorize returns None on duplicate content_hash without
                # idempotency_key path — but we pass idempotency_key, so None
                # here means something else (exception logged by memorize()).
                errors += 1
                print(f"[ERR]  {path.name} — memorize returned None")
            else:
                indexed += 1
                print(f"[OK]   #{row_id} {kwargs['memory_type']} {path.name}")
        except Exception as exc:  # noqa: BLE001 — we want to continue on per-file failures
            errors += 1
            logger.exception("memorize failed for %s", path)
            print(f"[ERR]  {path.name} — {exc}")

    return indexed, skipped, errors


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true", help="Parse and report without writing")
    args = ap.parse_args()

    indexed, skipped, errors = index_all(dry_run=args.dry_run)
    label = "DRY-RUN" if args.dry_run else "INDEX"
    print(f"\n{label} summary: indexed={indexed} skipped={skipped} errors={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
