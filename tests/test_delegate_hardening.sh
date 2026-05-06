#!/usr/bin/env bash
# Acceptance test for delegate hardening:
# (1) telemetry_agent_health.py surfaces .delegate_mode bypass as ⚠/ok=false
# (2) memory_session_start.py resets .delegate_counter to 0 on SessionStart
#
# Tests OBSERVABLE BEHAVIOR only. Exit 0 = PASS, non-zero = FAIL.

set -e

TELEMETRY="python3 /Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/telemetry_agent_health.py"
SESSION_START="python3 /Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/memory_session_start.py"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Setup: create isolated temp project dirs
# ---------------------------------------------------------------------------
TMPDIR_BASE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_BASE"' EXIT

# Each test scenario gets its own project dir with .claude/ sub-dir
PROJECT_BYPASS="$TMPDIR_BASE/proj_bypass"
PROJECT_NO_BYPASS="$TMPDIR_BASE/proj_no_bypass"
PROJECT_COUNTER="$TMPDIR_BASE/proj_counter"
PROJECT_NO_COUNTER="$TMPDIR_BASE/proj_no_counter"

for d in "$PROJECT_BYPASS" "$PROJECT_NO_BYPASS" "$PROJECT_COUNTER" "$PROJECT_NO_COUNTER"; do
    mkdir -p "$d/.claude" "$d/reports"
done

# ---------------------------------------------------------------------------
# SCENARIO 1: telemetry prose — bypass file with "off" → output contains ⚠
# ---------------------------------------------------------------------------
echo "off" > "$PROJECT_BYPASS/.claude/.delegate_mode"

PROSE_OUTPUT=$($TELEMETRY --project "$PROJECT_BYPASS" 2>/dev/null)

if echo "$PROSE_OUTPUT" | grep -q "⚠"; then
    pass "prose output contains ⚠ when .delegate_mode=off"
else
    fail "prose output missing ⚠ when .delegate_mode=off"
    echo "  Got: $(echo "$PROSE_OUTPUT" | tail -20)"
fi

# ---------------------------------------------------------------------------
# SCENARIO 2: telemetry prose — no bypass file → output contains ✓ for that signal
# ---------------------------------------------------------------------------
PROSE_NO_BYPASS=$($TELEMETRY --project "$PROJECT_NO_BYPASS" 2>/dev/null)

# The delegate bypass file signal should appear in prose and show ✓ (no bypass)
if echo "$PROSE_NO_BYPASS" | grep -qi "delegate.*✓\|✓.*delegate"; then
    pass "prose output contains ✓ for delegate signal when .delegate_mode absent"
else
    # Also acceptable: signal line present with ✓ at any position on the line
    if echo "$PROSE_NO_BYPASS" | grep -qi "delegate" && echo "$PROSE_NO_BYPASS" | grep -qi "✓"; then
        pass "prose output contains delegate line and ✓ when .delegate_mode absent"
    else
        fail "prose output missing delegate signal with ✓ when .delegate_mode absent"
        echo "  Got: $(echo "$PROSE_NO_BYPASS" | tail -20)"
    fi
fi

# ---------------------------------------------------------------------------
# SCENARIO 3: telemetry JSON — bypass file with "off" → signal has ok=false
# ---------------------------------------------------------------------------
JSON_OUTPUT=$($TELEMETRY --project "$PROJECT_BYPASS" --json 2>/dev/null)

if echo "$JSON_OUTPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
signals = data.get('signals', {})
dbf = signals.get('delegate_bypass_file')
if dbf is None:
    print('MISSING: delegate_bypass_file signal absent from JSON')
    sys.exit(1)
if dbf.get('ok') != False:
    print(f'WRONG ok value: expected False, got {dbf.get(\"ok\")}')
    sys.exit(1)
print('ok=false confirmed')
sys.exit(0)
" 2>&1; then
    pass "JSON output has delegate_bypass_file signal with ok=false when .delegate_mode=off"
else
    fail "JSON output missing or wrong for delegate_bypass_file signal when .delegate_mode=off"
    echo "  JSON signals: $(echo "$JSON_OUTPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(list(d.get("signals",{}).keys()))' 2>/dev/null || echo '(parse error)')"
fi

# ---------------------------------------------------------------------------
# SCENARIO 4: telemetry JSON — no bypass file → signal has ok=true
# ---------------------------------------------------------------------------
JSON_NO_BYPASS=$($TELEMETRY --project "$PROJECT_NO_BYPASS" --json 2>/dev/null)

if echo "$JSON_NO_BYPASS" | python3 -c "
import json, sys
data = json.load(sys.stdin)
signals = data.get('signals', {})
dbf = signals.get('delegate_bypass_file')
if dbf is None:
    print('MISSING: delegate_bypass_file signal absent from JSON')
    sys.exit(1)
if dbf.get('ok') != True:
    print(f'WRONG ok value: expected True, got {dbf.get(\"ok\")}')
    sys.exit(1)
print('ok=true confirmed')
sys.exit(0)
" 2>&1; then
    pass "JSON output has delegate_bypass_file signal with ok=true when .delegate_mode absent"
else
    fail "JSON output missing or wrong for delegate_bypass_file signal when .delegate_mode absent"
fi

# ---------------------------------------------------------------------------
# SCENARIO 5: session_start resets .delegate_counter from non-zero to 0
# ---------------------------------------------------------------------------
echo "5" > "$PROJECT_COUNTER/.claude/.delegate_counter"

COUNTER_FILE="$PROJECT_COUNTER/.claude/.delegate_counter"

# Run session_start with cwd pointing at the project dir
SESSION_OUT=$(echo "{\"session_id\": \"test-session\", \"cwd\": \"$PROJECT_COUNTER\"}" | $SESSION_START 2>/dev/null)

# Verify output is valid JSON with expected structure (behavior unchanged)
if echo "$SESSION_OUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
hso = data.get('hookSpecificOutput', {})
if hso.get('hookEventName') != 'SessionStart':
    print(f'Wrong hookEventName: {hso.get(\"hookEventName\")}')
    sys.exit(1)
if 'additionalContext' not in hso:
    print('Missing additionalContext key')
    sys.exit(1)
print('valid JSON structure confirmed')
sys.exit(0)
" 2>&1; then
    pass "session_start still outputs valid additionalContext JSON after counter reset"
else
    fail "session_start broke additionalContext JSON output format"
    echo "  Got: $SESSION_OUT"
fi

# Verify counter was reset to 0
if [[ -f "$COUNTER_FILE" ]]; then
    COUNTER_VAL=$(cat "$COUNTER_FILE")
    if [[ "$COUNTER_VAL" == "0" ]]; then
        pass "counter file reset to 0 (was 5)"
    else
        fail "counter file not reset: expected '0', got '$COUNTER_VAL'"
    fi
else
    fail "counter file disappeared after session_start (expected to remain as '0')"
fi

# ---------------------------------------------------------------------------
# SCENARIO 6: session_start does NOT create .delegate_counter when absent
# ---------------------------------------------------------------------------
NO_COUNTER_FILE="$PROJECT_NO_COUNTER/.claude/.delegate_counter"

# Ensure counter file does not exist
rm -f "$NO_COUNTER_FILE"

echo "{\"session_id\": \"test-session-2\", \"cwd\": \"$PROJECT_NO_COUNTER\"}" | $SESSION_START 2>/dev/null > /dev/null

if [[ -f "$NO_COUNTER_FILE" ]]; then
    fail "session_start created .delegate_counter when it did not previously exist"
else
    pass "session_start does not create .delegate_counter when absent"
fi

# ---------------------------------------------------------------------------
# SCENARIO 7: session_start does not crash on missing/invalid cwd
# ---------------------------------------------------------------------------
CRASH_OUT=$(echo '{"session_id": "test-crash", "cwd": "/nonexistent/path/that/does/not/exist"}' | $SESSION_START 2>/dev/null; echo "EXIT:$?")
EXIT_CODE=$(echo "$CRASH_OUT" | grep "EXIT:" | sed 's/EXIT://')

if [[ "$EXIT_CODE" == "0" ]]; then
    pass "session_start exits 0 (no crash) with nonexistent cwd"
else
    fail "session_start crashed (exit $EXIT_CODE) on nonexistent cwd"
fi

# Check it still produced valid JSON output
JSON_PART=$(echo "$CRASH_OUT" | grep -v "EXIT:" | tr -d '\n')
if echo "$JSON_PART" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    pass "session_start outputs valid JSON even with nonexistent cwd"
else
    fail "session_start output is not valid JSON with nonexistent cwd"
    echo "  Got: $JSON_PART"
fi

# ---------------------------------------------------------------------------
# SCENARIO 8: telemetry prose — signal appears on its own line (not buried)
# ---------------------------------------------------------------------------
PROSE_BYPASS=$($TELEMETRY --project "$PROJECT_BYPASS" 2>/dev/null)
DELEGATE_LINE=$(echo "$PROSE_BYPASS" | grep -i "delegate" || true)

if [[ -n "$DELEGATE_LINE" ]]; then
    pass "delegate bypass signal has its own line in prose output"
    # Verify the line contains ⚠ specifically for the bypass=off case
    if echo "$DELEGATE_LINE" | grep -q "⚠"; then
        pass "delegate bypass line in prose contains ⚠ marker"
    else
        fail "delegate bypass line in prose does not contain ⚠ marker — line: $DELEGATE_LINE"
    fi
else
    fail "no delegate-related line found in prose output when .delegate_mode=off"
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
