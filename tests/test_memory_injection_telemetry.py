#!/usr/bin/env python3
"""Executable acceptance test for the memory-injection telemetry feature.

Purpose:
    ONE acceptance harness that certifies the Artifact Contract for
    "instrument memory-injection telemetry" against OBSERVABLE behavior only.
    It never reads the Worker's implementation; it drives the public functions
    (``rolling_memory.build_context`` / ``build_start_context``) and the new
    ``memory_telemetry.py report`` CLI, then asserts on rendered bytes, the
    append-only JSONL log rows, and the report JSON.

Contract (inputs/outputs):
    - No inputs. Reads the DEPLOYED scripts under ~/.claude/scripts, the
      repo templates, the pinned proof DB + goldens, and the probe.
    - Emits ``[PASS] <case>: ...`` / ``[FAIL] <case>: ... — expected X, got Y``
      lines, ends with ``Results: N passed, M failed``.
    - Exit 0 iff ALL cases pass; non-zero if ANY fail.

CLI / Examples:
    python3 tests/test_memory_injection_telemetry.py

Limitations:
    - Requires the Prototype-stage proof artifacts under
      /tmp/claude_booster_memory_telemetry_proof to exist (pinned.db + golden/).
    - The core preservation/determinism gate is delegated to the frozen probe
      scripts/probes/memory_injection_golden.py — this test runs it and
      byte-compares its output against the pre-edit goldens.
    - Telemetry cases run each render in an isolated subprocess with HOME +
      CLAUDE_HOME redirected to a throwaway dir, so the log lands in a temp
      location. The real ~/.claude/logs/memory_injection.jsonl is also watched
      as a fallback (in case the Worker anchored the path at the real home);
      line-count deltas across BOTH candidates are summed.

DB-safety:
    NEVER touches ~/.claude/rolling_memory.db. All renders run against a copy
    of the pinned DB, an absent path, or a temp empty-schema DB. All perm/rename
    mutations of deployed files are restored in ``finally``.

ENV/Files:
    Reads: deployed rolling_memory.py + memory_telemetry.py, templates/scripts/*,
    pinned.db, golden/*, probe. Writes: only under a per-run tempdir.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ----------------------------------------------------------------------------
# Fixed locations (Artifact Contract + proof harness)
# ----------------------------------------------------------------------------
REAL_HOME = Path.home()
REAL_SCRIPTS = REAL_HOME / ".claude" / "scripts"
DEPLOYED_RM = REAL_SCRIPTS / "rolling_memory.py"
DEPLOYED_TEL = REAL_SCRIPTS / "memory_telemetry.py"

REPO = Path("/Users/dmitrijnazarov/Projects/Claude_Booster")
TEMPLATE_RM = REPO / "templates" / "scripts" / "rolling_memory.py"
TEMPLATE_TEL = REPO / "templates" / "scripts" / "memory_telemetry.py"
PROBE = REPO / "scripts" / "probes" / "memory_injection_golden.py"

PROOF = Path("/tmp/claude_booster_memory_telemetry_proof")
PINNED_DB = PROOF / "pinned.db"
GOLDEN_DIR = PROOF / "golden"
PINNED_SHA = "f2a479453aa17e8af6c0014453f6afbd911baeec44c6241b86a14a3b816f593c"

REPO_SCOPE = str(REPO)

# case_id -> (fn_name, kwargs, uses_absent_db) — mirrors the probe's CASES.
GOLDEN_CASES = {
    "build_context_default": ("build_context", {}, False),
    "build_context_repo": ("build_context", {"scope": REPO_SCOPE, "token_budget": 4000}, False),
    "build_start_query_none": ("build_start_context", {"scope": REPO_SCOPE, "query": None}, False),
    "build_start_query_set": ("build_start_context", {"scope": REPO_SCOPE, "query": "audit"}, False),
    "build_start_empty_db": ("build_start_context", {"scope": REPO_SCOPE, "query": None}, True),
}

REQUIRED_KEYS = {
    "ts_utc", "session_id", "project_root", "source",
    "memory_ids", "memory_types", "row_count", "char_count", "token_estimate",
}
ALLOWED_SOURCES = {"build_context", "build_start_context"}

# ----------------------------------------------------------------------------
# Result bookkeeping
# ----------------------------------------------------------------------------
_RESULTS: list[tuple[bool, str, str]] = []
base_out_dir: Path = Path("/tmp")  # set in main() to the per-run tempdir


def record(passed: bool, case: str, desc: str) -> None:
    _RESULTS.append((passed, case, desc))
    tag = "PASS" if passed else "FAIL"
    print(f"[{tag}] {case}: {desc}")


def fail(case: str, desc: str, expected, got) -> None:
    _RESULTS.append((False, case, desc))
    print(f"[FAIL] {case}: {desc} — expected {expected!r}, got {got!r}")


# ----------------------------------------------------------------------------
# Driver: run one render in an isolated subprocess with redirected HOME.
# ----------------------------------------------------------------------------
_DRIVER_SRC = r'''
import sys, json
from pathlib import Path
scripts_dir, fn_name, kwargs_json, db_path, out_path, init_empty = sys.argv[1:7]
sys.path.insert(0, scripts_dir)
import rolling_memory as rm
rm.DB_PATH = Path(db_path)
if init_empty == "1":
    rm.init_db()
fn = getattr(rm, fn_name)
out = fn(**json.loads(kwargs_json))
Path(out_path).write_text(out, encoding="utf-8")
sys.stdout.write(json.dumps({"char_len": len(out)}))
'''


def _make_env(home: Path, extra: dict | None = None) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_HOME"] = str(home / ".claude")
    env.pop("CLAUDE_BOOSTER_SKIP_MEMORY_TELEMETRY", None)
    if extra:
        env.update(extra)
    return env


def _candidate_logs(home: Path) -> list[Path]:
    paths = [
        str(home / ".claude" / "logs" / "memory_injection.jsonl"),
        str(REAL_HOME / ".claude" / "logs" / "memory_injection.jsonl"),
    ]
    # Dedup (real-HOME drives make both entries identical) so _new_lines does
    # not double-count the same appended row.
    return [Path(s) for s in dict.fromkeys(paths)]


def _snapshot(paths: list[Path]) -> dict[str, int]:
    snap = {}
    for p in paths:
        try:
            snap[str(p)] = sum(1 for _ in p.open("r", encoding="utf-8"))
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            snap[str(p)] = 0
    return snap


def _new_lines(paths: list[Path], before: dict[str, int]) -> list[str]:
    out: list[str] = []
    for p in paths:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
            continue
        out.extend(lines[before.get(str(p), 0):])
    return out


class DriveResult:
    def __init__(self, rc, render_bytes, char_len, new_rows, stderr):
        self.rc = rc
        self.render_bytes = render_bytes
        self.char_len = char_len
        self.new_rows = new_rows  # list[dict] parsed
        self.new_raw = None
        self.stderr = stderr


def drive(driver_path: Path, home: Path, fn_name: str, kwargs: dict,
          db_path: Path, init_empty: bool = False,
          extra_env: dict | None = None, out_dir: Path | None = None,
          use_real_home: bool = False) -> DriveResult:
    """Run one render; return exit code, rendered bytes, and appended log rows.

    ``use_real_home=True`` runs under the real HOME (no redirect) — required for
    build_start_context, whose incident-register render is HOME-sensitive and
    must match the goldens captured under the real HOME. The appended row is
    still captured via the real-log candidate in ``_candidate_logs``.
    """
    watch_home = REAL_HOME if use_real_home else home
    out_dir = out_dir or (base_out_dir if use_real_home else home)
    out_file = out_dir / f"render_{fn_name}_{os.urandom(4).hex()}.txt"
    logs = _candidate_logs(watch_home)
    before = _snapshot(logs)
    if use_real_home:
        env = os.environ.copy()
        env.pop("CLAUDE_BOOSTER_SKIP_MEMORY_TELEMETRY", None)
        if extra_env:
            env.update(extra_env)
    else:
        env = _make_env(home, extra_env)
    proc = subprocess.run(
        [sys.executable, str(driver_path), str(REAL_SCRIPTS), fn_name,
         json.dumps(kwargs), str(db_path), str(out_file), "1" if init_empty else "0"],
        env=env, capture_output=True, text=True,
    )
    logs = _candidate_logs(watch_home)
    raw_new = _new_lines(logs, before)
    parsed = []
    for ln in raw_new:
        try:
            parsed.append(json.loads(ln))
        except json.JSONDecodeError:
            parsed.append({"__unparseable__": ln})
    render_bytes = out_file.read_bytes() if out_file.exists() else None
    char_len = None
    try:
        char_len = json.loads(proc.stdout)["char_len"]
    except Exception:
        pass
    r = DriveResult(proc.returncode, render_bytes, char_len, parsed, proc.stderr)
    r.new_raw = raw_new
    return r


# ----------------------------------------------------------------------------
# Preconditions
# ----------------------------------------------------------------------------
def check_preconditions() -> bool:
    ok = True
    for p, label in [
        (DEPLOYED_RM, "deployed rolling_memory.py"),
        (DEPLOYED_TEL, "deployed memory_telemetry.py"),
        (TEMPLATE_RM, "template rolling_memory.py"),
        (TEMPLATE_TEL, "template memory_telemetry.py"),
        (PROBE, "golden probe"),
        (PINNED_DB, "pinned DB"),
        (GOLDEN_DIR, "golden dir"),
    ]:
        if not p.exists():
            fail("precondition", f"{label} missing", "exists", str(p))
            ok = False
    if PINNED_DB.exists():
        got = hashlib.sha256(PINNED_DB.read_bytes()).hexdigest()
        if got != PINNED_SHA:
            fail("precondition", "pinned DB sha mismatch", PINNED_SHA, got)
            ok = False
    return ok


# ----------------------------------------------------------------------------
# Cases 1 & 2 — preservation + determinism via the frozen probe
# ----------------------------------------------------------------------------
def case_preserve_and_determinism(base: Path) -> None:
    post = base / "post"
    proc = subprocess.run(
        [sys.executable, str(PROBE), "--db", str(PINNED_DB), "--out", str(post),
         "--mode", "verify-post", "--twice"],
        capture_output=True, text=True,
    )
    if proc.returncode == 3:
        record(False, "still_deterministic", "probe reported nondeterminism (rc=3)")
        record(False, "preserve_render_bytes", "cannot compare — render nondeterministic")
        return
    if proc.returncode == 4:
        record(False, "still_deterministic", "monkeypatch of DB_PATH did not redirect (rc=4)")
        record(False, "preserve_render_bytes", "probe env broken (rc=4)")
        return
    if proc.returncode != 0:
        record(False, "still_deterministic", f"probe rc={proc.returncode}; stderr={proc.stderr.strip()[:200]}")
        record(False, "preserve_render_bytes", "probe did not complete")
        return
    record(True, "still_deterministic", "probe --twice exited 0 post-edit (renders reproducible)")

    diverged = []
    for case_id in GOLDEN_CASES:
        golden = GOLDEN_DIR / f"{case_id}.golden"
        postf = post / f"{case_id}.post"
        if not golden.exists() or not postf.exists():
            diverged.append(f"{case_id}(missing)")
            continue
        if golden.read_bytes() != postf.read_bytes():
            diverged.append(case_id)
    if diverged:
        fail("preserve_render_bytes", "post-edit render diverged from pre-edit golden",
             "byte-identical for all 5 cases", diverged)
    else:
        record(True, "preserve_render_bytes",
               "all 5 rendered cases byte-identical to pre-edit goldens")


# ----------------------------------------------------------------------------
# Cases 3-8, 13 — observed rows from real renders against the pinned DB
# ----------------------------------------------------------------------------
def case_row_observations(base: Path, driver: Path) -> None:
    # ---- Drive A: build_context default ----
    homeA = base / "homeA"
    dbA = base / "copyA.db"
    shutil.copyfile(PINNED_DB, dbA)
    rA = drive(driver, homeA, "build_context", {}, dbA)

    if rA.rc != 0:
        fail("one_row_per_call", "build_context render subprocess crashed", "rc=0",
             f"rc={rA.rc}; {rA.stderr.strip()[:300]}")
        # Cannot proceed with row assertions on a crashed render.
    else:
        # case 3: exactly one row
        n = len(rA.new_rows)
        if n == 1:
            record(True, "one_row_per_call", "build_context appended exactly one JSONL line")
        else:
            fail("one_row_per_call", "build_context row count delta", 1, n)

        if len(rA.new_rows) == 1 and "__unparseable__" not in rA.new_rows[0]:
            row = rA.new_rows[0]
            # case 4: shape
            missing = REQUIRED_KEYS - set(row.keys())
            if not missing and row.get("source") in ALLOWED_SOURCES:
                record(True, "row_shape", "row has all 9 keys; source is valid")
            else:
                fail("row_shape", "row shape/keys", f"keys={sorted(REQUIRED_KEYS)} source in {ALLOWED_SOURCES}",
                     f"missing={sorted(missing)} source={row.get('source')!r}")
            # case 5: ids match count
            if isinstance(row.get("memory_ids"), list) and len(row["memory_ids"]) == row.get("row_count"):
                record(True, "ids_match_count", "len(memory_ids) == row_count")
            else:
                fail("ids_match_count", "len(memory_ids) vs row_count", row.get("row_count"),
                     len(row["memory_ids"]) if isinstance(row.get("memory_ids"), list) else row.get("memory_ids"))
            # case 6: type counts match
            mt = row.get("memory_types")
            if isinstance(mt, dict) and sum(mt.values()) == row.get("row_count"):
                record(True, "type_counts_match", "sum(memory_types.values()) == row_count")
            else:
                fail("type_counts_match", "sum(memory_types) vs row_count", row.get("row_count"),
                     (sum(mt.values()) if isinstance(mt, dict) else mt))
            # case 7 (default): char_count == char length of golden render
            expected_chars = len((GOLDEN_DIR / "build_context_default.golden").read_bytes().decode("utf-8"))
            if row.get("char_count") == expected_chars and rA.char_len == expected_chars:
                record(True, "char_count_matches_golden[default]",
                       f"char_count == rendered chars == {expected_chars}")
            else:
                fail("char_count_matches_golden[default]", "char_count vs golden chars",
                     expected_chars, (row.get("char_count"), rA.char_len))
        else:
            fail("row_shape", "no single parseable row to inspect", "1 parseable row", rA.new_raw)

    # ---- Drive B: build_context repo (char golden) ----
    homeB = base / "homeB"
    dbB = base / "copyB.db"
    shutil.copyfile(PINNED_DB, dbB)
    rB = drive(driver, homeB, "build_context", {"scope": REPO_SCOPE, "token_budget": 4000}, dbB)
    _assert_char_golden(rB, "build_context_repo", "char_count_matches_golden[repo]")

    # ---- Drives C & D: build_start (read-only) + no_db_write snapshot (case 13) ----
    ro_db = base / "ro.db"
    shutil.copyfile(PINNED_DB, ro_db)
    ro_wal = Path(str(ro_db) + "-wal")
    ro_shm = Path(str(ro_db) + "-shm")
    sha_before = hashlib.sha256(ro_db.read_bytes()).hexdigest()
    mtime_before = ro_db.stat().st_mtime

    # build_start_context's incident-register render is HOME-sensitive, so these
    # golden comparisons run under the REAL HOME (matching golden capture).
    homeC = base / "homeC"
    rC = drive(driver, homeC, "build_start_context", {"scope": REPO_SCOPE, "query": None},
               ro_db, use_real_home=True)
    _assert_char_golden(rC, "build_start_query_none", "char_count_matches_golden[start_none]")
    _assert_source(rC, "build_start_context", "start_none_source")

    homeD = base / "homeD"
    rD = drive(driver, homeD, "build_start_context", {"scope": REPO_SCOPE, "query": "audit"},
               ro_db, use_real_home=True)
    _assert_char_golden(rD, "build_start_query_set", "char_count_matches_golden[start_set]")

    sha_after = hashlib.sha256(ro_db.read_bytes()).hexdigest()
    mtime_after = ro_db.stat().st_mtime
    # A WAL-mode *reader* legitimately creates -shm (and a 0-byte -wal) — that is
    # SQLite's shared-memory index, NOT a logical write. The read-only contract
    # is that the DB file CONTENT is untouched: assert sha + mtime unchanged and
    # that any -wal carries no committed frames (0 bytes).
    wal_bytes = ro_wal.stat().st_size if ro_wal.exists() else 0
    if sha_before == sha_after and mtime_before == mtime_after and wal_bytes == 0:
        record(True, "no_db_write_on_start_path",
               "build_start_context left DB sha/mtime unchanged; -wal empty (no committed write)")
    else:
        fail("no_db_write_on_start_path", "start path must not mutate DB content",
             "sha+mtime unchanged, -wal 0 bytes",
             f"sha_eq={sha_before == sha_after} mtime_eq={mtime_before == mtime_after} wal_bytes={wal_bytes}")
    _ = ro_shm  # -shm presence is expected for WAL readers; not asserted

    # ---- Drive E: build_start against ABSENT db -> error-prose early return (case 8a) ----
    homeE = base / "homeE"
    absent = homeE / ".claude" / "___absent_start.db"
    absent.parent.mkdir(parents=True, exist_ok=True)
    rE = drive(driver, homeE, "build_start_context", {"scope": REPO_SCOPE, "query": None}, absent)
    ok8a = (len(rE.new_rows) == 1 and "__unparseable__" not in rE.new_rows[0]
            and rE.new_rows[0].get("source") == "build_start_context")
    # cross-check the render equals the 113B error-prose golden
    egold = (GOLDEN_DIR / "build_start_empty_db.golden").read_bytes()
    if ok8a and rE.render_bytes == egold:
        record(True, "early_return_paths_emit_row[error_prose]",
               "absent-DB error-prose branch emitted exactly one build_start_context row")
    else:
        fail("early_return_paths_emit_row[error_prose]", "error-prose branch instrumentation",
             "1 row source=build_start_context + golden bytes",
             f"rows={rE.new_raw} bytes_match={rE.render_bytes == egold}")

    # ---- Drive F: build_start against EMPTY-SCHEMA db -> rows-empty-no-error return (case 8b) ----
    homeF = base / "homeF"
    empty_db = base / "empty_schema.db"
    rF = drive(driver, homeF, "build_start_context", {"scope": REPO_SCOPE, "query": None},
               empty_db, init_empty=True)
    if rF.rc != 0:
        fail("early_return_paths_emit_row[rows_empty]", "empty-schema render crashed", "rc=0",
             f"rc={rF.rc}; {rF.stderr.strip()[:200]}")
    else:
        okF = (len(rF.new_rows) == 1 and "__unparseable__" not in rF.new_rows[0]
               and rF.new_rows[0].get("source") == "build_start_context"
               and rF.new_rows[0].get("row_count") == 0)
        if okF:
            record(True, "early_return_paths_emit_row[rows_empty]",
                   "empty-but-no-error branch emitted one build_start_context row with row_count=0")
        else:
            fail("early_return_paths_emit_row[rows_empty]", "rows-empty branch instrumentation",
                 "1 row source=build_start_context row_count=0", rF.new_raw)


def _assert_char_golden(r: DriveResult, golden_case: str, case_label: str) -> None:
    if r.rc != 0 or r.render_bytes is None:
        fail(case_label, "render subprocess failed", "rc=0", f"rc={r.rc}; {r.stderr.strip()[:200]}")
        return
    if len(r.new_rows) != 1 or "__unparseable__" in r.new_rows[0]:
        fail(case_label, "expected one parseable telemetry row", 1, r.new_raw)
        return
    expected = len((GOLDEN_DIR / f"{golden_case}.golden").read_bytes().decode("utf-8"))
    got = r.new_rows[0].get("char_count")
    if got == expected and r.char_len == expected:
        record(True, case_label, f"char_count == rendered chars == {expected}")
    else:
        fail(case_label, "char_count vs golden chars", expected, (got, r.char_len))


def _assert_source(r: DriveResult, expected_source: str, case_label: str) -> None:
    if len(r.new_rows) == 1 and r.new_rows[0].get("source") == expected_source:
        record(True, case_label, f"row source == {expected_source!r}")
    else:
        fail(case_label, "row source", expected_source,
             [row.get("source") for row in r.new_rows] if r.new_rows else "no rows")


# ----------------------------------------------------------------------------
# Case 9 — rendered rows logged, not fetched rows
# ----------------------------------------------------------------------------
def case_rendered_not_fetched(base: Path, driver: Path) -> None:
    db1 = base / "rnf_tiny.db"
    db2 = base / "rnf_big.db"
    shutil.copyfile(PINNED_DB, db1)
    shutil.copyfile(PINNED_DB, db2)
    home1 = base / "home_tiny"
    home2 = base / "home_big"
    rt = drive(driver, home1, "build_context", {"scope": REPO_SCOPE, "token_budget": 1}, db1)
    rb = drive(driver, home2, "build_context", {"scope": REPO_SCOPE, "token_budget": 100000}, db2)
    if rt.rc != 0 or rb.rc != 0 or len(rt.new_rows) != 1 or len(rb.new_rows) != 1:
        fail("rendered_not_fetched", "could not obtain both rows", "2 rows",
             f"tiny_rc={rt.rc} big_rc={rb.rc} tiny_rows={len(rt.new_rows)} big_rows={len(rb.new_rows)}")
        return
    tiny = rt.new_rows[0]
    big = rb.new_rows[0]
    # Same DB rows are FETCHED in both; only RENDERED count differs with budget.
    # A tiny budget must render strictly fewer rows than a huge budget, and the
    # logged row_count must equal len(memory_ids) and match the rendered string.
    tiny_ok = (isinstance(tiny.get("memory_ids"), list)
               and len(tiny["memory_ids"]) == tiny.get("row_count")
               and tiny.get("char_count") == rt.char_len)
    big_ok = (isinstance(big.get("memory_ids"), list)
              and len(big["memory_ids"]) == big.get("row_count"))
    if tiny_ok and big_ok and tiny.get("row_count") < big.get("row_count"):
        record(True, "rendered_not_fetched",
               f"logged row_count tracks RENDERED rows (tiny={tiny['row_count']} < big={big['row_count']})")
    else:
        fail("rendered_not_fetched", "memory_ids must reflect rendered subset, not all fetched",
             "tiny.row_count < big.row_count with consistent ids/char",
             f"tiny={tiny.get('row_count')} big={big.get('row_count')} tiny_ok={tiny_ok} big_ok={big_ok}")


# ----------------------------------------------------------------------------
# Case 10 — bypass env suppresses the row but not the render
# ----------------------------------------------------------------------------
def case_bypass_env(base: Path, driver: Path) -> None:
    db = base / "bypass.db"
    shutil.copyfile(PINNED_DB, db)
    home = base / "home_bypass"
    r = drive(driver, home, "build_context", {}, db,
              extra_env={"CLAUDE_BOOSTER_SKIP_MEMORY_TELEMETRY": "1"})
    golden = (GOLDEN_DIR / "build_context_default.golden").read_bytes()
    if r.rc == 0 and len(r.new_rows) == 0 and r.render_bytes == golden:
        record(True, "bypass_env_suppresses",
               "SKIP=1 appended no row and render bytes unchanged")
    else:
        fail("bypass_env_suppresses", "SKIP=1 must suppress row, keep render",
             "0 rows + golden bytes",
             f"rc={r.rc} rows={len(r.new_rows)} bytes_match={r.render_bytes == golden}")


# ----------------------------------------------------------------------------
# Cases 11 & 12 — fail-open under REAL induced failures + missing module safe
# ----------------------------------------------------------------------------
def case_fail_open(base: Path, driver: Path) -> None:
    golden = (GOLDEN_DIR / "build_context_default.golden").read_bytes()

    # (a) read-only log FILE (append raises PermissionError). We make only the
    # jsonl read-only, NOT the dir, so rolling_memory's own RotatingFileHandler
    # (memory_hooks.log in the same dir) still opens — isolating the induced
    # failure to the telemetry write.
    homeA = base / "fo_readonly_file"
    logsA = homeA / ".claude" / "logs"
    logsA.mkdir(parents=True, exist_ok=True)
    logfileA = logsA / "memory_injection.jsonl"
    logfileA.write_text("", encoding="utf-8")
    dbA = base / "fo_a.db"
    shutil.copyfile(PINNED_DB, dbA)
    os.chmod(logfileA, 0o400)
    try:
        rA = drive(driver, homeA, "build_context", {}, dbA)
    finally:
        os.chmod(logfileA, 0o600)
    if rA.rc == 0 and rA.render_bytes == golden and len(rA.new_rows) == 0:
        record(True, "fail_open_induced[readonly_file]",
               "read-only log file: exit 0, no row, render bytes intact")
    else:
        fail("fail_open_induced[readonly_file]", "must fail open on unwritable log file",
             "rc=0 + golden bytes + 0 rows",
             f"rc={rA.rc} bytes_match={rA.render_bytes == golden} rows={len(rA.new_rows)}; {rA.stderr.strip()[:200]}")

    # (c) log path is a directory (open-for-append raises)
    homeC = base / "fo_isdir"
    (homeC / ".claude" / "logs" / "memory_injection.jsonl").mkdir(parents=True, exist_ok=True)
    dbC = base / "fo_c.db"
    shutil.copyfile(PINNED_DB, dbC)
    rC = drive(driver, homeC, "build_context", {}, dbC)
    if rC.rc == 0 and rC.render_bytes == golden:
        record(True, "fail_open_induced[log_is_directory]",
               "log path is a directory: exit 0 + render bytes intact")
    else:
        fail("fail_open_induced[log_is_directory]", "must fail open when log path unusable",
             "rc=0 + golden bytes", f"rc={rC.rc} bytes_match={rC.render_bytes == golden}; {rC.stderr.strip()[:200]}")

    # (b) memory_telemetry.py parked (module absent) -> ALSO case 12 (import must not die)
    parked = DEPLOYED_TEL.with_suffix(".py.parked_by_test")
    homeB = base / "fo_absent"
    dbB = base / "fo_b.db"
    shutil.copyfile(PINNED_DB, dbB)
    moved = False
    try:
        DEPLOYED_TEL.rename(parked)
        moved = True
        rB = drive(driver, homeB, "build_context", {}, dbB)
    finally:
        if moved and parked.exists():
            parked.rename(DEPLOYED_TEL)
    # case 12: import rolling_memory + build_context must NOT raise with module gone
    if rB.rc == 0 and rB.render_bytes == golden:
        record(True, "missing_telemetry_module_is_safe",
               "with memory_telemetry parked, import+build_context did not raise; render intact")
    else:
        fail("missing_telemetry_module_is_safe", "top-level telemetry import would kill the memory engine",
             "rc=0 + golden bytes", f"rc={rB.rc} bytes_match={rB.render_bytes == golden}; {rB.stderr.strip()[:300]}")
    # case 11(b) is the fail-open aspect of the same run
    if rB.rc == 0 and len(rB.new_rows) == 0 and rB.render_bytes == golden:
        record(True, "fail_open_induced[module_absent]",
               "parked module: exit 0, no row, render bytes intact")
    else:
        fail("fail_open_induced[module_absent]", "must fail open when telemetry module absent",
             "rc=0, 0 rows, golden bytes",
             f"rc={rB.rc} rows={len(rB.new_rows)} bytes_match={rB.render_bytes == golden}")


# ----------------------------------------------------------------------------
# Case 18 — append-only log
# ----------------------------------------------------------------------------
def case_append_only(base: Path, driver: Path) -> None:
    home = base / "home_append"
    db = base / "append.db"
    shutil.copyfile(PINNED_DB, db)
    log = home / ".claude" / "logs" / "memory_injection.jsonl"
    drive(driver, home, "build_context", {}, db)
    if not log.exists():
        # Worker may anchor at real home; append-only still assertable there but
        # we avoid mutating the real log. Report inconclusive as a soft skip->fail-safe.
        record(False, "append_only", "temp log not created (path not HOME-anchored) — cannot assert in isolation")
        return
    first_bytes = log.read_bytes()
    first_lines = len(first_bytes.splitlines())
    drive(driver, home, "build_context", {}, db)
    second_bytes = log.read_bytes()
    second_lines = len(second_bytes.splitlines())
    if second_bytes.startswith(first_bytes) and second_lines == first_lines + 1:
        record(True, "append_only",
               f"log grew by exactly one line and prior bytes unchanged ({first_lines}->{second_lines})")
    else:
        fail("append_only", "log must be append-only",
             f"prefix-preserved and lines {first_lines}->{first_lines + 1}",
             f"prefix_ok={second_bytes.startswith(first_bytes)} lines={first_lines}->{second_lines}")


# ----------------------------------------------------------------------------
# Cases 14, 15, 16 — memory_telemetry.py report CLI
# ----------------------------------------------------------------------------
def _run_report(home: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DEPLOYED_TEL), "report", "--json", *extra],
        env=_make_env(home), capture_output=True, text=True,
    )


def case_report(base: Path) -> None:
    # case 14: report against ABSENT DB must not create a DB file.
    home = base / "report_home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    db_target = home / ".claude" / "rolling_memory.db"
    if db_target.exists():
        db_target.unlink()
    before = sorted(str(p) for p in (home / ".claude").glob("*.db"))
    proc = _run_report(home)
    after = sorted(str(p) for p in (home / ".claude").glob("*.db"))
    parsed = None
    try:
        parsed = json.loads(proc.stdout)
    except Exception:
        pass
    if proc.returncode == 0 and parsed is not None and not db_target.exists() and before == after:
        record(True, "report_does_not_create_db",
               "report on absent DB: exit 0, valid JSON, no .db file created")
    else:
        fail("report_does_not_create_db", "report must not create a DB on missing path",
             "rc=0, valid JSON, no new .db",
             f"rc={proc.returncode} json_ok={parsed is not None} db_created={db_target.exists()}; stderr={proc.stderr.strip()[:200]}")

    # case 15: report JSON shape
    if parsed is not None and {"sessions", "by_type", "never_injected_ids"} <= set(parsed.keys()):
        record(True, "report_json_shape", "report --json has sessions, by_type, never_injected_ids")
    else:
        fail("report_json_shape", "report JSON keys",
             {"sessions", "by_type", "never_injected_ids"},
             (sorted(parsed.keys()) if parsed is not None else "unparseable"))

    # case 16: --window N is N DAYS, filtered on ts_utc.
    win_home = base / "report_window_home"
    log = win_home / ".claude" / "logs" / "memory_injection.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)

    # Derive the Worker's own ts_utc format from a REAL emitted row, so the
    # synthetic rows are parsed identically by the report.
    probe_home = base / "tsprobe_home"
    probe_db = base / "tsprobe.db"
    shutil.copyfile(PINNED_DB, probe_db)
    driver = base / "driver.py"  # already written by main()
    rp = drive(driver, probe_home, "build_context", {}, probe_db)
    if not rp.new_rows or "__unparseable__" in rp.new_rows[0] or "ts_utc" not in rp.new_rows[0]:
        fail("report_window_is_days", "could not derive real ts_utc format", "a real row with ts_utc", rp.new_raw)
        return
    template_row = rp.new_rows[0]
    real_ts = template_row["ts_utc"]

    def reformat(dt: datetime) -> str:
        dt = dt.astimezone(timezone.utc)
        if isinstance(real_ts, str) and real_ts.endswith("Z"):
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return dt.isoformat()

    now = datetime.now(timezone.utc)
    fresh = dict(template_row)
    fresh["ts_utc"] = reformat(now - timedelta(days=1))
    fresh["session_id"] = "SESS_FRESH_MARKER"
    stale = dict(template_row)
    stale["ts_utc"] = reformat(now - timedelta(days=40))
    stale["session_id"] = "SESS_STALE_MARKER"
    with log.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(fresh) + "\n")
        fh.write(json.dumps(stale) + "\n")

    p30 = _run_report(win_home, "--window", "30")
    p90 = _run_report(win_home, "--window", "90")
    out30 = p30.stdout
    out90 = p90.stdout
    win_ok = (p30.returncode == 0 and p90.returncode == 0
              and "SESS_FRESH_MARKER" in out30 and "SESS_STALE_MARKER" not in out30
              and "SESS_FRESH_MARKER" in out90 and "SESS_STALE_MARKER" in out90)
    if win_ok:
        record(True, "report_window_is_days",
               "--window 30 keeps the 1-day-old row, drops the 40-day-old; --window 90 keeps both")
    else:
        fail("report_window_is_days", "--window N must filter by N days on ts_utc",
             "fresh-only in w30, both in w90",
             f"w30_fresh={'SESS_FRESH_MARKER' in out30} w30_stale={'SESS_STALE_MARKER' in out30} "
             f"w90_fresh={'SESS_FRESH_MARKER' in out90} w90_stale={'SESS_STALE_MARKER' in out90} "
             f"rc30={p30.returncode} rc90={p90.returncode}; stderr={p30.stderr.strip()[:200]}")


# ----------------------------------------------------------------------------
# Case 17 — deployed vs template byte-identical (both files)
# ----------------------------------------------------------------------------
def case_copies_identical() -> None:
    for label, dep, tpl in [
        ("rolling_memory.py", DEPLOYED_RM, TEMPLATE_RM),
        ("memory_telemetry.py", DEPLOYED_TEL, TEMPLATE_TEL),
    ]:
        if not dep.exists() or not tpl.exists():
            fail("copies_stay_identical", f"{label} missing on one side", "both exist",
                 f"deployed={dep.exists()} template={tpl.exists()}")
            continue
        if dep.read_bytes() == tpl.read_bytes():
            record(True, f"copies_stay_identical[{label}]", "deployed == template (byte-identical)")
        else:
            fail("copies_stay_identical", f"{label} deployed vs template differ",
                 "byte-identical",
                 f"dep_sha={hashlib.sha256(dep.read_bytes()).hexdigest()[:12]} "
                 f"tpl_sha={hashlib.sha256(tpl.read_bytes()).hexdigest()[:12]}")


# ----------------------------------------------------------------------------
# Case 19 — installer would deploy the new module into CLAUDE_HOME
# ----------------------------------------------------------------------------
def case_install_deploys_module(base: Path) -> None:
    temp_home = base / "install_home"
    (temp_home / ".claude").mkdir(parents=True, exist_ok=True)
    snippet = (
        "import install, json;"
        "pairs=install.enumerate_template_files();"
        "print(json.dumps([str(d) for s,d in pairs if s.name=='memory_telemetry.py']))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=str(REPO), env=_make_env(temp_home), capture_output=True, text=True,
    )
    targets = []
    try:
        targets = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        pass
    expected_tail = os.path.join(".claude", "scripts", "memory_telemetry.py")
    if proc.returncode == 0 and any(t.endswith(expected_tail) and str(temp_home) in t for t in targets):
        record(True, "install_deploys_new_module",
               f"install.enumerate_template_files maps memory_telemetry.py -> {targets[0]}")
    else:
        fail("install_deploys_new_module", "installer must deploy memory_telemetry.py into CLAUDE_HOME",
             f"a target ending {expected_tail} under temp home", targets or proc.stderr.strip()[:200])


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> int:
    print("=== memory-injection telemetry acceptance ===")
    if not check_preconditions():
        print("\nResults: preconditions failed — aborting.")
        passed = sum(1 for ok, *_ in _RESULTS if ok)
        failed = len(_RESULTS) - passed
        print(f"Results: {passed} passed, {failed} failed")
        return 1

    global base_out_dir
    base = Path(tempfile.mkdtemp(prefix="mem_telemetry_accept_"))
    base_out_dir = base
    driver = base / "driver.py"
    driver.write_text(_DRIVER_SRC, encoding="utf-8")

    # Some drives (build_start_context) must run under the REAL HOME to match the
    # HOME-sensitive goldens; they append to the real telemetry log. Capture its
    # prior state and restore it verbatim in finally (contract requirement).
    real_log = REAL_HOME / ".claude" / "logs" / "memory_injection.jsonl"
    real_log_existed = real_log.exists()
    real_log_original = real_log.read_bytes() if real_log_existed else None

    try:
        case_preserve_and_determinism(base)      # 1, 2
        case_row_observations(base, driver)       # 3,4,5,6,7(3x),8(2x),13
        case_rendered_not_fetched(base, driver)   # 9
        case_bypass_env(base, driver)             # 10
        case_fail_open(base, driver)              # 11(3x),12
        case_append_only(base, driver)            # 18
        case_report(base)                         # 14,15,16
        case_copies_identical()                   # 17
        case_install_deploys_module(base)         # 19
    finally:
        # Restore the real telemetry log to exactly its prior state.
        try:
            if real_log_original is not None:
                real_log.write_bytes(real_log_original)
            elif real_log.exists():
                real_log.unlink()
        except OSError:
            pass
        shutil.rmtree(base, ignore_errors=True)

    passed = sum(1 for ok, *_ in _RESULTS if ok)
    failed = len(_RESULTS) - passed
    print(f"\nResults: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
