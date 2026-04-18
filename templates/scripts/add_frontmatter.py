#!/usr/bin/env python3
"""Add/enhance YAML frontmatter on memory files and consilium/audit/handover reports.

Purpose:
    Adds missing fields (project, date, scope, preserve) to existing frontmatter.
    Adds full frontmatter to files without it.
    Skips MEMORY.md index files.

Contract:
    Input: --dry-run flag for preview, --run for actual changes
    Output: summary of changes made

Usage:
    python3 add_frontmatter.py --dry-run   # preview changes
    python3 add_frontmatter.py --run       # apply changes
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml


def parse_frontmatter(content: str) -> tuple[dict | None, str]:
    """Parse YAML frontmatter from markdown content. Returns (metadata, body)."""
    if not content.startswith("---"):
        return None, content
    end = content.find("---", 3)
    if end == -1:
        return None, content
    try:
        meta = yaml.safe_load(content[3:end])
        if not isinstance(meta, dict):
            return None, content
        body = content[end + 3:].lstrip("\n")
        return meta, body
    except yaml.YAMLError:
        return None, content


def build_frontmatter(meta: dict) -> str:
    """Build YAML frontmatter string from dict."""
    lines = ["---"]
    for key in ["name", "description", "type", "project", "date", "scope", "preserve"]:
        if key in meta:
            val = meta[key]
            if isinstance(val, bool):
                lines.append(f"{key}: {'true' if val else 'false'}")
            else:
                # Quote strings that might need it
                if isinstance(val, str) and (":" in val or '"' in val or "'" in val):
                    lines.append(f'{key}: "{val}"')
                else:
                    lines.append(f"{key}: {val}")
    # Any extra keys
    for key, val in meta.items():
        if key not in ["name", "description", "type", "project", "date", "scope", "preserve"]:
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def infer_project_from_path(filepath: str) -> str:
    """Infer project name from file path."""
    path = filepath.lower()
    # From memory paths like ~/.claude/projects/-Users-dmitrijnazarov-Projects-AINEWS/memory/
    m = re.search(r"projects[/-]+([\w-]+?)[/-]+memory", path, re.IGNORECASE)
    if m:
        name = m.group(1)
        # Clean up common prefixes
        for prefix in ["users-dmitrijnazarov-projects-", "users-dmitrijnazarov-"]:
            if name.startswith(prefix):
                name = name[len(prefix):]
        return name.lower().rstrip("-") or "global"
    # From project paths like ~/Projects/AINEWS/reports/
    m = re.search(r"Projects/([^/]+)/", filepath)
    if m:
        return m.group(1).lower()
    return "global"


def infer_date_from_file(filepath: str) -> str:
    """Infer date from filename or mtime."""
    # Try filename patterns: YYYY-MM-DD, YYYY_MM_DD
    m = re.search(r"(\d{4}[-_]\d{2}[-_]\d{2})", os.path.basename(filepath))
    if m:
        return m.group(1).replace("_", "-")
    # Fall back to mtime
    mtime = os.path.getmtime(filepath)
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def infer_type_from_path(filepath: str) -> str:
    """Infer type from filename."""
    base = os.path.basename(filepath).lower()
    if "consilium" in base:
        return "consilium"
    if "audit" in base:
        return "audit"
    if "handover" in base:
        return "handover"
    if base.startswith("feedback_"):
        return "feedback"
    if base.startswith("project_"):
        return "project"
    if base.startswith("reference_"):
        return "reference"
    return "memory"


def process_file(filepath: str, dry_run: bool) -> str | None:
    """Process a single file. Returns description of change or None if no change."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    meta, body = parse_frontmatter(content)
    changed = False

    if meta is None:
        # No frontmatter — create it
        meta = {}
        base = os.path.basename(filepath)
        meta["name"] = base.replace(".md", "").replace("_", " ").title()
        meta["description"] = f"Auto-generated frontmatter for {base}"
        changed = True

    # Ensure required fields
    ftype = infer_type_from_path(filepath)
    if "type" not in meta:
        meta["type"] = ftype
        changed = True
    else:
        ftype = meta["type"]

    if "project" not in meta:
        meta["project"] = infer_project_from_path(filepath)
        changed = True

    if "date" not in meta:
        meta["date"] = infer_date_from_file(filepath)
        changed = True

    if "scope" not in meta:
        meta["scope"] = "global" if ftype in ("consilium", "audit") else "project"
        changed = True

    if "preserve" not in meta:
        meta["preserve"] = ftype in ("consilium", "audit")
        changed = True

    if not changed:
        return None

    # Validate YAML
    new_fm = build_frontmatter(meta)
    try:
        yaml.safe_load(new_fm.replace("---", "").strip())
    except yaml.YAMLError as e:
        return f"SKIP (invalid YAML): {filepath}: {e}"

    if dry_run:
        return f"WOULD UPDATE: {filepath} (+project={meta.get('project')}, +date={meta.get('date')}, +scope={meta.get('scope')}, +preserve={meta.get('preserve')})"

    new_content = new_fm + "\n" + body
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)

    return f"UPDATED: {filepath}"


def find_memory_files() -> list[str]:
    """Find all memory .md files (excluding MEMORY.md indexes)."""
    base = Path.home() / ".claude" / "projects"
    files = []
    for md in base.rglob("memory/*.md"):
        if md.name == "MEMORY.md":
            continue
        files.append(str(md))
    return sorted(files)


def find_report_files() -> list[str]:
    """Find consilium, audit, and handover reports in Projects."""
    projects_dir = Path.home() / "Projects"
    files = []
    for pattern in ["**/reports/consilium_*.md", "**/reports/audit_*.md",
                     "**/audits/**/audit_report.md"]:
        for f in projects_dir.glob(pattern):
            files.append(str(f))
    return sorted(set(files))


def main():
    dry_run = "--dry-run" in sys.argv
    run = "--run" in sys.argv

    if not dry_run and not run:
        print("Usage: python3 add_frontmatter.py --dry-run | --run")
        sys.exit(1)

    memory_files = find_memory_files()
    report_files = find_report_files()

    print(f"Found {len(memory_files)} memory files, {len(report_files)} report files")
    print()

    changes = 0
    skips = 0

    for filepath in memory_files + report_files:
        result = process_file(filepath, dry_run)
        if result:
            print(result)
            if not result.startswith("SKIP"):
                changes += 1
            else:
                skips += 1

    print()
    print(f"{'Would change' if dry_run else 'Changed'}: {changes} files, Skipped: {skips}")


if __name__ == "__main__":
    main()
