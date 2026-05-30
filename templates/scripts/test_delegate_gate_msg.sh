#!/usr/bin/env bash
# Acceptance test: delegate_gate.py over-budget ADVISORY behavior + sync
#
# Artifact Contract:
#   The gate is ADVISORY: on budget exhaustion it emits a single-line JSON
#   {"additionalContext": "<nudge>"} on stdout and exits 0 (NEVER 2). The
#   nudge text contains "delegate_gate:". Exit 2 is reserved for the
#   malformed-payload fail-closed branch only.
#   templates/scripts/delegate_gate.py and ~/.claude/scripts/delegate_gate.py
#   are byte-identical. All existing tests pass.
#
# Observable behavior tested (no LLM judgment, pure exit-code/JSON assertions):
#   A1 — over-budget action → exit 0 AND stdout is one clean JSON object with
#        key "additionalContext"; malformed stdin still → exit 2 (fail-closed)
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
# Assertion A1: over-budget action → advisory (exit 0 + clean additionalContext
#               JSON on stdout); malformed stdin → exit 2 (fail-closed preserved).
# ---------------------------------------------------------------------------

# Drive the gate end-to-end via stdin. Pre-seed the counter to BUDGET so the
# next counted action is over budget. We use an isolated tempdir + CLAUDE_HOME
# so logging and counter state never touch the real repo.

A1_TMP=$(mktemp -d)
A1_PROJ="$A1_TMP/proj"
mkdir -p "$A1_PROJ/.claude"
printf '1\n' > "$A1_PROJ/.claude/.delegate_counter"   # at budget (=1)
printf 'IMPLEMENT\n' > "$A1_PROJ/.claude/.phase"
A1_HOME="$A1_TMP/claude_home"
mkdir -p "$A1_HOME/logs"

A1_PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'tool_name': 'Edit',
    'tool_input': {'file_path': sys.argv[1] + '/src/app.py'},
    'cwd': sys.argv[1],
    'session_id': 'a1-sess',
    'agent_id': '',
    'agent_type': ''
}))
" "$A1_PROJ")

A1_STDOUT=$(env CLAUDE_HOME="$A1_HOME" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$A1_PAYLOAD" 2>/dev/null)
A1_RC=$?

A1_OK=true
A1_NOTES=""

if [[ "$A1_RC" != "0" ]]; then
    A1_OK=false
    A1_NOTES+=" over-budget rc=$A1_RC (expected 0, NOT 2);"
fi

# stdout must be exactly one clean JSON object with key additionalContext,
# and the nudge text must contain "delegate_gate:".
A1_JSON=$(printf '%s' "$A1_STDOUT" | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
try:
    obj = json.loads(raw)
except Exception as e:
    print('BAD_JSON:' + str(e)); sys.exit(0)
if not (isinstance(obj, dict) and isinstance(obj.get('additionalContext'), str) and obj['additionalContext']):
    print('NO_KEY'); sys.exit(0)
print('OK' if 'delegate_gate:' in obj['additionalContext'] else 'NO_PREFIX')
")
if [[ "$A1_JSON" != "OK" ]]; then
    A1_OK=false
    A1_NOTES+=" advisory JSON check=$A1_JSON (raw: $A1_STDOUT);"
fi

# Malformed stdin must STILL fail-closed with exit 2.
MALFORMED_RC=$(env CLAUDE_HOME="$A1_HOME" python3 "$GATE_PY" <<< 'not json{{{' >/dev/null 2>&1; echo $?)
if [[ "$MALFORMED_RC" != "2" ]]; then
    A1_OK=false
    A1_NOTES+=" malformed-stdin rc=$MALFORMED_RC (expected 2, fail-closed);"
fi

rm -rf "$A1_TMP"

if $A1_OK; then
    pass "A1 — over-budget → exit 0 + clean additionalContext JSON; malformed stdin → exit 2"
else
    fail "A1 — advisory contract fails:$A1_NOTES"
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
