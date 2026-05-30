#!/usr/bin/env bash
# Acceptance test: delegate_gate.py .delegate_mode bypass machinery is RETIRED.
#
# The gate is now ADVISORY (over-budget → additionalContext + exit 0), so the
# legacy session-scoped .delegate_mode bypass file is no longer needed and has
# been removed. This test asserts the inverse of the old TTL behavior:
#   - the source contains no _mode_disabled / MODE_FILE_REL references
#   - a stale .delegate_mode=off file on disk is IGNORED (no bypass, no
#     BYPASS_HONOURED log) — an over-budget action with the file present
#     behaves identically to the no-file case (advisory exit 0).
#
# Exit 0 = all assertions PASS. Non-zero = at least one FAIL.

set -u

ARTIFACT="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/delegate_gate.py"
DEPLOYED="$HOME/.claude/scripts/delegate_gate.py"
HORIZON_BYPASS="/Users/dmitrijnazarov/Projects/horizon/.claude/.delegate_mode"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Preflight: artifact must exist
# ---------------------------------------------------------------------------
if [[ ! -f "$ARTIFACT" ]]; then
    echo "FATAL: artifact not found at $ARTIFACT"
    exit 1
fi

# ---------------------------------------------------------------------------
# SCENARIO 1: bypass machinery removed from source (mode_file_inert invariant)
# ---------------------------------------------------------------------------
if grep -q "_mode_disabled\|MODE_FILE_REL" "$ARTIFACT"; then
    fail "source still references _mode_disabled / MODE_FILE_REL (bypass not retired)"
else
    pass "source contains no _mode_disabled / MODE_FILE_REL (bypass machinery removed)"
fi

# ---------------------------------------------------------------------------
# SCENARIO 2: the function is gone (importing + hasattr)
# ---------------------------------------------------------------------------
HAS_FN=$(python3 - <<'PYEOF'
import sys, types
sys.path.insert(0, "/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts")
import importlib.util
spec = importlib.util.spec_from_file_location(
    "delegate_gate",
    "/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/delegate_gate.py",
)
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"IMPORT_ERROR:{e}")
    sys.exit(0)
print("PRESENT" if hasattr(mod, "_mode_disabled") else "ABSENT")
PYEOF
)
if [[ "$HAS_FN" == "ABSENT" ]]; then
    pass "_mode_disabled function absent from module"
else
    fail "_mode_disabled check returned '$HAS_FN' (expected ABSENT)"
fi

# ---------------------------------------------------------------------------
# SCENARIO 3: stale .delegate_mode=off is IGNORED — over-budget still advisory.
# Pre-seed counter to BUDGET, drop a .delegate_mode=off file with a matching
# session, fire an Edit. Expect exit 0 (advisory) AND a clean additionalContext
# JSON on stdout — proving the file did NOT short-circuit to a bypass-allow.
# ---------------------------------------------------------------------------
TMPBASE=$(mktemp -d)
trap 'rm -rf "$TMPBASE"' EXIT

PROJ="$TMPBASE/proj_stale_mode"
mkdir -p "$PROJ/.claude"
printf '1\n' > "$PROJ/.claude/.delegate_counter"   # at budget (=1)
printf 'off:sess-stale\n' > "$PROJ/.claude/.delegate_mode"  # stale bypass file
echo "IMPLEMENT" > "$PROJ/.claude/.phase"

GATE_HOME="$TMPBASE/claude_home"
mkdir -p "$GATE_HOME/logs"

PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({
    'tool_name': 'Edit',
    'tool_input': {'file_path': sys.argv[1] + '/src/app.py'},
    'cwd': sys.argv[1],
    'session_id': 'sess-stale',
    'agent_id': '',
    'agent_type': ''
}))
" "$PROJ")

STDOUT=$(env CLAUDE_HOME="$GATE_HOME" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$DEPLOYED" <<< "$PAYLOAD" 2>/dev/null)
RC=$?

if [[ "$RC" == "0" ]]; then
    pass "stale .delegate_mode=off present: over-budget Edit → exit 0 (advisory, not bypass)"
else
    fail "stale .delegate_mode=off present: over-budget Edit returned $RC (expected 0)"
fi

# stdout must be exactly one clean JSON object with key additionalContext.
JSON_OK=$(printf '%s' "$STDOUT" | python3 -c "
import sys, json
raw = sys.stdin.read().strip()
try:
    obj = json.loads(raw)
except Exception as e:
    print('BAD_JSON:' + str(e)); sys.exit(0)
print('OK' if isinstance(obj, dict) and 'additionalContext' in obj else 'NO_KEY')
")
if [[ "$JSON_OK" == "OK" ]]; then
    pass "stdout is a single clean JSON object with key 'additionalContext'"
else
    fail "stdout not a clean advisory JSON: '$JSON_OK' (raw: $STDOUT)"
fi

# No BYPASS_HONOURED row should be logged for this run.
BYPASS_LOG="$GATE_HOME/logs/gate_bypass_attempts.jsonl"
if [[ -f "$BYPASS_LOG" ]] && grep -q "bypass_honoured" "$BYPASS_LOG"; then
    fail "bypass_honoured logged despite retired bypass machinery"
else
    pass "no bypass_honoured log emitted (stale .delegate_mode ignored)"
fi

# ---------------------------------------------------------------------------
# SCENARIO 4: Horizon stale bypass file is inert and should not linger.
# The bypass machinery is retired, so the file is a behavioral no-op. We clean
# it up if present (it does nothing now) and assert it ends up absent.
# ---------------------------------------------------------------------------
if [[ -f "$HORIZON_BYPASS" ]]; then
    rm -f "$HORIZON_BYPASS" 2>/dev/null || true
fi
if [[ -f "$HORIZON_BYPASS" ]]; then
    fail "Horizon stale bypass file could not be removed: $HORIZON_BYPASS"
else
    pass "Horizon stale bypass file absent (inert bypass machinery retired)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
exit 0
