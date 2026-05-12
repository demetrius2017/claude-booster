#!/usr/bin/env bash
# test_model_metric_capture.sh
# Verifier acceptance test for model_metric_capture.py PostToolUse hook.
# Contract: captures per-tool-call latency + tokens into model_metrics table.
#
# Exit 0 = PASS (all assertions passed)
# Exit 1 = FAIL (one or more assertions failed)
#
# Run from repo root or anywhere:
#   bash /Users/dmitrijnazarov/Projects/Claude_Booster/tests/test_model_metric_capture.sh

set -uo pipefail

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_PATH="$HOME/.claude/scripts/model_metric_capture.py"
TEMPLATE_PATH="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/model_metric_capture.py"
DB="$HOME/.claude/rolling_memory.db"

TOTAL=11
PASS=0
FAIL=0

# ── helpers ───────────────────────────────────────────────────────────────────

pass() { echo "  PASS  case $1/$TOTAL: $2"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  case $1/$TOTAL: $2"; FAIL=$((FAIL + 1)); }

# Count rows with a specific session_id
count_rows() {
    sqlite3 "$DB" "SELECT COUNT(*) FROM model_metrics WHERE session_id='$1';" 2>/dev/null || echo "0"
}

# Count total rows in model_metrics
total_rows() {
    sqlite3 "$DB" "SELECT COUNT(*) FROM model_metrics;" 2>/dev/null || echo "0"
}

# Detect which timestamp column name the Worker used: ts_utc (contract) or ts (existing schema)
ts_col() {
    local cols
    cols=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>/dev/null | awk -F'|' '{print $2}')
    if echo "$cols" | grep -q "^ts_utc$"; then
        echo "ts_utc"
    else
        echo "ts"
    fi
}

# Detect which per-turn column name the Worker used: per_turn_ms (contract) or duration_per_turn_ms (existing)
per_turn_col() {
    local cols
    cols=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>/dev/null | awk -F'|' '{print $2}')
    if echo "$cols" | grep -q "^per_turn_ms$"; then
        echo "per_turn_ms"
    else
        echo "duration_per_turn_ms"
    fi
}

# Get a specific column value from a row by session_id
get_col() {
    local session_id="$1"
    local col="$2"
    sqlite3 "$DB" "SELECT ${col} FROM model_metrics WHERE session_id='${session_id}' LIMIT 1;" 2>/dev/null || echo ""
}

# Cleanup marker — all test rows use session_id matching 'test-mmc-%'
cleanup() {
    sqlite3 "$DB" "DELETE FROM model_metrics WHERE session_id LIKE 'test-mmc-%';" 2>/dev/null || true
}

# ── preamble ──────────────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "  model_metric_capture — acceptance test ($TOTAL cases)"
echo "============================================================"
echo ""

BASELINE=$(total_rows)
echo "  NOTE  model_metrics baseline row count: $BASELINE"
echo ""

# ── C1: script exists and is executable ───────────────────────────────────────

CASE=1
if [[ -f "$SCRIPT_PATH" && -x "$SCRIPT_PATH" ]]; then
    # Also verify python3 shebang
    SHEBANG=$(head -1 "$SCRIPT_PATH")
    if echo "$SHEBANG" | grep -q "python3"; then
        pass $CASE "Script exists at $SCRIPT_PATH, is executable, has python3 shebang"
    else
        fail $CASE "Script exists + executable but shebang is '$SHEBANG' (expected python3)"
    fi
else
    if [[ ! -f "$SCRIPT_PATH" ]]; then
        fail $CASE "Script NOT found at $SCRIPT_PATH"
    else
        fail $CASE "Script found at $SCRIPT_PATH but is NOT executable (needs chmod +x)"
    fi
fi

# ── C2: template mirror exists ────────────────────────────────────────────────

CASE=2
if [[ -f "$TEMPLATE_PATH" ]]; then
    pass $CASE "Template mirror exists at $TEMPLATE_PATH"
else
    fail $CASE "Template mirror NOT found at $TEMPLATE_PATH"
fi

# ── C3: empty stdin → exit 0, no new row ──────────────────────────────────────

CASE=3
BEFORE=$(total_rows)
EXIT_CODE=0
echo -n "" | python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
AFTER=$(total_rows)
if [[ "$EXIT_CODE" -eq 0 && "$AFTER" -eq "$BEFORE" ]]; then
    pass $CASE "Empty stdin → exit 0, no new row (before=$BEFORE after=$AFTER)"
else
    fail $CASE "Empty stdin: exit=$EXIT_CODE, before=$BEFORE after=$AFTER (expected exit=0, no row change)"
fi

# ── C4: malformed JSON → exit 0, no new row ───────────────────────────────────

CASE=4
BEFORE=$(total_rows)
EXIT_CODE=0
echo 'not valid json{' | python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
AFTER=$(total_rows)
if [[ "$EXIT_CODE" -eq 0 && "$AFTER" -eq "$BEFORE" ]]; then
    pass $CASE "Malformed JSON → exit 0, no new row"
else
    fail $CASE "Malformed JSON: exit=$EXIT_CODE, before=$BEFORE after=$AFTER"
fi

# ── C5: valid Agent/Task event → 1 row with correct values ───────────────────

CASE=5
SID="test-mmc-001"
JSON5=$(cat <<'EOF'
{"tool_name":"Task","tool_input":{"description":"Worker: build foo","model":"sonnet","subagent_type":"general-purpose"},"tool_response":{"usage":{"duration_ms":12345,"num_turns":3,"input_tokens":1000,"output_tokens":500}},"session_id":"test-mmc-001"}
EOF
)
BEFORE=$(total_rows)
EXIT_CODE=0
echo "$JSON5" | python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
AFTER=$(total_rows)
NEW_ROWS=$(count_rows "$SID")

if [[ "$EXIT_CODE" -ne 0 ]]; then
    fail $CASE "Task event: script exited $EXIT_CODE (expected 0)"
elif [[ "$NEW_ROWS" -ne 1 ]]; then
    fail $CASE "Task event: expected 1 new row with session_id=$SID, got $NEW_ROWS (before=$BEFORE after=$AFTER)"
else
    # Verify field values
    PROVIDER=$(get_col "$SID" "provider")
    DUR=$(get_col "$SID" "duration_ms")
    TURNS=$(get_col "$SID" "num_turns")
    PTCOL=$(per_turn_col)
    PTMS=$(get_col "$SID" "$PTCOL")
    CATEGORY=$(get_col "$SID" "task_category")

    ERRS=()
    [[ "$PROVIDER" != "anthropic" ]] && ERRS+=("provider='$PROVIDER' (expected anthropic)")
    [[ "$DUR" != "12345" ]] && ERRS+=("duration_ms='$DUR' (expected 12345)")
    [[ "$TURNS" != "3" ]] && ERRS+=("num_turns='$TURNS' (expected 3)")
    # per_turn_ms = 12345/3 = 4115
    [[ "$PTMS" != "4115" ]] && ERRS+=("${PTCOL}='$PTMS' (expected 4115)")
    # "Worker" in description → task_category = coding
    [[ "$CATEGORY" != "coding" ]] && ERRS+=("task_category='$CATEGORY' (expected coding — 'Worker' in description)")

    if [[ ${#ERRS[@]} -eq 0 ]]; then
        pass $CASE "Task event: 1 row, provider=anthropic, duration_ms=12345, num_turns=3, ${PTCOL}=4115, task_category=coding"
    else
        fail $CASE "Task event row has wrong values: ${ERRS[*]}"
    fi
fi

# ── C6: Explore Agent event → task_category='recon' ──────────────────────────

CASE=6
SID="test-mmc-002"
JSON6=$(cat <<'EOF'
{"tool_name":"Task","tool_input":{"description":"Recon repo structure","model":"haiku","subagent_type":"Explore"},"tool_response":{"usage":{"duration_ms":5000,"num_turns":1,"input_tokens":200,"output_tokens":100}},"session_id":"test-mmc-002"}
EOF
)
EXIT_CODE=0
echo "$JSON6" | python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
NEW_ROWS=$(count_rows "$SID")

if [[ "$EXIT_CODE" -ne 0 ]]; then
    fail $CASE "Explore event: script exited $EXIT_CODE (expected 0)"
elif [[ "$NEW_ROWS" -ne 1 ]]; then
    fail $CASE "Explore event: expected 1 row with session_id=$SID, got $NEW_ROWS"
else
    CATEGORY=$(get_col "$SID" "task_category")
    if [[ "$CATEGORY" == "recon" ]]; then
        pass $CASE "Explore agent event → task_category='recon'"
    else
        fail $CASE "Explore agent event → task_category='$CATEGORY' (expected 'recon')"
    fi
fi

# ── C7: Bash event with codex_worker.sh → 1 row, provider=codex-cli ──────────

CASE=7
SID="test-mmc-003"
JSON7=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"~/.claude/scripts/codex_worker.sh gpt-5.5 < /tmp/prompt.txt"},"tool_response":{"output":"ok"},"session_id":"test-mmc-003"}
EOF
)
BEFORE=$(total_rows)
EXIT_CODE=0
echo "$JSON7" | python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
NEW_ROWS=$(count_rows "$SID")

if [[ "$EXIT_CODE" -ne 0 ]]; then
    fail $CASE "codex_worker.sh Bash event: script exited $EXIT_CODE (expected 0)"
elif [[ "$NEW_ROWS" -ne 1 ]]; then
    fail $CASE "codex_worker.sh Bash event: expected 1 row with session_id=$SID, got $NEW_ROWS"
else
    PROVIDER=$(get_col "$SID" "provider")
    MODEL=$(get_col "$SID" "model")
    ERRS=()
    [[ "$PROVIDER" != "codex-cli" ]] && ERRS+=("provider='$PROVIDER' (expected codex-cli)")
    [[ "$MODEL" != "gpt-5.5" ]] && ERRS+=("model='$MODEL' (expected gpt-5.5)")
    if [[ ${#ERRS[@]} -eq 0 ]]; then
        pass $CASE "codex_worker.sh Bash event → 1 row, provider=codex-cli, model=gpt-5.5"
    else
        fail $CASE "codex_worker.sh Bash event row wrong: ${ERRS[*]}"
    fi
fi

# ── C8: unrelated Bash event → no new row ─────────────────────────────────────

CASE=8
SID="test-mmc-004"
JSON8=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"ls /tmp"},"tool_response":{"output":"file1\nfile2"},"session_id":"test-mmc-004"}
EOF
)
BEFORE=$(total_rows)
EXIT_CODE=0
echo "$JSON8" | python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
AFTER=$(total_rows)
NEW_ROWS=$(count_rows "$SID")

if [[ "$EXIT_CODE" -eq 0 && "$NEW_ROWS" -eq 0 ]]; then
    pass $CASE "Unrelated Bash event (ls /tmp) → exit 0, no new row"
else
    fail $CASE "Unrelated Bash: exit=$EXIT_CODE, rows_for_sid=$NEW_ROWS (expected exit=0, 0 rows)"
fi

# ── C9: CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE=1 → no row ────────────────────────

CASE=9
SID="test-mmc-005"
JSON9=$(cat <<'EOF'
{"tool_name":"Task","tool_input":{"description":"Worker: should be skipped","model":"sonnet","subagent_type":"general-purpose"},"tool_response":{"usage":{"duration_ms":9999,"num_turns":2,"input_tokens":100,"output_tokens":50}},"session_id":"test-mmc-005"}
EOF
)
BEFORE=$(total_rows)
EXIT_CODE=0
CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE=1 echo "$JSON9" | CLAUDE_BOOSTER_SKIP_METRIC_CAPTURE=1 python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
AFTER=$(total_rows)
NEW_ROWS=$(count_rows "$SID")

if [[ "$EXIT_CODE" -eq 0 && "$NEW_ROWS" -eq 0 ]]; then
    pass $CASE "SKIP env var set: valid Agent event → exit 0, no row inserted"
else
    fail $CASE "SKIP env var: exit=$EXIT_CODE, rows_for_sid=$NEW_ROWS (expected exit=0, 0 rows)"
fi

# ── C10: missing usage field → exit 0, no row ─────────────────────────────────

CASE=10
SID="test-mmc-006"
JSON10=$(cat <<'EOF'
{"tool_name":"Task","tool_input":{"description":"Worker: no usage","model":"sonnet","subagent_type":"general-purpose"},"tool_response":{"content":"done, no usage block"},"session_id":"test-mmc-006"}
EOF
)
BEFORE=$(total_rows)
EXIT_CODE=0
echo "$JSON10" | python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
AFTER=$(total_rows)
NEW_ROWS=$(count_rows "$SID")

if [[ "$EXIT_CODE" -eq 0 && "$NEW_ROWS" -eq 0 ]]; then
    pass $CASE "Task event with missing usage field → exit 0, no row (graceful degradation)"
else
    fail $CASE "Missing usage: exit=$EXIT_CODE, rows_for_sid=$NEW_ROWS (expected exit=0, 0 rows)"
fi

# ── C11: model fallback to 'inherit' when tool_input.model missing ────────────

CASE=11
SID="test-mmc-007"
JSON11=$(cat <<'EOF'
{"tool_name":"Task","tool_input":{"description":"Verifier check schema","subagent_type":"general-purpose"},"tool_response":{"usage":{"duration_ms":8000,"num_turns":1,"input_tokens":300,"output_tokens":150}},"session_id":"test-mmc-007"}
EOF
)
EXIT_CODE=0
echo "$JSON11" | python3 "$SCRIPT_PATH" > /dev/null 2>&1 || EXIT_CODE=$?
NEW_ROWS=$(count_rows "$SID")

if [[ "$EXIT_CODE" -ne 0 ]]; then
    fail $CASE "No model in tool_input: script exited $EXIT_CODE (expected 0)"
elif [[ "$NEW_ROWS" -eq 0 ]]; then
    # If no row at all, still acceptable (no model may mean skip), but test fallback separately
    # Actually contract says fallback to "inherit" → should insert
    fail $CASE "No model in tool_input: expected 1 row with model='inherit', got 0 rows"
else
    MODEL=$(get_col "$SID" "model")
    if [[ "$MODEL" == "inherit" ]]; then
        pass $CASE "No model in tool_input → row inserted with model='inherit'"
    else
        fail $CASE "No model in tool_input → model='$MODEL' (expected 'inherit')"
    fi
fi

# ── cleanup ───────────────────────────────────────────────────────────────────

cleanup

FINAL=$(total_rows)
echo ""
echo "  CLEANUP  Deleted test rows (session_id LIKE 'test-mmc-%')"
echo "  VERIFY   model_metrics row count: before-test=$BASELINE, after-cleanup=$FINAL"
if [[ "$FINAL" -eq "$BASELINE" ]]; then
    echo "  OK       Row count restored to baseline"
else
    echo "  WARN     Row count differs: baseline=$BASELINE current=$FINAL (delta=$((FINAL - BASELINE)))"
fi

# ── summary ───────────────────────────────────────────────────────────────────

echo ""
echo "------------------------------------------------------------"
echo "  Result: $PASS/$TOTAL passed, $FAIL failed"
echo "------------------------------------------------------------"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
