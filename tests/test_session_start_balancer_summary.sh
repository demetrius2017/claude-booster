#!/usr/bin/env bash
# test_session_start_balancer_summary.sh
# Verifier: checks that memory_session_start.py emits === MODEL BALANCER === section
# Exit 0 = PASS, Exit 1 = FAIL

set -euo pipefail

SCRIPT="$HOME/.claude/scripts/memory_session_start.py"
TEMPLATE="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/memory_session_start.py"
MB_JSON="$HOME/.claude/model_balancer.json"
BACKUP="/tmp/mb_session_test_$$.json"
TEMP_SWAP="/tmp/mb_session_test_temp.json"

PASS=0
FAIL=0
FAILURES=()

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAILURES+=("$1"); FAIL=$((FAIL + 1)); }

# ── Cleanup trap ───────────────────────────────────────────────────────────────
trap 'cp "$BACKUP" "$MB_JSON" 2>/dev/null; rm -f "$TEMP_SWAP"; echo "Trap: original model_balancer.json restored."' EXIT

# Save original JSON up front (trap depends on this)
cp "$MB_JSON" "$BACKUP" 2>/dev/null || true
ORIGINAL_DATE=$(python3 -c "import json; print(json.load(open('$BACKUP')).get('decision_date','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")

echo "=== Verifier: test_session_start_balancer_summary ==="
echo "Script: $SCRIPT"
echo "Original decision_date: $ORIGINAL_DATE"
echo ""

# ── C1: Script is executable / invokable via python3 ─────────────────────────
echo "C1: Script executable / python3-invokable"
if python3 -c "
import ast, sys
with open('$SCRIPT') as f:
    src = f.read()
try:
    ast.parse(src)
    ok = True
except SyntaxError:
    ok = False
sys.exit(0 if ok else 1)
"; then
    pass "C1: Script parses cleanly as Python3 (no syntax errors)"
else
    fail "C1: Script has Python3 syntax errors"
fi

# ── C2: Template mirror exists ────────────────────────────────────────────────
echo "C2: Template mirror exists"
if [[ -f "$TEMPLATE" ]]; then
    pass "C2: Template mirror exists at $TEMPLATE"
else
    fail "C2: Template mirror missing at $TEMPLATE"
fi

# ── C3: Happy path — MODEL BALANCER section appears ──────────────────────────
echo "C3: Happy path with real model_balancer.json"
HAPPY_OUTPUT=$(echo '{}' | python3 "$SCRIPT" 2>&1)
HAPPY_EXIT=$?

if echo "$HAPPY_OUTPUT" | grep -q "=== MODEL BALANCER ==="; then
    pass "C3a: Output contains '=== MODEL BALANCER ==='"
else
    fail "C3a: Output missing '=== MODEL BALANCER ==='"
fi

# Check for date=, lead=, coding=, hard=, audit= within 3 lines of the header
# Strategy: extract lines after the header, check first 3
BALANCER_LINES=$(echo "$HAPPY_OUTPUT" | grep -A3 "=== MODEL BALANCER ===" || true)
if echo "$BALANCER_LINES" | grep -qE "date=.*lead=.*coding=.*hard=.*audit="; then
    pass "C3b: Single line contains date=, lead=, coding=, hard=, audit="
elif echo "$BALANCER_LINES" | grep -q "date=" && \
     echo "$BALANCER_LINES" | grep -q "lead=" && \
     echo "$BALANCER_LINES" | grep -q "coding=" && \
     echo "$BALANCER_LINES" | grep -q "hard=" && \
     echo "$BALANCER_LINES" | grep -q "audit="; then
    pass "C3b: All required fields (date, lead, coding, hard, audit) present within 3 lines of header"
else
    fail "C3b: Missing one or more of: date=, lead=, coding=, hard=, audit= within 3 lines of '=== MODEL BALANCER ==='"
fi

if echo "$BALANCER_LINES" | grep -q ":"; then
    pass "C3c: Output contains ':' (provider:model format)"
else
    fail "C3c: Missing ':' in MODEL BALANCER lines (expected provider:model format)"
fi

# ── C4: fresh vs stale freshness label ───────────────────────────────────────
echo "C4: fresh/stale correctness"
TODAY=$(python3 -c "import datetime; print(datetime.date.today().isoformat())")

# Build a fresh JSON (today's date, minimal routing)
python3 -c "
import json, datetime
d = {
  'schema_version': 2,
  'decision_date': '$TODAY',
  'routing': {
    'trivial': {'provider': 'codex-cli', 'model': 'gpt-5.3-codex-spark'},
    'medium':  {'provider': 'codex-cli', 'model': 'gpt-5.4-mini'},
    'coding':  {'provider': 'codex-cli', 'model': 'gpt-5.3-codex'},
    'hard':    {'provider': 'codex-cli', 'model': 'gpt-5.5'},
    'audit_external': {'provider': 'pal', 'model': 'gpt-5.5'},
    'lead':    {'provider': 'anthropic', 'model': 'claude-opus-4-7'}
  }
}
print(json.dumps(d))
" > "$MB_JSON"

FRESH_OUTPUT=$(echo '{}' | python3 "$SCRIPT" 2>&1)
if echo "$FRESH_OUTPUT" | grep -q "fresh"; then
    pass "C4a: today decision_date produces '(fresh)' label"
else
    fail "C4a: today decision_date should produce '(fresh)', got: $(echo "$FRESH_OUTPUT" | grep -A3 'MODEL BALANCER' || echo '[no MODEL BALANCER section]')"
fi

# Build a stale JSON (old date)
python3 -c "
import json
d = {
  'schema_version': 2,
  'decision_date': '2020-01-01',
  'routing': {
    'trivial': {'provider': 'codex-cli', 'model': 'gpt-5.3-codex-spark'},
    'medium':  {'provider': 'codex-cli', 'model': 'gpt-5.4-mini'},
    'coding':  {'provider': 'codex-cli', 'model': 'gpt-5.3-codex'},
    'hard':    {'provider': 'codex-cli', 'model': 'gpt-5.5'},
    'audit_external': {'provider': 'pal', 'model': 'gpt-5.5'},
    'lead':    {'provider': 'anthropic', 'model': 'claude-opus-4-7'}
  }
}
print(json.dumps(d))
" > "$MB_JSON"

STALE_OUTPUT=$(echo '{}' | python3 "$SCRIPT" 2>&1)
if echo "$STALE_OUTPUT" | grep -q "stale"; then
    pass "C4b: 2020-01-01 decision_date produces '(stale)' label"
else
    fail "C4b: 2020-01-01 decision_date should produce '(stale)', got: $(echo "$STALE_OUTPUT" | grep -A3 'MODEL BALANCER' || echo '[no MODEL BALANCER section]')"
fi

# Restore original before next test
cp "$BACKUP" "$MB_JSON"

# ── C5: Missing-file fallback ─────────────────────────────────────────────────
echo "C5: Missing-file fallback"
cp "$MB_JSON" "$TEMP_SWAP"
rm -f "$MB_JSON"

MISSING_OUTPUT=$(echo '{}' | python3 "$SCRIPT" 2>&1)
MISSING_EXIT=$?

if [[ $MISSING_EXIT -eq 0 ]]; then
    pass "C5a: Exit 0 when model_balancer.json is missing"
else
    fail "C5a: Expected exit 0 for missing JSON, got $MISSING_EXIT"
fi

if echo "$MISSING_OUTPUT" | grep -qi "MODEL BALANCER"; then
    pass "C5b: Output still contains MODEL BALANCER header on missing file"
else
    fail "C5b: Missing MODEL BALANCER header when JSON file absent"
fi

if echo "$MISSING_OUTPUT" | grep -qiE "no decision file|error|missing|not found|fallback"; then
    pass "C5c: Output contains a graceful fallback/error marker for missing file"
else
    fail "C5c: Expected fallback message (e.g. 'no decision file') when JSON absent"
fi

# Restore
cp "$TEMP_SWAP" "$MB_JSON"

# ── C6: Corrupt-JSON fallback ─────────────────────────────────────────────────
echo "C6: Corrupt-JSON fallback"
cp "$MB_JSON" "$TEMP_SWAP"
echo "not valid json {{{" > "$MB_JSON"

CORRUPT_OUTPUT=$(echo '{}' | python3 "$SCRIPT" 2>&1)
CORRUPT_EXIT=$?

if [[ $CORRUPT_EXIT -eq 0 ]]; then
    pass "C6a: Exit 0 when model_balancer.json is corrupt"
else
    fail "C6a: Expected exit 0 for corrupt JSON, got $CORRUPT_EXIT"
fi

if echo "$CORRUPT_OUTPUT" | grep -qi "MODEL BALANCER"; then
    pass "C6b: Output still contains MODEL BALANCER header on corrupt file"
else
    fail "C6b: Missing MODEL BALANCER header when JSON corrupt"
fi

if echo "$CORRUPT_OUTPUT" | grep -qiE "corrupt|invalid|parse|error|malform|decode"; then
    pass "C6c: Output contains a corruption-marker message"
else
    fail "C6c: Expected corruption marker (e.g. 'corrupt', 'parse error') for bad JSON"
fi

# Restore
cp "$TEMP_SWAP" "$MB_JSON"

# ── C7: Existing sections preserved ──────────────────────────────────────────
echo "C7: Existing sections still appear alongside MODEL BALANCER"
ALL_OUTPUT=$(echo '{}' | python3 "$SCRIPT" 2>&1)

HAS_EXISTING=0
if echo "$ALL_OUTPUT" | grep -qiE "DIRECTIVES|Rolling Memory|MEMORY|CONTEXT|TELEMETRY|ACTIVE"; then
    HAS_EXISTING=1
fi

if [[ $HAS_EXISTING -eq 1 ]]; then
    pass "C7a: At least one existing section (DIRECTIVES / Rolling Memory / etc.) still present"
else
    fail "C7a: No existing output sections detected — MODEL BALANCER may have displaced them"
fi

if echo "$ALL_OUTPUT" | grep -qi "MODEL BALANCER"; then
    pass "C7b: MODEL BALANCER also present (both coexist)"
else
    fail "C7b: MODEL BALANCER section missing in full-output run"
fi

# ── C8: Script always exits 0 ────────────────────────────────────────────────
echo "C8: Exit codes"
NORMAL_EXIT=$(echo '{}' | python3 "$SCRIPT" 2>&1; echo $?)
# The last line is the exit code since we combined
NORMAL_EC=$(echo '{}' | python3 "$SCRIPT" > /dev/null 2>&1; echo $?)
if [[ $NORMAL_EC -eq 0 ]]; then
    pass "C8a: Exit 0 with normal JSON"
else
    fail "C8a: Expected exit 0 with normal JSON, got $NORMAL_EC"
fi

cp "$MB_JSON" "$TEMP_SWAP"; rm -f "$MB_JSON"
MISSING_EC=$(echo '{}' | python3 "$SCRIPT" > /dev/null 2>&1; echo $?)
cp "$TEMP_SWAP" "$MB_JSON"
if [[ $MISSING_EC -eq 0 ]]; then
    pass "C8b: Exit 0 with missing JSON"
else
    fail "C8b: Expected exit 0 with missing JSON, got $MISSING_EC"
fi

cp "$MB_JSON" "$TEMP_SWAP"; echo "not valid json" > "$MB_JSON"
CORRUPT_EC=$(echo '{}' | python3 "$SCRIPT" > /dev/null 2>&1; echo $?)
cp "$TEMP_SWAP" "$MB_JSON"
if [[ $CORRUPT_EC -eq 0 ]]; then
    pass "C8c: Exit 0 with corrupt JSON"
else
    fail "C8c: Expected exit 0 with corrupt JSON, got $CORRUPT_EC"
fi

# ── C9 (bonus): Line count parity ────────────────────────────────────────────
echo "C9 (bonus): Live vs template line-count parity"
if [[ -f "$TEMPLATE" ]]; then
    LIVE_LC=$(wc -l < "$SCRIPT")
    TMPL_LC=$(wc -l < "$TEMPLATE")
    DIFF=$(( LIVE_LC - TMPL_LC ))
    ABS_DIFF=${DIFF#-}
    if [[ $ABS_DIFF -le 50 ]]; then
        pass "C9: Live ($LIVE_LC LOC) vs template ($TMPL_LC LOC) differ by $ABS_DIFF (within ±50)"
    else
        fail "C9: Live ($LIVE_LC LOC) vs template ($TMPL_LC LOC) differ by $ABS_DIFF — exceeds ±50 threshold (possible sync gap)"
    fi
else
    fail "C9: Template missing — cannot check parity"
fi

# ── C10 (bonus): Provider tokens appear in happy-path output ─────────────────
echo "C10 (bonus): Provider tokens in MODEL BALANCER line"
MB_LINE=$(echo '{}' | python3 "$SCRIPT" 2>&1 | grep -A3 "=== MODEL BALANCER ===" || true)
if echo "$MB_LINE" | grep -qiE "anthropic|codex-cli|pal"; then
    pass "C10: At least one of 'anthropic', 'codex-cli', 'pal' found in MODEL BALANCER output"
else
    fail "C10: None of 'anthropic', 'codex-cli', 'pal' found in MODEL BALANCER output"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=== RESULTS: $PASS passed, $FAIL failed ==="
if [[ ${#FAILURES[@]} -gt 0 ]]; then
    echo "FAILURES:"
    for f in "${FAILURES[@]}"; do
        echo "  - $f"
    done
fi

# Verify trap will restore correctly
RESTORED_DATE=$(python3 -c "import json; print(json.load(open('$MB_JSON')).get('decision_date','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
echo ""
echo "Current model_balancer.json decision_date: $RESTORED_DATE (expected: $ORIGINAL_DATE)"
if [[ "$RESTORED_DATE" == "$ORIGINAL_DATE" ]]; then
    echo "JSON integrity: OK (dates match)"
else
    echo "JSON integrity: WARNING — dates differ"
fi

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
