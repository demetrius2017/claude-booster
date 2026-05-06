#!/usr/bin/env bash
# Acceptance test for delegate_gate.py session-scoped TTL for .delegate_mode bypass.
#
# Tests OBSERVABLE BEHAVIOR of _mode_disabled(root, session_id) only.
# Does NOT test the full gate flow, counter logic, or hook wiring.
#
# Exit 0 = all assertions PASS. Non-zero = at least one FAIL.

set -e

ARTIFACT="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/delegate_gate.py"
HORIZON_BYPASS="/Users/dmitrijnazarov/Projects/horizon/.claude/.delegate_mode"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Preflight: artifact must exist and be importable
# ---------------------------------------------------------------------------
if [[ ! -f "$ARTIFACT" ]]; then
    echo "FATAL: artifact not found at $ARTIFACT"
    exit 1
fi

# Verify _mode_disabled accepts two arguments (root, session_id) without TypeError.
# We check this by inspecting the function signature via inspect.signature.
SIG_CHECK=$(python3 - <<'PYEOF'
import sys, inspect
sys.path.insert(0, "/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts")
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "delegate_gate",
    "/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/delegate_gate.py"
)
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"IMPORT_ERROR:{e}")
    sys.exit(1)
if not hasattr(mod, "_mode_disabled"):
    print("MISSING_FUNCTION")
    sys.exit(1)
sig = inspect.signature(mod._mode_disabled)
params = list(sig.parameters.keys())
if len(params) < 2:
    print(f"WRONG_ARITY:{params}")
    sys.exit(1)
print("OK")
PYEOF
)

if [[ "$SIG_CHECK" != "OK" ]]; then
    echo "FATAL: _mode_disabled signature check failed — $SIG_CHECK"
    echo "       Expected _mode_disabled(root, session_id). Worker may not have applied the change yet."
    exit 1
fi

pass "_mode_disabled(root, session_id) signature is present and importable"

# ---------------------------------------------------------------------------
# Helper: call _mode_disabled(root_path, session_id) → prints "True" or "False"
# ---------------------------------------------------------------------------
call_mode_disabled() {
    local root_dir="$1"
    local session_id="$2"
    python3 - "$root_dir" "$session_id" <<'PYEOF'
import sys, importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "delegate_gate",
    "/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/delegate_gate.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
root = pathlib.Path(sys.argv[1])
session_id = sys.argv[2]
result = mod._mode_disabled(root, session_id)
print(result)
PYEOF
}

# ---------------------------------------------------------------------------
# Setup: isolated temp dirs, cleaned up on exit
# ---------------------------------------------------------------------------
TMPBASE=$(mktemp -d)
trap 'rm -rf "$TMPBASE"' EXIT

mk_project() {
    local name="$1"
    local dir="$TMPBASE/$name"
    mkdir -p "$dir/.claude"
    echo "$dir"
}

# ---------------------------------------------------------------------------
# SCENARIO 1: bare "off" (legacy format, no session) → False (expired/ignored)
# ---------------------------------------------------------------------------
PROJ1=$(mk_project "bare_off")
echo "off" > "$PROJ1/.claude/.delegate_mode"

RESULT1=$(call_mode_disabled "$PROJ1" "sess-123")
if [[ "$RESULT1" == "False" ]]; then
    pass "bare 'off' with any session_id → False (legacy bypass ignored)"
else
    fail "bare 'off' with any session_id → expected False, got '$RESULT1'"
fi

# Also test that bare "off" is ignored regardless of session value
RESULT1B=$(call_mode_disabled "$PROJ1" "")
if [[ "$RESULT1B" == "False" ]]; then
    pass "bare 'off' with empty session_id → False (legacy bypass still ignored)"
else
    fail "bare 'off' with empty session_id → expected False, got '$RESULT1B'"
fi

# ---------------------------------------------------------------------------
# SCENARIO 2: "off:sess-123" with matching session → True (honoured)
# ---------------------------------------------------------------------------
PROJ2=$(mk_project "matching_session")
echo "off:sess-123" > "$PROJ2/.claude/.delegate_mode"

RESULT2=$(call_mode_disabled "$PROJ2" "sess-123")
if [[ "$RESULT2" == "True" ]]; then
    pass "'off:sess-123' with session='sess-123' → True (matching session honoured)"
else
    fail "'off:sess-123' with session='sess-123' → expected True, got '$RESULT2'"
fi

# ---------------------------------------------------------------------------
# SCENARIO 3: "off:sess-456" with different session → False (foreign session)
# ---------------------------------------------------------------------------
PROJ3=$(mk_project "mismatched_session")
echo "off:sess-456" > "$PROJ3/.claude/.delegate_mode"

RESULT3=$(call_mode_disabled "$PROJ3" "sess-123")
if [[ "$RESULT3" == "False" ]]; then
    pass "'off:sess-456' with session='sess-123' → False (session mismatch, ignored)"
else
    fail "'off:sess-456' with session='sess-123' → expected False, got '$RESULT3'"
fi

# ---------------------------------------------------------------------------
# SCENARIO 4: no file at all → False
# ---------------------------------------------------------------------------
PROJ4=$(mk_project "no_file")
# No .delegate_mode file created

RESULT4=$(call_mode_disabled "$PROJ4" "sess-123")
if [[ "$RESULT4" == "False" ]]; then
    pass "no .delegate_mode file → False"
else
    fail "no .delegate_mode file → expected False, got '$RESULT4'"
fi

# ---------------------------------------------------------------------------
# SCENARIO 5: edge case — "off:" with empty session_id
# Must not crash; True or False both acceptable per contract.
# ---------------------------------------------------------------------------
PROJ5=$(mk_project "empty_session_in_file")
echo "off:" > "$PROJ5/.claude/.delegate_mode"

RESULT5=$(call_mode_disabled "$PROJ5" "" 2>&1)
if [[ "$RESULT5" == "True" || "$RESULT5" == "False" ]]; then
    pass "'off:' with empty session_id → '$RESULT5' (no crash; either value acceptable)"
else
    fail "'off:' with empty session_id → crashed or unexpected output: '$RESULT5'"
fi

# ---------------------------------------------------------------------------
# SCENARIO 6: Horizon stale bypass file must not exist
# ---------------------------------------------------------------------------
if [[ -f "$HORIZON_BYPASS" ]]; then
    fail "Horizon stale bypass file still exists at $HORIZON_BYPASS — must be deleted"
else
    pass "Horizon stale bypass file absent at $HORIZON_BYPASS"
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
