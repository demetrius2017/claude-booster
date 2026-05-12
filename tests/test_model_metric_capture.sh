#!/usr/bin/env bash
# test_model_metric_capture.sh
# Verifier acceptance test for model_metric_capture.py PostToolUse hook.
#
# Derived from Artifact Contract — tests expected post-fix behaviour.
# Schema under test: model_metrics(id, timestamp, tool_name, model, provider,
#   duration_ms, input_tokens, output_tokens, category)
#
# Exit 0 = all PASS, Exit 1 = one or more FAIL
#
# Usage:
#   bash /Users/dmitrijnazarov/Projects/Claude_Booster/tests/test_model_metric_capture.sh

set -uo pipefail

# ── paths (all $HOME-expanded, no ~ literals) ──────────────────────────────────
SCRIPT="$HOME/.claude/scripts/model_metric_capture.py"
TEMPLATE="$HOME/../Projects/Claude_Booster/templates/scripts/model_metric_capture.py"
# Resolve absolute template path without relying on cwd
TEMPLATE="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)/templates/scripts/model_metric_capture.py"
DB="$HOME/.claude/rolling_memory.db"
LOG_DIR="$HOME/.claude/logs"
TODAY_UTC=$(python3 -c "from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime('%Y%m%d'))")
MARKER="$LOG_DIR/.metric_capture_sample_$TODAY_UTC"

TOTAL=17
PASS=0
FAIL=0
FAILURES=()

# ── helpers ────────────────────────────────────────────────────────────────────

pass() {
    echo "  PASS  $1: $2"
    PASS=$((PASS + 1))
}

fail() {
    echo "  FAIL  $1: $2"
    FAIL=$((FAIL + 1))
    FAILURES+=("$1")
}

# Count rows where a marker column matches a test-specific value.
# We use model values that are unique per test to avoid cross-contamination.
count_by_model() {
    local model_val="$1"
    sqlite3 "$DB" "SELECT COUNT(*) FROM model_metrics WHERE model='${model_val}';" 2>/dev/null || echo "0"
}

get_col_by_model() {
    local model_val="$1"
    local col="$2"
    sqlite3 "$DB" "SELECT ${col} FROM model_metrics WHERE model='${model_val}' ORDER BY rowid DESC LIMIT 1;" 2>/dev/null || echo ""
}

total_rows() {
    sqlite3 "$DB" "SELECT COUNT(*) FROM model_metrics;" 2>/dev/null || echo "0"
}

# Detect which column name was used for the contract's "timestamp" field.
# The Worker may have added it as "timestamp" or left it as "ts_utc".
ts_col() {
    local cols
    cols=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>/dev/null | awk -F'|' '{print $2}')
    if echo "$cols" | grep -qx "timestamp"; then
        echo "timestamp"
    else
        echo "ts_utc"
    fi
}

# Detect input_tokens column name (contract: input_tokens; old: tokens_in).
in_tok_col() {
    local cols
    cols=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>/dev/null | awk -F'|' '{print $2}')
    if echo "$cols" | grep -qx "input_tokens"; then
        echo "input_tokens"
    else
        echo "tokens_in"
    fi
}

# Detect output_tokens column name (contract: output_tokens; old: tokens_out).
out_tok_col() {
    local cols
    cols=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>/dev/null | awk -F'|' '{print $2}')
    if echo "$cols" | grep -qx "output_tokens"; then
        echo "output_tokens"
    else
        echo "tokens_out"
    fi
}

# Detect category column name (contract: category; old: task_category).
cat_col() {
    local cols
    cols=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>/dev/null | awk -F'|' '{print $2}')
    if echo "$cols" | grep -qx "category"; then
        echo "category"
    else
        echo "task_category"
    fi
}

# Detect tool_name column (contract: tool_name; may be absent in old schema).
has_tool_name_col() {
    local cols
    cols=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>/dev/null | awk -F'|' '{print $2}')
    echo "$cols" | grep -qx "tool_name" && echo "yes" || echo "no"
}

# Clean up: delete all rows with model values we injected during tests.
# Uses unique model sentinel values per test to avoid hitting production rows.
cleanup() {
    sqlite3 "$DB" "DELETE FROM model_metrics WHERE model LIKE 'test-mmc-%';" 2>/dev/null || true
    # Also clean codex test models used in C1-C6
    sqlite3 "$DB" "DELETE FROM model_metrics WHERE model IN ('gpt-5.5','gpt-5.4-mini','gpt-5.3-codex','gpt-5.3-codex-spark','gpt-5.2') AND provider='codex-cli';" 2>/dev/null || true
}

# Run script with JSON piped to stdin, return exit code.
run_hook() {
    local json="$1"
    local exit_code=0
    echo "$json" | python3 "$SCRIPT" > /dev/null 2>&1 || exit_code=$?
    echo "$exit_code"
}

# ── preamble ───────────────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "  model_metric_capture — acceptance test ($TOTAL cases)"
echo "  schema cols: ts=$(ts_col) in=$(in_tok_col) out=$(out_tok_col) cat=$(cat_col)"
echo "  tool_name col present: $(has_tool_name_col)"
echo "============================================================"
echo ""

BASELINE=$(total_rows)
echo "  INFO  model_metrics baseline: $BASELINE rows"
echo "  INFO  today UTC: $TODAY_UTC  marker: $MARKER"
echo ""

# ── A1: tool_response.usage path ──────────────────────────────────────────────

ID="A1"
MODEL_A1="test-mmc-a1"
JSON=$(cat <<EOF
{"tool_name":"Task","tool_response":{"usage":{"duration_ms":1200,"input_tokens":500,"output_tokens":300}},"tool_input":{"model":"$MODEL_A1"}}
EOF
)
BEFORE=$(total_rows)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "$MODEL_A1")
if [[ "$EXIT" -eq 0 && "$AFTER" -eq 1 ]]; then
    DUR=$(get_col_by_model "$MODEL_A1" "duration_ms")
    IN_COL=$(in_tok_col); IN=$(get_col_by_model "$MODEL_A1" "$IN_COL")
    OUT_COL=$(out_tok_col); OUT=$(get_col_by_model "$MODEL_A1" "$OUT_COL")
    if [[ "$DUR" == "1200" && "$IN" == "500" && "$OUT" == "300" ]]; then
        pass "$ID" "tool_response.usage → row inserted, duration_ms=1200 ${IN_COL}=500 ${OUT_COL}=300"
    else
        fail "$ID" "Row inserted but wrong values: duration_ms='$DUR'(expected 1200) ${IN_COL}='$IN'(expected 500) ${OUT_COL}='$OUT'(expected 300)"
    fi
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected exit=0, 1 row)"
fi

# ── A2: toolUseResult.usage path ──────────────────────────────────────────────

ID="A2"
MODEL_A2="test-mmc-a2"
JSON=$(cat <<EOF
{"tool_name":"Agent","toolUseResult":{"usage":{"duration_ms":800,"input_tokens":400,"output_tokens":200}},"tool_input":{"model":"$MODEL_A2"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "$MODEL_A2")
if [[ "$EXIT" -eq 0 && "$AFTER" -eq 1 ]]; then
    DUR=$(get_col_by_model "$MODEL_A2" "duration_ms")
    IN_COL=$(in_tok_col); IN=$(get_col_by_model "$MODEL_A2" "$IN_COL")
    if [[ "$DUR" == "800" && "$IN" == "400" ]]; then
        pass "$ID" "toolUseResult.usage → row inserted, duration_ms=800 ${IN_COL}=400"
    else
        fail "$ID" "Row inserted but wrong values: duration_ms='$DUR'(exp 800) ${IN_COL}='$IN'(exp 400)"
    fi
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected exit=0, 1 row)"
fi

# ── A3: tool_response.toolUseResult.usage nested path ─────────────────────────

ID="A3"
MODEL_A3="test-mmc-a3"
JSON=$(cat <<EOF
{"tool_name":"Task","tool_response":{"toolUseResult":{"usage":{"duration_ms":950,"input_tokens":600,"output_tokens":250}}},"tool_input":{"model":"$MODEL_A3"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "$MODEL_A3")
if [[ "$EXIT" -eq 0 && "$AFTER" -eq 1 ]]; then
    DUR=$(get_col_by_model "$MODEL_A3" "duration_ms")
    IN_COL=$(in_tok_col); IN=$(get_col_by_model "$MODEL_A3" "$IN_COL")
    if [[ "$DUR" == "950" && "$IN" == "600" ]]; then
        pass "$ID" "tool_response.toolUseResult.usage → row inserted, duration_ms=950"
    else
        fail "$ID" "Wrong values: duration_ms='$DUR'(exp 950) ${IN_COL}='$IN'(exp 600)"
    fi
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected exit=0, 1 row)"
fi

# ── A4: top-level usage path ──────────────────────────────────────────────────

ID="A4"
MODEL_A4="test-mmc-a4"
JSON=$(cat <<EOF
{"tool_name":"Agent","usage":{"duration_ms":1100,"input_tokens":700,"output_tokens":350},"tool_input":{"model":"$MODEL_A4"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "$MODEL_A4")
if [[ "$EXIT" -eq 0 && "$AFTER" -eq 1 ]]; then
    DUR=$(get_col_by_model "$MODEL_A4" "duration_ms")
    IN_COL=$(in_tok_col); IN=$(get_col_by_model "$MODEL_A4" "$IN_COL")
    if [[ "$DUR" == "1100" && "$IN" == "700" ]]; then
        pass "$ID" "top-level usage → row inserted, duration_ms=1100"
    else
        fail "$ID" "Wrong values: duration_ms='$DUR'(exp 1100) ${IN_COL}='$IN'(exp 700)"
    fi
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected exit=0, 1 row)"
fi

# ── B1: no-usage Task → no row inserted ───────────────────────────────────────

ID="B1"
BEFORE=$(total_rows)
JSON=$(cat <<'EOF'
{"tool_name":"Task","tool_response":{"content":"done"},"tool_input":{"model":"sonnet","description":"some task"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(total_rows)
if [[ "$EXIT" -eq 0 && "$AFTER" -eq "$BEFORE" ]]; then
    pass "$ID" "Task with no usage at any path → exit 0, no row (before=$BEFORE after=$AFTER)"
else
    fail "$ID" "exit=$EXIT, before=$BEFORE after=$AFTER (expected exit=0, count unchanged)"
fi

# ── B2: daily sample marker created on first no-usage event ───────────────────

ID="B2"
# Remove marker if it exists so we can test first-event creation
MARKER_WAS_PRESENT=0
if [[ -f "$MARKER" ]]; then
    MARKER_WAS_PRESENT=1
    mv "$MARKER" "${MARKER}.bak.$$"
fi
mkdir -p "$LOG_DIR"

# Run a no-usage event (same as B1 — the marker is created on that code path)
JSON=$(cat <<'EOF'
{"tool_name":"Task","tool_response":{"content":"done"},"tool_input":{"model":"sonnet-b2","description":"b2 no-usage"}}
EOF
)
run_hook "$JSON" > /dev/null

if [[ -f "$MARKER" ]]; then
    pass "$ID" "Daily sample marker created at $MARKER after first no-usage event"
else
    fail "$ID" "Marker file $MARKER NOT found after no-usage event (expected creation on first miss per day)"
fi

# Restore marker state
if [[ "$MARKER_WAS_PRESENT" -eq 1 ]]; then
    mv "${MARKER}.bak.$$" "$MARKER"
fi

# ── C1: codex_worker.sh with --model gpt-5.5 ──────────────────────────────────

ID="C1"
BEFORE=$(total_rows)
JSON=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"codex_worker.sh gpt-5.5 < /tmp/prompt.txt"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "gpt-5.5")
if [[ "$EXIT" -eq 0 && "$AFTER" -ge 1 ]]; then
    PROV=$(get_col_by_model "gpt-5.5" "provider")
    if [[ "$PROV" == "codex-cli" ]]; then
        pass "$ID" "codex_worker.sh gpt-5.5 (positional) → row inserted, model=gpt-5.5 provider=codex-cli"
    else
        fail "$ID" "Row inserted but provider='$PROV' (expected codex-cli)"
    fi
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected exit=0, ≥1 row)"
fi

# ── C2: codex exec -m gpt-5.5 ─────────────────────────────────────────────────

ID="C2"
# Delete C1's gpt-5.5 row first so count is unambiguous
sqlite3 "$DB" "DELETE FROM model_metrics WHERE model='gpt-5.5' AND provider='codex-cli';" 2>/dev/null || true
JSON=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"codex exec -m gpt-5.5 --prompt /tmp/p.txt"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "gpt-5.5")
if [[ "$EXIT" -eq 0 && "$AFTER" -ge 1 ]]; then
    pass "$ID" "codex exec -m gpt-5.5 → row inserted, model=gpt-5.5"
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected exit=0, ≥1 row for gpt-5.5)"
fi

# ── C3: --model gpt-5.4-mini ──────────────────────────────────────────────────

ID="C3"
JSON=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"codex_worker.sh gpt-5.4-mini"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "gpt-5.4-mini")
if [[ "$EXIT" -eq 0 && "$AFTER" -ge 1 ]]; then
    pass "$ID" "gpt-5.4-mini (positional) → row inserted"
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected ≥1 row for gpt-5.4-mini)"
fi

# ── C4: --model gpt-5.3-codex ─────────────────────────────────────────────────

ID="C4"
JSON=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"codex_worker.sh gpt-5.3-codex"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "gpt-5.3-codex")
if [[ "$EXIT" -eq 0 && "$AFTER" -ge 1 ]]; then
    pass "$ID" "gpt-5.3-codex (positional) → row inserted"
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected ≥1 row for gpt-5.3-codex)"
fi

# ── C5: --model gpt-5.3-codex-spark ──────────────────────────────────────────

ID="C5"
JSON=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"codex_worker.sh gpt-5.3-codex-spark"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "gpt-5.3-codex-spark")
if [[ "$EXIT" -eq 0 && "$AFTER" -ge 1 ]]; then
    pass "$ID" "gpt-5.3-codex-spark (positional) → row inserted"
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected ≥1 row for gpt-5.3-codex-spark)"
fi

# ── C6: --model gpt-5.2 ───────────────────────────────────────────────────────

ID="C6"
JSON=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"codex_worker.sh gpt-5.2"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(count_by_model "gpt-5.2")
if [[ "$EXIT" -eq 0 && "$AFTER" -ge 1 ]]; then
    pass "$ID" "gpt-5.2 (positional) → row inserted"
else
    fail "$ID" "exit=$EXIT rows=$AFTER (expected ≥1 row for gpt-5.2)"
fi

# ── C7: --model gpt-4o (NOT in allowlist) → no row ────────────────────────────

ID="C7"
BEFORE=$(total_rows)
JSON=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"codex_worker.sh gpt-4o"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(total_rows)
if [[ "$EXIT" -eq 0 && "$AFTER" -eq "$BEFORE" ]]; then
    pass "$ID" "gpt-4o (positional, not in allowlist) → no row inserted"
else
    fail "$ID" "exit=$EXIT before=$BEFORE after=$AFTER (expected exit=0, count unchanged)"
fi

# ── C8: heredoc fragment — $(cat must not be captured as model ────────────────

ID="C8"
BEFORE=$(total_rows)
# Embed the heredoc fragment; the $(cat token must not produce a row
JSON=$(cat <<'ENDJSON'
{"tool_name":"Bash","tool_input":{"command":"bash -c \"$(cat <<'EOF'\n--model gpt-5.5\nEOF\n)\""}}
ENDJSON
)
EXIT=$(run_hook "$JSON")
AFTER=$(total_rows)
if [[ "$EXIT" -eq 0 && "$AFTER" -eq "$BEFORE" ]]; then
    pass "$ID" "heredoc fragment with \$(cat — no row (token boundary blocks false capture)"
else
    fail "$ID" "exit=$EXIT before=$BEFORE after=$AFTER (expected exit=0, count unchanged for heredoc)"
fi

# ── C9: bare grep of codex_worker.sh → no row ─────────────────────────────────

ID="C9"
BEFORE=$(total_rows)
JSON=$(cat <<'EOF'
{"tool_name":"Bash","tool_input":{"command":"grep codex_worker.sh somefile"}}
EOF
)
EXIT=$(run_hook "$JSON")
AFTER=$(total_rows)
if [[ "$EXIT" -eq 0 && "$AFTER" -eq "$BEFORE" ]]; then
    pass "$ID" "bare grep codex_worker.sh → no row (not a real invocation)"
else
    fail "$ID" "exit=$EXIT before=$BEFORE after=$AFTER (expected exit=0, count unchanged)"
fi

# ── D1: both artifact files byte-identical ────────────────────────────────────

ID="D1"
if diff -q "$SCRIPT" "$TEMPLATE" > /dev/null 2>&1; then
    pass "$ID" "installed script and template are byte-identical"
else
    fail "$ID" "files differ: diff $SCRIPT $TEMPLATE"
fi

# ── D2: installed script parses as valid Python ───────────────────────────────

ID="D2"
PARSE_ERR=$(python3 -c "import ast; ast.parse(open('$SCRIPT').read())" 2>&1)
if [[ -z "$PARSE_ERR" ]]; then
    pass "$ID" "installed script is syntactically valid Python"
else
    fail "$ID" "ast.parse failed: $PARSE_ERR"
fi

# ── cleanup ────────────────────────────────────────────────────────────────────

cleanup

FINAL=$(total_rows)
echo ""
echo "  CLEANUP  test rows removed; model_metrics: baseline=$BASELINE current=$FINAL"
if [[ "$FINAL" -ne "$BASELINE" ]]; then
    echo "  WARN     row count changed by $((FINAL - BASELINE)) (may be unrelated production activity)"
fi

# ── summary ────────────────────────────────────────────────────────────────────

echo ""
echo "------------------------------------------------------------"
echo "  Passed $PASS/$TOTAL tests"
if [[ "${#FAILURES[@]}" -gt 0 ]]; then
    echo "  Failed cases: ${FAILURES[*]}"
fi
echo "------------------------------------------------------------"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
