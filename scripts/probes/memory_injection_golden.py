#!/usr/bin/env python3
"""Golden-render capture probe for the memory-injection telemetry invariant.

Purpose:
    Prove/verify INV_PRESERVE_RENDER_BYTES: adding telemetry to
    ``rolling_memory.build_context()`` / ``build_start_context()`` must not
    change one byte of the rendered context string, for a FIXED DB state and
    FIXED arguments.

Contract:
    - Inputs:
        --db PATH            Path to the PINNED sqlite DB to render against.
                             (For the empty/absent branch, pass a path that
                             does NOT exist; case (e) exercises the error prose.)
        --out DIR            Directory to write golden/verify render files into.
        --mode {capture-golden, verify-post}
                             capture-golden : write <case>.golden files.
                             verify-post    : write <case>.post files, then the
                                              caller `cmp`s golden vs post.
        --twice              Run every case twice and assert the two renders are
                             byte-identical (determinism check). FATAL on drift.
    - Outputs (files under --out):
        build_context_default.<ext>
        build_context_repo.<ext>
        build_start_query_none.<ext>
        build_start_query_set.<ext>
        build_start_empty_db.<ext>
      where <ext> is `golden` (capture) or `post` (verify).
      Also prints a JSON manifest {case: {sha256, bytes}} to stdout.

    - Exit codes:
        0  all cases rendered (and, with --twice, all deterministic)
        3  nondeterminism detected across two runs (FATAL — invariant unprovable)
        4  monkeypatch of rolling_memory.DB_PATH did NOT redirect reads

DB-safety:
    The probe NEVER renders against --db directly. It copies --db to a
    throwaway working file inside --out and monkeypatches
    ``rolling_memory.DB_PATH`` to that copy. ``build_context`` opens a
    read-WRITE (WAL) connection, so rendering mutates the DB header; landing
    that on a disposable copy keeps the canonical pinned DB byte-stable across
    capture-golden and verify-post runs. For the empty-db case the working
    copy is deliberately absent so ``get_readonly_connection`` raises and the
    error-prose branch is exercised.

CLI / Examples:
    # Capture the golden matrix against the pinned DB
    python scripts/probes/memory_injection_golden.py \
        --db /tmp/claude_booster_memory_telemetry_proof/pinned.db \
        --out /tmp/claude_booster_memory_telemetry_proof/golden \
        --mode capture-golden --twice

    # After the Worker's edit, re-run and compare
    python scripts/probes/memory_injection_golden.py \
        --db /tmp/claude_booster_memory_telemetry_proof/pinned.db \
        --out /tmp/claude_booster_memory_telemetry_proof/post \
        --mode verify-post --twice
    for c in build_context_default build_context_repo build_start_query_none \
             build_start_query_set build_start_empty_db; do
      cmp GOLDEN/$c.golden POST/$c.post || echo "DIVERGENCE: $c"
    done

Limitations:
    - Determinism assumes the pinned DB rows are frozen. SQL ORDER BY ties are
      resolved by SQLite's stable-for-a-given-file plan; the --twice check is
      what actually certifies determinism for THIS pinned DB, not a general
      guarantee across arbitrary DBs.
    - Renders against the code that is importable as ``rolling_memory`` on
      sys.path — by default ``~/.claude/scripts``. Override via --rm-dir.

ENV/Files:
    Reads: the module resolved as ``rolling_memory`` (default
    ``~/.claude/scripts/rolling_memory.py``), and --db (copied, read-only intent).
    Writes: only under --out.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import shutil
import sys
from pathlib import Path

REPO_SCOPE = "/Users/dmitrijnazarov/Projects/Claude_Booster"

# case_id -> (callable_name, kwargs, uses_empty_db)
CASES = [
    ("build_context_default", "build_context", {}, False),
    ("build_context_repo", "build_context", {"scope": REPO_SCOPE, "token_budget": 4000}, False),
    ("build_start_query_none", "build_start_context", {"scope": REPO_SCOPE, "query": None}, False),
    ("build_start_query_set", "build_start_context", {"scope": REPO_SCOPE, "query": "audit"}, False),
    ("build_start_empty_db", "build_start_context", {"scope": REPO_SCOPE, "query": None}, True),
]


def _load_rolling_memory(rm_dir: Path):
    sys.path.insert(0, str(rm_dir))
    return importlib.import_module("rolling_memory")


def _prove_monkeypatch(rm, working_db: Path) -> bool:
    """Empirically confirm DB_PATH monkeypatch redirects reads.

    Sets DB_PATH to a nonexistent path and asserts build_start_context returns
    the 'DB not initialized' error prose (proving get_readonly_connection reads
    the module global at call time). Then restores.
    """
    absent = working_db.parent / "___definitely_absent_probe.db"
    if absent.exists():
        absent.unlink()
    saved = rm.DB_PATH
    try:
        rm.DB_PATH = absent
        out = rm.build_start_context(scope=REPO_SCOPE, query=None)
        return "DB not initialized" in out or "unable to open" in out.lower()
    finally:
        rm.DB_PATH = saved


def _render_case(rm, case_id, fn_name, kwargs, uses_empty_db, out_dir: Path, src_db: Path):
    if uses_empty_db:
        working = out_dir / f"__absent_{case_id}.db"
        if working.exists():
            working.unlink()
        rm.DB_PATH = working  # deliberately nonexistent -> error branch
    else:
        working = out_dir / f"__work_{case_id}.db"
        shutil.copyfile(src_db, working)  # disposable copy; WAL mutation lands here
        rm.DB_PATH = working
    fn = getattr(rm, fn_name)
    return fn(**kwargs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--mode", choices=["capture-golden", "verify-post"], required=True)
    ap.add_argument("--twice", action="store_true")
    ap.add_argument("--rm-dir", type=Path, default=Path.home() / ".claude" / "scripts")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    rm = _load_rolling_memory(args.rm_dir)

    # P0 receipt: prove monkeypatch actually redirects reads.
    if not _prove_monkeypatch(rm, args.out / "probe.db"):
        print("FATAL: monkeypatch of rolling_memory.DB_PATH did NOT redirect reads",
              file=sys.stderr)
        return 4

    ext = "golden" if args.mode == "capture-golden" else "post"
    manifest = {}
    for case_id, fn_name, kwargs, uses_empty in CASES:
        r1 = _render_case(rm, case_id, fn_name, kwargs, uses_empty, args.out, args.db)
        if args.twice:
            r2 = _render_case(rm, case_id, fn_name, kwargs, uses_empty, args.out, args.db)
            if r1 != r2:
                print(f"FATAL nondeterminism in {case_id}: two runs differ", file=sys.stderr)
                (args.out / f"{case_id}.run1").write_text(r1, encoding="utf-8")
                (args.out / f"{case_id}.run2").write_text(r2, encoding="utf-8")
                return 3
        data = r1.encode("utf-8")
        (args.out / f"{case_id}.{ext}").write_bytes(data)
        manifest[case_id] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
