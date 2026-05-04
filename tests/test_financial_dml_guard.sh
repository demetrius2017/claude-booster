#!/usr/bin/env bash
# Acceptance test for financial_dml_guard.py (PreToolUse hook)
# Tests observable behavior only — does NOT read or reference the implementation.
#
# Exit 0 = all scenarios passed
# Exit 1 = one or more scenarios failed

set -uo pipefail

HOOK_PATH="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/financial_dml_guard.py"
PASS_COUNT=0
FAIL_COUNT=0
RESULTS=()

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

TMPDIR_TEST="$(mktemp -d)"
export TMPDIR_TEST
trap 'rm -rf "$TMPDIR_TEST"' EXIT

# Create docs/dep_manifest.json inside the temp dir (hook searches for it
# relative to CWD or HOME — we'll cd into the tmpdir when running the hook).
mkdir -p "$TMPDIR_TEST/docs"

cat > "$TMPDIR_TEST/docs/dep_manifest.json" <<'EOF'
{
  "append_only_tables": ["nav_snapshots", "trade_ledger", "audit_log"],
  "data_patches_forbidden": ["positions.quantity", "positions.avg_cost", "broker_orders.status", "account_balances.amount"]
}
EOF

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# run_hook <scenario_label> <expected_exit> <json_payload> [env_override...]
# Runs the hook from TMPDIR_TEST so it can find docs/dep_manifest.json.
run_hook() {
    local label="$1"
    local expected_exit="$2"
    local payload="$3"
    shift 3
    local extra_env=("$@")

    local actual_exit
    if [[ ${#extra_env[@]} -gt 0 ]]; then
        actual_exit=$(cd "$TMPDIR_TEST" && env "${extra_env[@]}" python3 "$HOOK_PATH" <<< "$payload" >/dev/null 2>&1; echo $?)
    else
        actual_exit=$(cd "$TMPDIR_TEST" && python3 "$HOOK_PATH" <<< "$payload" >/dev/null 2>&1; echo $?)
    fi

    if [[ "$actual_exit" -eq "$expected_exit" ]]; then
        RESULTS+=("  PASS  [$label] — expected exit $expected_exit, got $actual_exit")
        (( PASS_COUNT++ )) || true
    else
        RESULTS+=("  FAIL  [$label] — expected exit $expected_exit, got $actual_exit")
        (( FAIL_COUNT++ )) || true
    fi
}

# Build a standard Bash tool-call payload (jq handles all JSON escaping)
bash_payload() {
    local cmd="$1"
    local cwd="${2:-$TMPDIR_TEST}"
    jq -n --arg cmd "$cmd" --arg cwd "$cwd" \
        '{"tool_name":"Bash","tool_input":{"command":$cmd},"cwd":$cwd}'
}

# Build a non-Bash tool-call payload (e.g. Edit)
non_bash_payload() {
    local tool="$1"
    jq -n --arg tool "$tool" \
        '{"tool_name":$tool,"tool_input":{"file_path":"/tmp/test.py","old_string":"x","new_string":"y"},"cwd":"/tmp"}'
}

# Build a payload with [dml-authorized] marker via a fake transcript file
authorized_payload() {
    local cmd="$1"
    local transcript_file="$TMPDIR_TEST/fake_transcript.jsonl"
    # Hook reads JSONL where each line is a message object with .message.role and .message.content[].text
    printf '{"message":{"role":"assistant","content":[{"type":"text","text":"[dml-authorized] Confirmed root cause in apply_fill(). Applying one-time data cleanup."}]}}\n' > "$transcript_file"
    jq -n --arg cmd "$cmd" --arg cwd "$TMPDIR_TEST" --arg tp "$transcript_file" \
        '{"tool_name":"Bash","tool_input":{"command":$cmd},"cwd":$cwd,"transcript_path":$tp}'
}

# ---------------------------------------------------------------------------
# Scenario (a): UPDATE on data_patches_forbidden table → exit 2
# ---------------------------------------------------------------------------
run_hook \
    "a. UPDATE on forbidden table (positions)" \
    2 \
    "$(bash_payload "psql -c \"UPDATE positions SET qty=0 WHERE id=1;\"")"

# ---------------------------------------------------------------------------
# Scenario (b): DELETE on append_only_tables → exit 2
# ---------------------------------------------------------------------------
run_hook \
    "b. DELETE on append-only table (trade_ledger)" \
    2 \
    "$(bash_payload "psql -c \"DELETE FROM trade_ledger WHERE ts < '2025-01-01';\"")"

# ---------------------------------------------------------------------------
# Scenario (c): TRUNCATE on append_only_tables → exit 2
# ---------------------------------------------------------------------------
run_hook \
    "c. TRUNCATE on append-only table (nav_snapshots)" \
    2 \
    "$(bash_payload "psql -c \"TRUNCATE TABLE nav_snapshots;\"")"

# ---------------------------------------------------------------------------
# Scenario (d): INSERT on append_only_tables → exit 0 (allowed)
# ---------------------------------------------------------------------------
run_hook \
    "d. INSERT on append-only table (audit_log) — allowed" \
    0 \
    "$(bash_payload "psql -c \"INSERT INTO audit_log (event) VALUES ('test');\"")"

# ---------------------------------------------------------------------------
# Scenario (e): UPDATE on non-protected table → exit 0
# ---------------------------------------------------------------------------
run_hook \
    "e. UPDATE on non-protected table (user_prefs)" \
    0 \
    "$(bash_payload "psql -c \"UPDATE user_prefs SET theme='dark' WHERE user_id=42;\"")"

# ---------------------------------------------------------------------------
# Scenario (f): Non-Bash tool call (Edit) → exit 0
# ---------------------------------------------------------------------------
run_hook \
    "f. Non-Bash tool (Edit) — ignored" \
    0 \
    "$(non_bash_payload "Edit")"

# ---------------------------------------------------------------------------
# Scenario (g): Bash command without any SQL DML → exit 0
# ---------------------------------------------------------------------------
run_hook \
    "g. Bash without SQL (ls command)" \
    0 \
    "$(bash_payload "ls -la /tmp")"

# ---------------------------------------------------------------------------
# Scenario (h): CLAUDE_BOOSTER_DML_ALLOWED=1 bypass → exit 0 even for blocked op
# ---------------------------------------------------------------------------
run_hook \
    "h. CLAUDE_BOOSTER_DML_ALLOWED=1 bypass on DELETE append-only" \
    0 \
    "$(bash_payload "psql -c \"DELETE FROM trade_ledger WHERE id=1;\"")" \
    "CLAUDE_BOOSTER_DML_ALLOWED=1"

# ---------------------------------------------------------------------------
# Scenario (i): No manifest exists → fail-open (exit 0)
# ---------------------------------------------------------------------------
NO_MANIFEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST" "$NO_MANIFEST_DIR"' EXIT

# Build payload with cwd pointing to NO_MANIFEST_DIR (no .claude/ there)
no_manifest_payload=$(bash_payload 'psql -c "DELETE FROM trade_ledger WHERE id=1;"' "$NO_MANIFEST_DIR")

no_manifest_exit=$(cd "$NO_MANIFEST_DIR" && \
    python3 "$HOOK_PATH" <<< "$no_manifest_payload" \
    >/dev/null 2>&1; echo $?)

if [[ "$no_manifest_exit" -eq 0 ]]; then
    RESULTS+=("  PASS  [i. No manifest exists — fail-open] — expected exit 0, got $no_manifest_exit")
    (( PASS_COUNT++ )) || true
else
    RESULTS+=("  FAIL  [i. No manifest exists — fail-open] — expected exit 0, got $no_manifest_exit")
    (( FAIL_COUNT++ )) || true
fi

# ---------------------------------------------------------------------------
# Scenario (j): psql -c pattern with UPDATE on forbidden table → exit 2
# ---------------------------------------------------------------------------
run_hook \
    "j. psql -c with UPDATE on broker_orders" \
    2 \
    "$(bash_payload "psql -U postgres -d mydb -c \"UPDATE broker_orders SET status='cancelled';\"")"

# ---------------------------------------------------------------------------
# Scenario (k): sqlite3 pattern with DELETE on append-only table → exit 2
# ---------------------------------------------------------------------------
run_hook \
    "k. sqlite3 with DELETE on nav_snapshots" \
    2 \
    "$(bash_payload "sqlite3 /tmp/db.sqlite \"DELETE FROM nav_snapshots WHERE date < '2025-01-01';\"")"

# ---------------------------------------------------------------------------
# Scenario (l): Heredoc/pipe pattern with UPDATE on forbidden table
# The contract does not guarantee detection; document actual behavior.
# If exit 2 → good, the hook detected it. If exit 0 → fail-open is acceptable.
# We verify the result is either 0 or 2 (no crash / unexpected code).
# ---------------------------------------------------------------------------
HEREDOC_CMD=$(cat <<'HEREDOC'
psql -U postgres <<SQL
UPDATE account_balances SET amount=0 WHERE id=1;
SQL
HEREDOC
)

heredoc_payload=$(jq -n --arg cmd "$HEREDOC_CMD" --arg cwd "$TMPDIR_TEST" \
    '{"tool_name":"Bash","tool_input":{"command":$cmd},"cwd":$cwd}')
heredoc_exit=$(cd "$TMPDIR_TEST" && python3 "$HOOK_PATH" <<< "$heredoc_payload" >/dev/null 2>&1; echo $?)

if [[ "$heredoc_exit" -eq 2 || "$heredoc_exit" -eq 0 ]]; then
    RESULTS+=("  PASS  [l. Heredoc pattern UPDATE account_balances] — exit $heredoc_exit (2=blocked, 0=fail-open; both acceptable per contract)")
    (( PASS_COUNT++ )) || true
else
    RESULTS+=("  FAIL  [l. Heredoc pattern] — unexpected exit code $heredoc_exit (expected 0 or 2)")
    (( FAIL_COUNT++ )) || true
fi

# ---------------------------------------------------------------------------
# Scenario (m): [dml-authorized] transcript bypass → exit 0
# ---------------------------------------------------------------------------
auth_exit=$(cd "$TMPDIR_TEST" && python3 "$HOOK_PATH" <<< "$(authorized_payload "psql -c \"UPDATE positions SET qty=0 WHERE id=99;\"")" >/dev/null 2>&1; echo $?)

if [[ "$auth_exit" -eq 0 ]]; then
    RESULTS+=("  PASS  [m. [dml-authorized] transcript bypass] — expected exit 0, got $auth_exit")
    (( PASS_COUNT++ )) || true
else
    RESULTS+=("  FAIL  [m. [dml-authorized] transcript bypass] — expected exit 0, got $auth_exit")
    (( FAIL_COUNT++ )) || true
fi

# ---------------------------------------------------------------------------
# Scenario (n): UPDATE on account_balances (data_patches_forbidden) → exit 2
# (Second forbidden-table variant to ensure list is read, not hardcoded)
# ---------------------------------------------------------------------------
run_hook \
    "n. UPDATE on account_balances (forbidden list coverage)" \
    2 \
    "$(bash_payload "psql -c \"UPDATE account_balances SET amount=100 WHERE user_id=5;\"")"

# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------
echo ""
echo "=============================="
echo " financial_dml_guard.py — Acceptance Test Results"
echo "=============================="
for r in "${RESULTS[@]}"; do
    echo "$r"
done
echo "------------------------------"
echo " Total: $((PASS_COUNT + FAIL_COUNT))  PASS: $PASS_COUNT  FAIL: $FAIL_COUNT"
echo "=============================="
echo ""

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "OVERALL: FAIL"
    exit 1
else
    echo "OVERALL: PASS"
    exit 0
fi
