#!/usr/bin/env bash
# Acceptance test: delegate_gate.py _feedback() message brevity + sync
#
# Artifact Contract:
#   On budget exhaustion, stderr is ≤50 chars, single line, contains
#   "delegate_gate:" and "Agent".
#   templates/scripts/delegate_gate.py and ~/.claude/scripts/delegate_gate.py
#   are byte-identical. All existing tests pass.
#
# Observable behavior tested (no LLM judgment, pure exit-code/length assertions):
#   A1 — _feedback() output is ≤50 chars and contains no embedded newlines
#   A2 — templates/scripts/delegate_gate.py and ~/.claude/scripts/delegate_gate.py
#        are byte-identical
#   A3 — all existing test_delegate_gate_*.sh scripts exit 0
#
# Exit 0 = all assertions pass, non-zero = at least one failure.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATE_PY="$REPO_ROOT/templates/scripts/delegate_gate.py"
SCRIPT_DIR="$REPO_ROOT/templates/scripts"
DEPLOYED_PY="$HOME/.claude/scripts/delegate_gate.py"
TESTS_DIR="$REPO_ROOT/tests"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Assertion A1: _feedback() output is ≤50 chars, single line,
#               contains "delegate_gate:" and "Agent"
# ---------------------------------------------------------------------------

# We call the function directly via python3 by importing delegate_gate as a module.
# We pass synthetic arguments: root=Path('/tmp'), tool='Edit', counter=2.
# The function is pure string-construction — no filesystem or stdin side-effects.

FEEDBACK_OUTPUT=$(python3 - <<'PYEOF'
import sys, pathlib
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else '.')
# Minimal _gate_common stub so the import succeeds without the real harness.
import types, sys as _sys
_gc = types.ModuleType("_gate_common")
_gc.BYPASS_LOG_NAME = "bypass.jsonl"
_gc.DECISION_ALLOW = "allow"
_gc.DECISION_AUTO_SKIP = "auto_skip"
_gc.DECISION_BLOCK = "block"
_gc.DECISION_BYPASS_HONOURED = "bypass_honoured"
_gc.DECISION_BYPASS_REFUSED = "bypass_refused"
_gc.DELEGATE_LOG_NAME = "delegate.jsonl"
_gc.append_jsonl = lambda *a, **kw: None
_gc.is_subagent_context = lambda d: False
_gc.iso_now = lambda: "2026-01-01T00:00:00Z"
_gc.project_root_from = lambda cwd: None
_gc.redact_secrets = lambda s: s
_sys.modules["_gate_common"] = _gc

import importlib.util, pathlib, os
spec = importlib.util.spec_from_file_location(
    "delegate_gate",
    os.path.join(os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else ".", ".")
)

# Load by direct exec to avoid __main__ guard.
src = open("/dev/stdin").read()   # not used; file path comes from argv
PYEOF
)

# Simpler approach: exec _feedback() via subprocess, capture its output.
FEEDBACK_OUTPUT=$(python3 - "$GATE_PY" <<'PYEOF'
import sys, types, pathlib

gate_path = sys.argv[1]

# Stub _gate_common so the import chain completes.
_gc = types.ModuleType("_gate_common")
_gc.BYPASS_LOG_NAME = "bypass.jsonl"
_gc.DECISION_ALLOW = "allow"
_gc.DECISION_AUTO_SKIP = "auto_skip"
_gc.DECISION_BLOCK = "block"
_gc.DECISION_BYPASS_HONOURED = "bypass_honoured"
_gc.DECISION_BYPASS_REFUSED = "bypass_refused"
_gc.DELEGATE_LOG_NAME = "delegate.jsonl"
_gc.append_jsonl = lambda *a, **kw: None
_gc.is_subagent_context = lambda d: False
_gc.iso_now = lambda: "2026-01-01T00:00:00Z"
_gc.project_root_from = lambda cwd: None
_gc.redact_secrets = lambda s: s
sys.modules["_gate_common"] = _gc

import importlib.util
spec = importlib.util.spec_from_file_location("delegate_gate", gate_path)
mod = importlib.util.module_from_spec(spec)
# Skip __main__ block; safe because we only call _feedback, not main().
spec.loader.exec_module(mod)

result = mod._feedback(pathlib.Path("/tmp/proj"), "Edit", 2)
print(result, end="")
PYEOF
)

PY_EXIT=$?
if [[ $PY_EXIT -ne 0 ]]; then
    fail "A1 — could not invoke _feedback() (python3 exit $PY_EXIT)"
else
    MSG_LEN=${#FEEDBACK_OUTPUT}
    # Check for embedded newlines: count lines by splitting.
    LINE_COUNT=$(printf '%s' "$FEEDBACK_OUTPUT" | wc -l)
    # wc -l counts \n characters; a single-line string with no trailing \n gives 0.
    # A string with one \n (trailing newline added by gate's stderr.write) gives 1 but
    # _feedback() itself returns the string WITHOUT a trailing newline — the caller adds it.
    # We test the raw return value here (no trailing \n), so LINE_COUNT must be 0.

    A1_OK=true
    A1_NOTES=""

    if [[ $MSG_LEN -gt 50 ]]; then
        A1_OK=false
        A1_NOTES+=" length=$MSG_LEN (>50);"
    fi
    if [[ $LINE_COUNT -gt 0 ]]; then
        A1_OK=false
        A1_NOTES+=" embedded_newlines=$LINE_COUNT;"
    fi
    if ! printf '%s' "$FEEDBACK_OUTPUT" | grep -q "delegate_gate:"; then
        A1_OK=false
        A1_NOTES+=" missing 'delegate_gate:';"
    fi
    if ! printf '%s' "$FEEDBACK_OUTPUT" | grep -q "Agent"; then
        A1_OK=false
        A1_NOTES+=" missing 'Agent';"
    fi

    if $A1_OK; then
        pass "A1 — _feedback() output: len=$MSG_LEN, single-line, contains 'delegate_gate:' and 'Agent' | msg=$(printf '%s' "$FEEDBACK_OUTPUT")"
    else
        fail "A1 — _feedback() output fails:$A1_NOTES | msg=$(printf '%s' "$FEEDBACK_OUTPUT")"
    fi
fi

# ---------------------------------------------------------------------------
# Assertion A2: template and deployed copy are byte-identical
# ---------------------------------------------------------------------------

if [[ ! -f "$GATE_PY" ]]; then
    fail "A2 — template not found: $GATE_PY"
elif [[ ! -f "$DEPLOYED_PY" ]]; then
    fail "A2 — deployed copy not found: $DEPLOYED_PY"
else
    if diff -q "$GATE_PY" "$DEPLOYED_PY" > /dev/null 2>&1; then
        pass "A2 — template and deployed copy are byte-identical"
    else
        fail "A2 — template and deployed copy differ"
        diff "$GATE_PY" "$DEPLOYED_PY" | head -20 || true
    fi
fi

# ---------------------------------------------------------------------------
# Assertion A3: all existing test_delegate_gate_*.sh pass
# ---------------------------------------------------------------------------

SUITE_PASS=0
SUITE_FAIL=0
SUITE_ERRORS=""

for test_script in "$TESTS_DIR"/test_delegate_gate_*.sh; do
    [[ -f "$test_script" ]] || continue
    script_name="$(basename "$test_script")"
    if bash "$test_script" > /tmp/_dg_test_out_$$.txt 2>&1; then
        SUITE_PASS=$((SUITE_PASS + 1))
    else
        exit_code=$?
        SUITE_FAIL=$((SUITE_FAIL + 1))
        SUITE_ERRORS+="  $script_name (exit $exit_code)\n"
        # Show last few lines of output for context.
        tail -10 /tmp/_dg_test_out_$$.txt >&2 || true
    fi
    rm -f /tmp/_dg_test_out_$$.txt
done

if [[ $SUITE_FAIL -eq 0 ]]; then
    pass "A3 — all existing tests pass ($SUITE_PASS scripts)"
else
    fail "A3 — $SUITE_FAIL of $((SUITE_PASS + SUITE_FAIL)) existing tests FAILED:"
    printf '%b' "$SUITE_ERRORS"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [[ $FAIL -eq 0 ]]; then
    echo "ALL ASSERTIONS PASS"
    exit 0
else
    echo "SOME ASSERTIONS FAILED"
    exit 1
fi
