#!/usr/bin/env python3
"""
backup_rolling_memory.py — safe SQLite backup with rolling retention.

Purpose:
    Create a timestamped backup of rolling_memory.db (or any SQLite DB)
    using the official SQLite backup API, then prune older backups so
    that exactly `retention` files remain (newest first by mtime).

Contract:
    Inputs:
        --db PATH        Source SQLite DB. Default: ~/.claude/rolling_memory.db
        --retention N    Number of backup files to keep (>=1). Default: 2.
    Outputs:
        Creates {db}.bak_{YYYYMMDD_HHMMSS}, prunes older {db}.bak* files.
        Prints the new backup path on success and lists the surviving set.
    Exit codes:
        0 on success, 1 on usage / file errors.

CLI / Examples:
    python3 ~/.claude/scripts/backup_rolling_memory.py
    python3 ~/.claude/scripts/backup_rolling_memory.py --retention 3
    python3 ~/.claude/scripts/backup_rolling_memory.py --db /tmp/foo.db --retention 1

Limitations:
    - Retention is enforced by mtime, not by filename pattern. A backup
      whose mtime is touched after creation will be reordered.
    - The pruning glob is `{db_basename}.bak*`, which intentionally
      matches the bare `.bak` (no timestamp) variant as well.
    - Uses SQLite's native backup API, which handles concurrent writers
      correctly. Plain `cp` of a live DB is not safe and is avoided.

Files:
    Reads:  $DB
    Writes: $DB.bak_<ts>
    Deletes: older $DB.bak* beyond `retention`
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path.home() / ".claude" / "rolling_memory.db"
DEFAULT_RETENTION = 2


def create_backup(db_path: Path, retention: int = DEFAULT_RETENTION) -> Path:
    if retention < 1:
        raise ValueError(f"retention must be >= 1, got {retention}")
    if not db_path.exists():
        raise FileNotFoundError(f"source DB not found: {db_path}")
    if not db_path.is_file():
        raise ValueError(f"source DB is not a regular file: {db_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = db_path.with_name(f"{db_path.name}.bak_{timestamp}")

    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(bak_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    _prune(db_path, retention)
    return bak_path


def _prune(db_path: Path, retention: int) -> list[Path]:
    parent = db_path.parent
    prefix = f"{db_path.name}.bak"
    candidates = sorted(
        (p for p in parent.glob(f"{prefix}*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    keep = candidates[:retention]
    drop = candidates[retention:]
    for p in drop:
        p.unlink()
    return keep


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backup a SQLite DB with rolling retention.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"source SQLite DB (default: {DEFAULT_DB})")
    parser.add_argument("--retention", type=int, default=DEFAULT_RETENTION,
                        help=f"backups to keep (default: {DEFAULT_RETENTION})")
    args = parser.parse_args()

    try:
        new_bak = create_backup(args.db.expanduser(), args.retention)
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parent = args.db.expanduser().parent
    prefix = f"{args.db.expanduser().name}.bak"
    survivors = sorted(
        (p for p in parent.glob(f"{prefix}*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    print(f"created: {new_bak}")
    print(f"retained {len(survivors)} backup(s):")
    for p in survivors:
        size_kb = p.stat().st_size // 1024
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {p.name}  {size_kb}K  {mtime}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
