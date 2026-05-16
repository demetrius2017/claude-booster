#!/usr/bin/env python3
"""
Acceptance test for go_gate.py — v2.

Tests the following changes to the contract:
  1. Block stderr message is short: "go_gate: → /go" (≤ 20 chars, excluding newline)
  2. Description-prefix detection for Explore/Plan intent (no subagent_type required)
  3. Gerund forms (Exploring, Explorer, Planning) are NOT matched
  4. Case-insensitivity of description prefix match
  5. Existing test_go_gate.sh still passes (backward compat)
  6. Deployed copy at ~/.claude/scripts/go_gate.py is identical to templates copy

Exit code: 0 if ALL tests pass, 1 if ANY fail.
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Resolve script locations ───────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).resolve().parent          # templates/scripts/
GATE = SCRIPTS_DIR / "go_gate.py"
DEPLOYED_GATE = Path.home() / ".claude" / "scripts" / "go_gate.py"
EXISTING_SHELL_TEST = SCRIPTS_DIR / "test_go_gate.sh"

# ── Counters ───────────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def _ok(label: str) -> None:
    global PASS
    PASS += 1
    print(f"[PASS] {label}")


def _fail(label: str, reason: str) -> None:
    global FAIL
    FAIL += 1
    msg = f"[FAIL] {label} — {reason}"
    FAILURES.append(msg)
    print(msg)


# ── Test infra ─────────────────────────────────────────────────────────────────

def make_workspace() -> tempfile.TemporaryDirectory:
    """Create temp dir with .claude/ subdir; no .go_active marker."""
    tmp = tempfile.mkdtemp(prefix="test_go_gate_v2_")
    claude_dir = Path(tmp) / ".claude"
    claude_dir.mkdir()
    (claude_dir / ".phase").write_text("IMPLEMENT\n")
    # Deliberately NO .go_active file — default for blocking tests
    logs_dir = Path(tmp) / "logs"
    logs_dir.mkdir()
    return tmp


def make_payload(
    description: str = "",
    subagent_type: str = "",
    agent_id: str = "",
    tool_name: str = "Agent",
    cwd: str = "",
) -> str:
    return json.dumps({
        "tool_name": tool_name,
        "tool_input": {
            "description": description,
            "subagent_type": subagent_type,
            "prompt": "",
        },
        "agent_id": agent_id,
        "agent_type": "",
        "cwd": cwd,
        "session_id": "verifier-test",
    })


def run_gate(payload: str, tmpdir: str, extra_env: dict | None = None) -> tuple[int, str]:
    """Run go_gate.py in a subprocess, return (exit_code, stderr_text)."""
    env = {
        **os.environ,
        "PYTHONPATH": str(SCRIPTS_DIR),
        "CLAUDE_HOME": tmpdir,
    }
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, str(GATE)],
        input=payload.encode(),
        capture_output=True,
        env=env,
    )
    stderr = result.stderr.decode(errors="replace")
    return result.returncode, stderr


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 1 — Stderr message ≤ 20 chars
# ═══════════════════════════════════════════════════════════════════════════════

def test_va1_stderr_length() -> None:
    """Block stderr message must be ≤ 20 chars (excluding trailing newline)."""
    label = "VA1: block stderr ≤ 20 chars"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="implement the feature",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code != 2:
            _fail(label, f"expected exit 2 (block), got {code} — test setup issue")
            return
        stripped = stderr.rstrip("\n")
        char_count = len(stripped)
        if char_count <= 20:
            _ok(f"{label} (got {char_count!r} chars: {stripped!r})")
        else:
            _fail(label, f"stderr={stripped!r} is {char_count} chars, exceeds 20")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 2 — "Explore: ..." description prefix → exit 0
# ═══════════════════════════════════════════════════════════════════════════════

def test_va2_explore_colon_prefix() -> None:
    """description='Explore: run prod migration query', no subagent_type → exit 0."""
    label = "VA2: 'Explore: ...' prefix (no subagent_type) → exit 0"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="Explore: run prod migration query",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 0:
            _ok(label)
        else:
            _fail(label, f"exit {code}, stderr={stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 3 — "Plan ..." description prefix → exit 0
# ═══════════════════════════════════════════════════════════════════════════════

def test_va3_plan_prefix() -> None:
    """description='Plan the migration steps', no subagent_type → exit 0."""
    label = "VA3: 'Plan the migration steps' prefix (no subagent_type) → exit 0"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="Plan the migration steps",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 0:
            _ok(label)
        else:
            _fail(label, f"exit {code}, stderr={stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 4 — "Exploring ..." gerund NOT matched → exit 2 (if coding keywords)
# ═══════════════════════════════════════════════════════════════════════════════

def test_va4_exploring_gerund_blocked() -> None:
    """description='Exploring implementation of feature X' + coding keyword → exit 2."""
    label = "VA4: gerund 'Exploring implementation...' with coding keyword → exit 2 (blocked)"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="Exploring implementation of feature X",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 2:
            _ok(label)
        else:
            _fail(label, f"exit {code} — gerund form 'Exploring' was incorrectly allowed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 5 — "Planning to implement..." → exit 2
# ═══════════════════════════════════════════════════════════════════════════════

def test_va5_planning_gerund_blocked() -> None:
    """description='Planning to implement the fix' → exit 2 (gerund not matched)."""
    label = "VA5: gerund 'Planning to implement the fix' → exit 2 (blocked)"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="Planning to implement the fix",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 2:
            _ok(label)
        else:
            _fail(label, f"exit {code} — gerund 'Planning' was incorrectly allowed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 6 — "Explorer agent for ..." → exit 2
# ═══════════════════════════════════════════════════════════════════════════════

def test_va6_explorer_blocked() -> None:
    """description='Explorer agent for codebase' with coding keyword → exit 2."""
    label = "VA6: 'Explorer agent for codebase' (non-exact prefix) → exit 2 (blocked)"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            # "Explorer" is not "Explore" — must not match
            description="Explorer agent for codebase implement the fix",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 2:
            _ok(label)
        else:
            _fail(label, f"exit {code} — 'Explorer' was incorrectly matched as Explore prefix")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 7 — bare "explore" (end-of-string) → exit 0
# ═══════════════════════════════════════════════════════════════════════════════

def test_va7_bare_explore() -> None:
    """description='explore' (bare word, end-of-string) → exit 0."""
    label = "VA7: bare 'explore' (end-of-string boundary) → exit 0"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="explore",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 0:
            _ok(label)
        else:
            _fail(label, f"exit {code}, stderr={stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 8 — Case-insensitivity: EXPLORE: and explore: both → exit 0
# ═══════════════════════════════════════════════════════════════════════════════

def test_va8_case_insensitive() -> None:
    """EXPLORE: find files and explore: find files both → exit 0."""
    tmpdir = make_workspace()
    try:
        for variant in ["EXPLORE: find files", "explore: find files", "Explore: find files"]:
            label = f"VA8: case variant '{variant}' → exit 0"
            payload = make_payload(
                description=variant,
                subagent_type="",
                cwd=tmpdir,
            )
            code, stderr = run_gate(payload, tmpdir)
            if code == 0:
                _ok(label)
            else:
                _fail(label, f"exit {code}, stderr={stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 9 — Existing test_go_gate.sh passes
# ═══════════════════════════════════════════════════════════════════════════════

def test_va9_existing_shell_test() -> None:
    """templates/scripts/test_go_gate.sh must exit 0 after modification."""
    label = "VA9: existing test_go_gate.sh exits 0 (backward compat)"
    if not EXISTING_SHELL_TEST.exists():
        _fail(label, f"test_go_gate.sh not found at {EXISTING_SHELL_TEST}")
        return
    result = subprocess.run(
        ["bash", str(EXISTING_SHELL_TEST)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        _ok(label)
    else:
        _fail(
            label,
            f"exit {result.returncode}\n"
            f"  stdout: {result.stdout[-800:]!r}\n"
            f"  stderr: {result.stderr[-400:]!r}",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 10 — Deployed copy identical to templates copy
# ═══════════════════════════════════════════════════════════════════════════════

def test_va10_deployed_synced() -> None:
    """~/.claude/scripts/go_gate.py must be byte-identical to templates/scripts/go_gate.py."""
    label = "VA10: deployed ~/.claude/scripts/go_gate.py identical to templates copy"
    if not DEPLOYED_GATE.exists():
        _fail(label, f"deployed copy not found at {DEPLOYED_GATE}")
        return
    if filecmp.cmp(str(GATE), str(DEPLOYED_GATE), shallow=False):
        _ok(label)
    else:
        # Show line count difference for diagnostic
        tmpl_lines = GATE.read_text(errors="replace").splitlines()
        dep_lines = DEPLOYED_GATE.read_text(errors="replace").splitlines()
        _fail(
            label,
            f"files differ: templates has {len(tmpl_lines)} lines, "
            f"deployed has {len(dep_lines)} lines. "
            f"First diff around line: {_first_diff_line(tmpl_lines, dep_lines)}",
        )


def _first_diff_line(a: list[str], b: list[str]) -> int:
    for i, (la, lb) in enumerate(zip(a, b), start=1):
        if la != lb:
            return i
    return min(len(a), len(b)) + 1


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 11 — subagent_type='Explore' takes priority (still exit 0)
# ═══════════════════════════════════════════════════════════════════════════════

def test_va11_subagent_type_priority() -> None:
    """subagent_type='Explore' → exit 0 (subagent_type check fires before description prefix)."""
    label = "VA11: subagent_type='Explore' → exit 0 (type check fires first)"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="implement fix write code",  # all coding keywords
            subagent_type="Explore",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 0:
            _ok(label)
        else:
            _fail(label, f"exit {code}, stderr={stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Assertion 12 — Fail-open on empty description and binary stdin
# ═══════════════════════════════════════════════════════════════════════════════

def test_va12_fail_open_edge_cases() -> None:
    """Gate fails open on empty description or binary/non-JSON stdin."""
    tmpdir = make_workspace()
    try:
        # Empty description
        label = "VA12a: empty description + IMPLEMENT + no marker → exit 0 (fail-open or no coding keywords)"
        payload = make_payload(description="", subagent_type="", cwd=tmpdir)
        code, _ = run_gate(payload, tmpdir)
        # Empty description has no coding keywords → should be allowed (exit 0)
        if code == 0:
            _ok(label)
        else:
            _fail(label, f"exit {code} — empty description should be allowed (no coding keywords)")

        # Binary/non-JSON stdin → fail-open
        label = "VA12b: binary stdin → exit 0 (fail-open)"
        env = {
            **os.environ,
            "PYTHONPATH": str(SCRIPTS_DIR),
            "CLAUDE_HOME": tmpdir,
        }
        result = subprocess.run(
            [sys.executable, str(GATE)],
            input=b"\x00\x01\x02\x03\xff\xfe",
            capture_output=True,
            env=env,
        )
        if result.returncode == 0:
            _ok(label)
        else:
            _fail(label, f"exit {result.returncode} on binary input")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Invariant: stderr_length_cap — verify it on a freshly blocking call
# ═══════════════════════════════════════════════════════════════════════════════

def test_inv_stderr_length_cap() -> None:
    """INV: Any blocking call produces stderr ≤ 20 chars (excluding newline)."""
    label = "INV: stderr_length_cap ≤ 20 chars on every block"
    tmpdir = make_workspace()
    try:
        for desc in ["implement feature", "write code now", "fix the bug"]:
            payload = make_payload(description=desc, subagent_type="", cwd=tmpdir)
            code, stderr = run_gate(payload, tmpdir)
            if code != 2:
                _fail(label, f"desc={desc!r} was not blocked (exit {code}), can't measure")
                continue
            stripped = stderr.rstrip("\n")
            if len(stripped) <= 20:
                _ok(f"{label} for {desc!r} ({len(stripped)} chars)")
            else:
                _fail(label, f"desc={desc!r}: stderr={stripped!r} is {len(stripped)} chars > 20")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Invariant: explore_plan_never_blocked (both via subagent_type and description prefix)
# ═══════════════════════════════════════════════════════════════════════════════

def test_inv_explore_plan_never_blocked() -> None:
    """INV: Explore/Plan (subagent_type OR description prefix) → never exit 2."""
    tmpdir = make_workspace()
    try:
        cases = [
            ("subagent_type='Explore'", "implement fix write code", "Explore"),
            ("subagent_type='Plan'", "implement fix write code", "Plan"),
            ("desc prefix 'Explore:'", "Explore: implement the feature", ""),
            ("desc prefix 'Plan '", "Plan the implementation steps", ""),
            ("desc 'plan:'", "plan: implement steps", ""),
        ]
        for name, desc, st in cases:
            label = f"INV: explore_plan_never_blocked — {name}"
            payload = make_payload(description=desc, subagent_type=st, cwd=tmpdir)
            code, stderr = run_gate(payload, tmpdir)
            if code == 0:
                _ok(label)
            else:
                _fail(label, f"exit {code}, was blocked — stderr={stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Invariant: gerund_not_matched — Exploring/Planning/Explorer → blocked
# ═══════════════════════════════════════════════════════════════════════════════

def test_inv_gerund_not_matched() -> None:
    """INV: gerund_not_matched — Exploring/Planning/Explorer with coding keyword → exit 2."""
    tmpdir = make_workspace()
    try:
        cases = [
            "Exploring implementation of feature X",
            "Planning to implement the fix",
            "Explorer agent implementing the module",
            "exploring and implementing the solution",  # lowercase gerund
        ]
        for desc in cases:
            label = f"INV: gerund_not_matched — '{desc[:40]}' → exit 2"
            payload = make_payload(description=desc, subagent_type="", cwd=tmpdir)
            code, stderr = run_gate(payload, tmpdir)
            if code == 2:
                _ok(label)
            else:
                _fail(label, f"exit {code} — gerund form was incorrectly allowed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Invariant: case_insensitive — EXPLORE/explore/Explore all matched
# ═══════════════════════════════════════════════════════════════════════════════

def test_inv_case_insensitive() -> None:
    """INV: case_insensitive — all case variants of 'explore'/'plan' prefix → exit 0."""
    tmpdir = make_workspace()
    try:
        variants = [
            "EXPLORE: find stuff",
            "explore: find stuff",
            "Explore: find stuff",
            "ExPlOrE: find stuff",
            "PLAN the migration",
            "plan the migration",
            "Plan the migration",
        ]
        for desc in variants:
            label = f"INV: case_insensitive — '{desc}' → exit 0"
            payload = make_payload(description=desc, subagent_type="", cwd=tmpdir)
            code, stderr = run_gate(payload, tmpdir)
            if code == 0:
                _ok(label)
            else:
                _fail(label, f"exit {code} — case variant blocked, stderr={stderr!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Invariant: fail_open_preserved — exception path exits 0
# ═══════════════════════════════════════════════════════════════════════════════

def test_inv_fail_open_preserved() -> None:
    """INV: fail_open_preserved — malformed JSON stdin → exit 0."""
    label = "INV: fail_open_preserved — malformed JSON → exit 0"
    tmpdir = make_workspace()
    try:
        env = {
            **os.environ,
            "PYTHONPATH": str(SCRIPTS_DIR),
            "CLAUDE_HOME": tmpdir,
        }
        result = subprocess.run(
            [sys.executable, str(GATE)],
            input=b"NOT_VALID_JSON{{{{{",
            capture_output=True,
            env=env,
        )
        if result.returncode == 0:
            _ok(label)
        else:
            _fail(label, f"exit {result.returncode} on malformed JSON (expected 0)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Invariant: subagent_type_priority — type check before description prefix
# ═══════════════════════════════════════════════════════════════════════════════

def test_inv_subagent_type_priority() -> None:
    """INV: subagent_type_priority — subagent_type='Explore' wins even over coding description."""
    label = "INV: subagent_type_priority — Explore type check fires before description analysis"
    tmpdir = make_workspace()
    try:
        # If subagent_type check fires first, this exits 0 immediately.
        # If description keyword check fires first, "implement" would block it.
        payload = make_payload(
            description="implement refactor fix write code edit modify",
            subagent_type="Explore",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 0:
            _ok(label)
        else:
            _fail(label, f"exit {code} — subagent_type='Explore' was blocked (type check not first)")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Branching scenario: false_positive_exploring
# ═══════════════════════════════════════════════════════════════════════════════

def test_branch_false_positive_exploring() -> None:
    """BRANCH: 'Exploring the codebase...' with coding keywords → must be blocked (exit 2)."""
    label = "BRANCH: false_positive_exploring — 'Exploring...' + coding kw → exit 2"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="Exploring the codebase to implement the feature",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 2:
            _ok(label)
        else:
            _fail(label, f"exit {code} — 'Exploring...' was incorrectly allowed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Branching scenario: false_positive_explorer
# ═══════════════════════════════════════════════════════════════════════════════

def test_branch_false_positive_explorer() -> None:
    """BRANCH: 'Explorer agent for...' with coding keyword → must be blocked (exit 2)."""
    label = "BRANCH: false_positive_explorer — 'Explorer agent...' → exit 2"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="Explorer agent for the codebase implementing feature",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 2:
            _ok(label)
        else:
            _fail(label, f"exit {code} — 'Explorer' was incorrectly matched as Explore prefix")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Branching scenario: false_positive_planning
# ═══════════════════════════════════════════════════════════════════════════════

def test_branch_false_positive_planning() -> None:
    """BRANCH: 'Planning to implement...' → must be blocked (exit 2)."""
    label = "BRANCH: false_positive_planning — 'Planning to implement...' → exit 2"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="Planning to implement the fix in the codebase",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 2:
            _ok(label)
        else:
            _fail(label, f"exit {code} — 'Planning to...' was incorrectly allowed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Branching scenario: case_variant allowed
# ═══════════════════════════════════════════════════════════════════════════════

def test_branch_case_variant_allowed() -> None:
    """BRANCH: case_variant — 'EXPLORE: query' → must be allowed (exit 0)."""
    label = "BRANCH: case_variant — 'EXPLORE: query' → exit 0"
    tmpdir = make_workspace()
    try:
        payload = make_payload(
            description="EXPLORE: query the database for patterns",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 0:
            _ok(label)
        else:
            _fail(label, f"exit {code} — uppercase EXPLORE: was blocked")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Additional: boundary check — "Explore " (space boundary) vs "ExploreX" (no boundary)
# ═══════════════════════════════════════════════════════════════════════════════

def test_boundary_checks() -> None:
    """Additional: word boundary — 'Explore ' allowed, 'ExploreX implement' blocked."""
    tmpdir = make_workspace()
    try:
        # "Explore " (with trailing space, valid prefix) → exit 0
        label = "BOUNDARY: 'Explore the repo' (space after Explore) → exit 0"
        payload = make_payload(
            description="Explore the repo for patterns",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 0:
            _ok(label)
        else:
            _fail(label, f"exit {code}")

        # "ExploreX implement" — no separator, not a valid prefix → exit 2
        label = "BOUNDARY: 'ExploreX implement the feature' (no separator) → exit 2"
        payload = make_payload(
            description="ExploreX implement the feature",
            subagent_type="",
            cwd=tmpdir,
        )
        code, stderr = run_gate(payload, tmpdir)
        if code == 2:
            _ok(label)
        else:
            _fail(label, f"exit {code} — 'ExploreX' was incorrectly matched as Explore prefix")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    global PASS, FAIL

    if not GATE.exists():
        print(f"[FAIL] SETUP: go_gate.py not found at {GATE}")
        return 1

    print(f"Testing: {GATE}")
    print(f"Deployed: {DEPLOYED_GATE}")
    print("=" * 60)

    # Run all tests
    test_va1_stderr_length()
    test_va2_explore_colon_prefix()
    test_va3_plan_prefix()
    test_va4_exploring_gerund_blocked()
    test_va5_planning_gerund_blocked()
    test_va6_explorer_blocked()
    test_va7_bare_explore()
    test_va8_case_insensitive()
    test_va9_existing_shell_test()
    test_va10_deployed_synced()
    test_va11_subagent_type_priority()
    test_va12_fail_open_edge_cases()

    test_inv_stderr_length_cap()
    test_inv_explore_plan_never_blocked()
    test_inv_gerund_not_matched()
    test_inv_case_insensitive()
    test_inv_fail_open_preserved()
    test_inv_subagent_type_priority()

    test_branch_false_positive_exploring()
    test_branch_false_positive_explorer()
    test_branch_false_positive_planning()
    test_branch_case_variant_allowed()
    test_boundary_checks()

    print("=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")

    if FAILURES:
        print("\nFailed tests:")
        for msg in FAILURES:
            print(f"  {msg}")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
