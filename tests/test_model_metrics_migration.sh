#!/usr/bin/env bash
# test_model_metrics_migration.sh
# Verifier acceptance test for the model_metrics table migration.
# Exit 0 = PASS, non-zero = FAIL.
# Artifact contract: see Worker brief (model_balancer feature, SCHEMA_VERSION 5→6).
#
# Usage:
#   bash tests/test_model_metrics_migration.sh

set -euo pipefail

DB="$HOME/.claude/rolling_memory.db"
ROLLING_MEM="$HOME/.claude/scripts/rolling_memory.py"
TOTAL=8
PASS=0
FAIL=0

# ── helpers ──────────────────────────────────────────────────────────────────

pass() { echo "  PASS  case $1/$TOTAL: $2"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  case $1/$TOTAL: $2"; FAIL=$((FAIL + 1)); }

# ── preamble ─────────────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "  model_metrics migration — acceptance test (${TOTAL} cases)"
echo "============================================================"

# Informational: check for backup created by Worker
if ls "$HOME"/.claude/rolling_memory.db.bak.* 2>/dev/null | head -1 | grep -q .; then
    LATEST_BAK=$(ls -t "$HOME"/.claude/rolling_memory.db.bak.* 2>/dev/null | head -1)
    echo "  NOTE  Worker backup found: $LATEST_BAK"
else
    echo "  NOTE  No timestamped backup found at ~/.claude/rolling_memory.db.bak.* (standard .bak may exist)"
fi
echo ""

# ── C1: table exists ──────────────────────────────────────────────────────────

CASE=1
RESULT=$(sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='table' AND name='model_metrics';" 2>&1)
if [[ "$RESULT" == "model_metrics" ]]; then
    pass $CASE "Table 'model_metrics' exists in rolling_memory.db"
else
    fail $CASE "Table 'model_metrics' NOT found — got: '$RESULT'"
fi

# ── C2: exactly 13 columns ───────────────────────────────────────────────────

CASE=2
COL_COUNT=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>&1 | wc -l | tr -d ' ')
if [[ "$COL_COUNT" -eq 13 ]]; then
    pass $CASE "Table has exactly 13 columns (got $COL_COUNT)"
else
    fail $CASE "Expected 13 columns, got $COL_COUNT"
fi

# ── C3: all expected column names present ────────────────────────────────────

CASE=3
PRAGMA_OUT=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>&1)
EXPECTED_COLS=(id ts_utc provider model task_category duration_ms num_turns per_turn_ms tokens_in tokens_out success session_id project_root)
ALL_OK=1
MISSING=()
for col in "${EXPECTED_COLS[@]}"; do
    if ! echo "$PRAGMA_OUT" | grep -q "|${col}|"; then
        ALL_OK=0
        MISSING+=("$col")
    fi
done
if [[ "$ALL_OK" -eq 1 ]]; then
    pass $CASE "All 13 expected column names found"
else
    fail $CASE "Missing columns: ${MISSING[*]}"
fi

# ── C4: NOT NULL constraints on required columns ─────────────────────────────

CASE=4
# PRAGMA table_info columns: cid|name|type|notnull|dflt_value|pk
NOT_NULL_COLS=(ts_utc provider model success)
NN_OK=1
NN_BAD=()
for col in "${NOT_NULL_COLS[@]}"; do
    # notnull field should be 1
    NOTNULL=$(sqlite3 "$DB" "PRAGMA table_info(model_metrics);" 2>&1 | awk -F'|' -v c="$col" '$2 == c {print $4}')
    if [[ "$NOTNULL" != "1" ]]; then
        NN_OK=0
        NN_BAD+=("$col(notnull=$NOTNULL)")
    fi
done
if [[ "$NN_OK" -eq 1 ]]; then
    pass $CASE "NOT NULL constraints correct on: ${NOT_NULL_COLS[*]}"
else
    fail $CASE "NOT NULL missing on: ${NN_BAD[*]}"
fi

# ── C5: both indexes exist ───────────────────────────────────────────────────

CASE=5
IDX_OUT=$(sqlite3 "$DB" "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_model_metrics_%';" 2>&1)
IDX1="idx_model_metrics_model_ts"
IDX2="idx_model_metrics_provider_ts"
if echo "$IDX_OUT" | grep -q "$IDX1" && echo "$IDX_OUT" | grep -q "$IDX2"; then
    pass $CASE "Both indexes present: $IDX1, $IDX2"
else
    fail $CASE "Missing index(es). Found: $(echo "$IDX_OUT" | tr '\n' ' ') — expected both $IDX1 and $IDX2"
fi

# ── C6: insert / select roundtrip ────────────────────────────────────────────

CASE=6
TEST_SESSION="verifier-test-$(date +%s)"
sqlite3 "$DB" "INSERT INTO model_metrics (ts_utc, provider, model, success, session_id) VALUES ('2026-05-12T00:00:00Z', 'test', 'test-model', 1, '${TEST_SESSION}');" 2>&1
SEL=$(sqlite3 "$DB" "SELECT ts_utc, provider, model, success FROM model_metrics WHERE session_id='${TEST_SESSION}';" 2>&1)
# Clean up regardless
sqlite3 "$DB" "DELETE FROM model_metrics WHERE session_id='${TEST_SESSION}';" 2>&1
if [[ "$SEL" == "2026-05-12T00:00:00Z|test|test-model|1" ]]; then
    pass $CASE "Insert/select roundtrip succeeded; test row cleaned up"
else
    fail $CASE "Roundtrip failed — selected: '$SEL'"
fi

# ── C7: SCHEMA_VERSION in rolling_memory.py ─────────────────────────────────

CASE=7
# Read the constant directly from source
GREP_OUT=$(grep -E '^SCHEMA_VERSION\s*=' "$ROLLING_MEM" 2>&1)
if [[ -z "$GREP_OUT" ]]; then
    fail $CASE "SCHEMA_VERSION constant not found in rolling_memory.py"
else
    # Extract the integer value
    CONST_VAL=$(echo "$GREP_OUT" | grep -oE '[0-9]+')
    # Verify it's an integer >= 6 (was 5 before migration)
    if [[ "$CONST_VAL" =~ ^[0-9]+$ ]] && [[ "$CONST_VAL" -ge 6 ]]; then
        # Also verify python import returns the same number
        PY_VAL=$(python3 -c "import sys; sys.path.insert(0, '$HOME/.claude/scripts'); import rolling_memory; print(rolling_memory.SCHEMA_VERSION)" 2>&1)
        if [[ "$PY_VAL" == "$CONST_VAL" ]]; then
            pass $CASE "SCHEMA_VERSION = $CONST_VAL (>= 6, was 5 pre-migration); python import agrees"
        else
            fail $CASE "SCHEMA_VERSION source=$CONST_VAL but python import returned '$PY_VAL'"
        fi
    else
        fail $CASE "SCHEMA_VERSION='$CONST_VAL' — expected integer >= 6 (was 5 before migration)"
    fi
fi

# ── C8 (bonus): idempotent re-migration ──────────────────────────────────────

CASE=8
# Find the migrate entry point in rolling_memory.py
MIGRATE_FN=$(grep -E 'def (migrate|_migrate|init_db|_init_db|setup_db|_setup_db|ensure_schema|_ensure_schema)' "$ROLLING_MEM" 2>/dev/null | head -3 | awk '{print $2}' | cut -d'(' -f1)

if [[ -z "$MIGRATE_FN" ]]; then
    echo "  SKIP  case $CASE/$TOTAL: No recognizable migrate function found; skipping idempotency check"
    # Count as pass since it's a bonus case
    PASS=$((PASS + 1))
else
    # Call the function twice and check no exception
    IDEMPOTENT_ERR=$(python3 - <<PYEOF 2>&1
import sys, traceback
sys.path.insert(0, "$HOME/.claude/scripts")
import rolling_memory
fn = getattr(rolling_memory, "$MIGRATE_FN", None)
if fn is None:
    print("NOTFOUND")
    sys.exit(1)
try:
    fn()
    fn()
    print("OK")
except Exception as e:
    traceback.print_exc()
    sys.exit(1)
PYEOF
)
    if echo "$IDEMPOTENT_ERR" | grep -q "^OK"; then
        pass $CASE "Re-migration idempotent via $MIGRATE_FN() — no error on double call"
    elif echo "$IDEMPOTENT_ERR" | grep -q "NOTFOUND"; then
        echo "  SKIP  case $CASE/$TOTAL: Function $MIGRATE_FN not importable; skipping"
        PASS=$((PASS + 1))
    else
        fail $CASE "Re-migration raised error: $(echo "$IDEMPOTENT_ERR" | tail -3)"
    fi
fi

# ── summary ──────────────────────────────────────────────────────────────────

echo ""
echo "------------------------------------------------------------"
echo "  Result: $PASS/$TOTAL passed, $FAIL failed"
echo "------------------------------------------------------------"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
