#!/usr/bin/env bash
# test_session_start_limits_summary.sh
# Verifier: checks that memory_session_start.py emits a correct === LIMITS === block.
# Exit 0 = PASS (all assertions pass). Exit 1 = FAIL (prints failing assertions).

set -uo pipefail

SCRIPT="$HOME/.claude/scripts/memory_session_start.py"
TEMPLATE="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/memory_session_start.py"
DB="$HOME/.claude/rolling_memory.db"
MB_JSON="$HOME/.claude/model_balancer.json"
MB_BACKUP="/tmp/mb_limits_test_$$.json"
PASS=0
FAIL=0
FAILURES=()

# ── Backup & cleanup trap ─────────────────────────────────────────────────────
cp "$MB_JSON" "$MB_BACKUP" 2>/dev/null || true
trap 'cp "$MB_BACKUP" "$MB_JSON" 2>/dev/null || true
      sqlite3 "$DB" "DELETE FROM model_metrics WHERE provider=\"test-marker-zzz\"" 2>/dev/null || true' EXIT

# ── Helpers ───────────────────────────────────────────────────────────────────
assert() {
  local id="$1" desc="$2" result="$3"
  if [[ "$result" == "ok" ]]; then
    echo "[PASS] $id: $desc"
    PASS=$(( PASS + 1 ))
  else
    echo "[FAIL] $id: $desc — $result"
    FAILURES+=("$id: $desc — $result")
    FAIL=$(( FAIL + 1 ))
  fi
}

# Run script and unwrap JSON to extract additionalContext text
run_script() {
  echo '{}' | python3 "$SCRIPT" 2>&1 | python3 -c "
import sys, json
raw = sys.stdin.read()
try:
    data = json.loads(raw)
    ctx = data.get('hookSpecificOutput', {}).get('additionalContext', raw)
except Exception:
    ctx = raw
print(ctx)
"
}

# Extract LIMITS block bullets (lines between === LIMITS === and next === header)
extract_limits_bullets() {
  local text="$1"
  echo "$text" | awk '/^=== LIMITS ===$/{found=1; next} found && /^=== /{found=0; next} found && /^  \* /{print}'
}

# ── Baseline output (used by most assertions) ─────────────────────────────────
BASELINE=$(run_script)
LIMITS_BULLETS=$(extract_limits_bullets "$BASELINE")

# ── C1: Script runs, exit 0 ───────────────────────────────────────────────────
if echo '{}' | python3 "$SCRIPT" >/dev/null 2>&1; then
  assert C1 "Script exits 0 with empty stdin" ok
else
  EXIT_CODE=$?
  assert C1 "Script exits 0 with empty stdin" "non-zero exit: $EXIT_CODE"
fi

# ── C2: Template mirror exists ────────────────────────────────────────────────
if [[ -f "$TEMPLATE" ]]; then
  assert C2 "Template mirror exists" ok
else
  assert C2 "Template mirror exists" "file not found: $TEMPLATE"
fi

# ── C3: Output contains === LIMITS === header ─────────────────────────────────
if echo "$BASELINE" | grep -q '=== LIMITS ==='; then
  assert C3 "Output contains '=== LIMITS ===' header" ok
else
  assert C3 "Output contains '=== LIMITS ===' header" "header not found in output"
fi

# ── C4: Exactly 4 bullet lines under LIMITS header ────────────────────────────
BULLET_COUNT=$(echo "$LIMITS_BULLETS" | grep -c '.' 2>/dev/null || true)
if [[ "$BULLET_COUNT" -eq 4 ]]; then
  assert C4 "Exactly 4 '  * ' lines under LIMITS header" ok
else
  assert C4 "Exactly 4 '  * ' lines under LIMITS header" "found $BULLET_COUNT lines; bullets='$(echo "$LIMITS_BULLETS" | tr '\n' '|')'"
fi

# ── C5: First LIMITS line contains required substrings ───────────────────────
LINE1=$(echo "$LIMITS_BULLETS" | sed -n '1p')
if echo "$LINE1" | grep -q '5h window:' \
   && echo "$LINE1" | grep -q 'anthropic' \
   && echo "$LINE1" | grep -q 'tokens' \
   && echo "$LINE1" | grep -q 'calls' \
   && echo "$LINE1" | grep -q 'codex-cli'; then
  assert C5 "Line 1 has: 5h window, anthropic, tokens, calls, codex-cli" ok
else
  assert C5 "Line 1 has: 5h window, anthropic, tokens, calls, codex-cli" "got: '$LINE1'"
fi

# ── C6: Second LIMITS line contains /lead supervisor + state= + valid state ───
LINE2=$(echo "$LIMITS_BULLETS" | sed -n '2p')
if echo "$LINE2" | grep -q '/lead supervisor:' \
   && echo "$LINE2" | grep -q 'state=' \
   && echo "$LINE2" | grep -qE 'state=(closed|half_open|open|inactive)'; then
  assert C6 "Line 2 has: /lead supervisor, state=<valid>" ok
else
  assert C6 "Line 2 has: /lead supervisor, state=<valid>" "got: '$LINE2'"
fi

# ── C7: Third LIMITS line contains weekly_max_snapshot + % or unknown ─────────
LINE3=$(echo "$LIMITS_BULLETS" | sed -n '3p')
if echo "$LINE3" | grep -q 'weekly_max_snapshot:' \
   && echo "$LINE3" | grep -qE '%|unknown'; then
  assert C7 "Line 3 has: weekly_max_snapshot, (% or unknown)" ok
else
  assert C7 "Line 3 has: weekly_max_snapshot, (% or unknown)" "got: '$LINE3'"
fi

# ── C8: Fourth LIMITS line contains codex_pro_quota placeholder ───────────────
LINE4=$(echo "$LIMITS_BULLETS" | sed -n '4p')
if echo "$LINE4" | grep -q 'codex_pro_quota:' \
   && echo "$LINE4" | grep -q 'no source' \
   && echo "$LINE4" | grep -q 'day-N'; then
  assert C8 "Line 4 has: codex_pro_quota, (no source, day-N)" ok
else
  assert C8 "Line 4 has: codex_pro_quota, (no source, day-N)" "got: '$LINE4'"
fi

# ── C9: LIMITS block appears AFTER MODEL BALANCER block ───────────────────────
MB_LINE=$(echo "$BASELINE" | grep -n '=== MODEL BALANCER ===' | head -1 | cut -d: -f1)
LIM_LINE=$(echo "$BASELINE" | grep -n '=== LIMITS ===' | head -1 | cut -d: -f1)
if [[ -n "$MB_LINE" && -n "$LIM_LINE" && "$LIM_LINE" -gt "$MB_LINE" ]]; then
  assert C9 "LIMITS block appears after MODEL BALANCER block (line $LIM_LINE > $MB_LINE)" ok
else
  assert C9 "LIMITS block appears after MODEL BALANCER block" "MODEL_BALANCER=${MB_LINE:-missing}, LIMITS=${LIM_LINE:-missing}"
fi

# ── C10: MODEL BALANCER block still present ───────────────────────────────────
if echo "$BASELINE" | grep -q '=== MODEL BALANCER ==='; then
  assert C10 "MODEL BALANCER block still present" ok
else
  assert C10 "MODEL BALANCER block still present" "not found in output"
fi

# ── C11: DIRECTIVES and FEEDBACK sections still present ───────────────────────
DIRS_OK=false; FEED_OK=false
echo "$BASELINE" | grep -q '=== DIRECTIVES ===' && DIRS_OK=true || true
echo "$BASELINE" | grep -q '=== FEEDBACK ===' && FEED_OK=true || true
if $DIRS_OK && $FEED_OK; then
  assert C11 "DIRECTIVES and FEEDBACK sections still present" ok
else
  assert C11 "DIRECTIVES and FEEDBACK sections still present" "DIRECTIVES=$DIRS_OK FEEDBACK=$FEED_OK"
fi

# ── C12: Missing model_balancer.json — script exits 0 and degrades gracefully ─
mv "$MB_JSON" "${MB_JSON}.aside_c12" 2>/dev/null || true
OUT_C12=$(run_script)
EXIT_C12=$?
mv "${MB_JSON}.aside_c12" "$MB_JSON" 2>/dev/null || true

if [[ $EXIT_C12 -eq 0 ]]; then
  assert C12a "Exit 0 when model_balancer.json missing" ok
else
  assert C12a "Exit 0 when model_balancer.json missing" "exit code: $EXIT_C12"
fi

if echo "$OUT_C12" | grep -q '=== LIMITS ==='; then
  assert C12b "LIMITS header present when model_balancer.json missing" ok
else
  assert C12b "LIMITS header present when model_balancer.json missing" "header not found"
fi

WEEKLY_C12=$(extract_limits_bullets "$OUT_C12" | sed -n '3p')
if echo "$WEEKLY_C12" | grep -qi 'unknown'; then
  assert C12c "weekly_max_snapshot shows 'unknown' when JSON missing" ok
else
  assert C12c "weekly_max_snapshot shows 'unknown' when JSON missing" "got: '$WEEKLY_C12'"
fi

# ── C13: Corrupt model_balancer.json — script exits 0, LIMITS block present ───
cp "$MB_JSON" "${MB_JSON}.aside_c13" 2>/dev/null || true
printf 'not valid {' > "$MB_JSON"
OUT_C13=$(run_script)
EXIT_C13=$?
mv "${MB_JSON}.aside_c13" "$MB_JSON" 2>/dev/null || true

if [[ $EXIT_C13 -eq 0 ]]; then
  assert C13a "Exit 0 when model_balancer.json is corrupt" ok
else
  assert C13a "Exit 0 when model_balancer.json is corrupt" "exit code: $EXIT_C13"
fi

if echo "$OUT_C13" | grep -q '=== LIMITS ==='; then
  assert C13b "LIMITS block present with corrupt model_balancer.json" ok
else
  assert C13b "LIMITS block present with corrupt model_balancer.json" "header not found"
fi

# ── C14: model_metrics marker row — 5h window line shows a number (not crash) ─
sqlite3 "$DB" "INSERT INTO model_metrics (ts_utc, provider, model, tokens_in, tokens_out, success)
               VALUES (datetime('now'), 'test-marker-zzz', 'test-model', 100, 50, 1)" 2>/dev/null || true

OUT_C14=$(run_script)
LIM1_C14=$(extract_limits_bullets "$OUT_C14" | sed -n '1p')

# Should contain at least one number followed by 'k'
if echo "$LIM1_C14" | grep -qE '[0-9]+k'; then
  assert C14 "5h window line shows a number (Nk) after inserting test row" ok
else
  assert C14 "5h window line shows a number (Nk) after inserting test row" "got: '$LIM1_C14'"
fi
# marker row cleanup handled by trap EXIT

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: $PASS passed, $FAIL failed (14 assertions total)"
if [[ $FAIL -eq 0 ]]; then
  echo "STATUS: PASS"
  exit 0
else
  echo "STATUS: FAIL"
  echo ""
  echo "Failed assertions:"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
