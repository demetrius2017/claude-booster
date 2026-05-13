#!/usr/bin/env bash
# test_codex_regex_fix.sh
#
# Acceptance test for the Codex command regex fix in:
#   model_metric_capture.py  (_match_codex_command)
#   delegate_gate.py         (_bash_is_codex_worker)
#
# Artifact Contract:
#   Both functions must correctly match real-world Codex invocations that use
#   full paths (e.g. ~/.claude/scripts/codex_worker.sh), time wrappers,
#   pipe-based input, and stdin redirection — while rejecting false positives
#   like grep/cat/vim/head operating on the script file itself.
#
# Test structure:
#   Section 1: _match_codex_command() — positive cases (must return correct model)
#   Section 2: _match_codex_command() — negative cases (must return None)
#   Section 3: _bash_is_codex_worker() — positive cases (must return True)
#   Section 4: _bash_is_codex_worker() — negative cases (must return False)
#   Section 5: Template/installed byte-identity check
#   Section 6: No garbage rows in model_metrics (provider=codex-cli, bad model)
#
# Exit 0 = all pass, non-zero = at least one failure with diagnostic output.
#
# Usage:
#   bash /Users/dmitrijnazarov/Projects/Claude_Booster/tests/test_codex_regex_fix.sh

set -uo pipefail

TMPFILES=()
trap 'rm -f "${TMPFILES[@]}" 2>/dev/null' EXIT

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MMC_TEMPLATE="$REPO_ROOT/templates/scripts/model_metric_capture.py"
DG_TEMPLATE="$REPO_ROOT/templates/scripts/delegate_gate.py"
MMC_INSTALLED="$HOME/.claude/scripts/model_metric_capture.py"
DG_INSTALLED="$HOME/.claude/scripts/delegate_gate.py"
DB="$HOME/.claude/rolling_memory.db"

PASS=0
FAIL=0
FAILURES=()

pass() {
    echo "  PASS  [$1]  $2"
    PASS=$((PASS + 1))
}

fail() {
    echo "  FAIL  [$1]  $2"
    FAIL=$((FAIL + 1))
    FAILURES+=("$1")
}

echo ""
echo "================================================================"
echo "  test_codex_regex_fix — Codex command regex acceptance test"
echo "================================================================"
echo ""

# ---------------------------------------------------------------------------
# Guard: required files must exist
# ---------------------------------------------------------------------------
for f in "$MMC_TEMPLATE" "$DG_TEMPLATE"; do
    if [[ ! -f "$f" ]]; then
        echo "FATAL: required file not found: $f"
        exit 2
    fi
done

# ---------------------------------------------------------------------------
# SECTION 1: _match_codex_command() — positive cases
#
# Each command must return the exact model string shown.
# The test calls model_metric_capture._match_codex_command() directly.
# ---------------------------------------------------------------------------
echo "=== SECTION 1: _match_codex_command() — positive cases ==="
echo ""

check_match_codex() {
    local id="$1"
    local cmd="$2"
    local expected_model="$3"

    local tmp
    tmp=$(mktemp /tmp/codex_cmd.XXXXXX)
    TMPFILES+=("$tmp")
    printf '%s' "$cmd" > "$tmp"

    local got
    got=$(python3 - "$tmp" <<'PYEOF' 2>/dev/null
import sys, importlib.util
spec = importlib.util.spec_from_file_location(
    "model_metric_capture",
    __import__("os").path.expanduser(
        "~/.claude/scripts/model_metric_capture.py"
    )
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with open(sys.argv[1]) as fh:
    cmd = fh.read()
result = mod._match_codex_command(cmd)
print(result if result is not None else "None")
PYEOF
)

    if [[ "$got" == "$expected_model" ]]; then
        pass "$id" "_match_codex_command returned '$got' for: ${cmd:0:70}"
    else
        fail "$id" "_match_codex_command: expected '$expected_model', got '$got' | cmd: ${cmd:0:70}"
    fi
}

# Full-path with time wrapper and pipe
check_match_codex "MC-P1" \
    "time (printf '...\\n' | ~/.claude/scripts/codex_worker.sh gpt-5.3-codex-spark) 2>&1" \
    "gpt-5.3-codex-spark"

# Full-path with time wrapper and stdin redirect
check_match_codex "MC-P2" \
    "time ~/.claude/scripts/codex_worker.sh gpt-5.3-codex-spark < /tmp/file 2>&1" \
    "gpt-5.3-codex-spark"

# Full-path, time wrapper, stdin redirect, pipe to tail
check_match_codex "MC-P3" \
    "time ~/.claude/scripts/codex_worker.sh gpt-5.3-codex < /tmp/file 2>&1 | tail -80" \
    "gpt-5.3-codex"

# Bare script name, no path
check_match_codex "MC-P4" \
    "codex_worker.sh gpt-5.3-codex" \
    "gpt-5.3-codex"

# Bare script name piped from echo
check_match_codex "MC-P5" \
    "echo task | codex_worker.sh gpt-5.3-codex" \
    "gpt-5.3-codex"

# Full-path sandbox_worker with pipe and model gpt-5.5
check_match_codex "MC-P6" \
    "printf 'task' | ~/.claude/scripts/codex_sandbox_worker.sh gpt-5.5" \
    "gpt-5.5"

echo ""

# ---------------------------------------------------------------------------
# SECTION 2: _match_codex_command() — negative cases (false positives)
#
# Each command must return None — these are NOT codex invocations.
# ---------------------------------------------------------------------------
echo "=== SECTION 2: _match_codex_command() — negative cases ==="
echo ""

check_match_none() {
    local id="$1"
    local cmd="$2"

    local tmp
    tmp=$(mktemp /tmp/codex_cmd.XXXXXX)
    TMPFILES+=("$tmp")
    printf '%s' "$cmd" > "$tmp"

    local got
    got=$(python3 - "$tmp" <<'PYEOF' 2>/dev/null
import sys, importlib.util
spec = importlib.util.spec_from_file_location(
    "model_metric_capture",
    __import__("os").path.expanduser(
        "~/.claude/scripts/model_metric_capture.py"
    )
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with open(sys.argv[1]) as fh:
    cmd = fh.read()
result = mod._match_codex_command(cmd)
print(result if result is not None else "None")
PYEOF
)

    if [[ "$got" == "None" ]]; then
        pass "$id" "_match_codex_command returned None (correct) for: ${cmd:0:70}"
    else
        fail "$id" "_match_codex_command: expected None, got '$got' (false positive!) | cmd: ${cmd:0:70}"
    fi
}

# grep operating on the script file — false positive
check_match_none "MC-N1" "grep codex_worker.sh logs/"

# head reading the script file — false positive
check_match_none "MC-N2" "head -30 ~/.claude/scripts/codex_worker.sh 2>/dev/null"

# vim editing the script — false positive
check_match_none "MC-N3" "vim codex_worker.sh"

# cat reading the script — false positive
check_match_none "MC-N4" "cat codex_worker.sh"

echo ""

# ---------------------------------------------------------------------------
# SECTION 3: _bash_is_codex_worker() — positive cases
#
# Each command must return True. Uses delegate_gate from installed path,
# falling back to template path.
# ---------------------------------------------------------------------------
echo "=== SECTION 3: _bash_is_codex_worker() — positive cases ==="
echo ""

# Determine which delegate_gate to use for direct function import
if [[ -f "$DG_INSTALLED" ]]; then
    DG_FOR_IMPORT="$DG_INSTALLED"
else
    DG_FOR_IMPORT="$DG_TEMPLATE"
fi
# Also need _gate_common — prefer installed scripts dir, fall back to template dir
DG_SCRIPT_DIR="$(dirname "$DG_FOR_IMPORT")"

check_is_codex() {
    local id="$1"
    local cmd="$2"
    local expect="$3"  # "true" or "false"

    local tmp
    tmp=$(mktemp /tmp/codex_cmd.XXXXXX)
    TMPFILES+=("$tmp")
    printf '%s' "$cmd" > "$tmp"

    local got
    got=$(python3 - "$tmp" "$DG_FOR_IMPORT" "$DG_SCRIPT_DIR" <<'PYEOF' 2>/dev/null
import sys, importlib.util
gate_path = sys.argv[2]
script_dir = sys.argv[3]
# Add script_dir so _gate_common import works
sys.path.insert(0, script_dir)
spec = importlib.util.spec_from_file_location("delegate_gate", gate_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with open(sys.argv[1]) as fh:
    cmd = fh.read()
result = mod._bash_is_codex_worker(cmd)
print("true" if result else "false")
PYEOF
)

    if [[ "$got" == "$expect" ]]; then
        pass "$id" "_bash_is_codex_worker=$got (correct) | ${cmd:0:70}"
    else
        fail "$id" "_bash_is_codex_worker: expected $expect, got '$got' | cmd: ${cmd:0:70}"
    fi
}

# Full-path with time wrapper and pipe
check_is_codex "DG-P1" \
    "time (printf '...\\n' | ~/.claude/scripts/codex_worker.sh gpt-5.3-codex-spark) 2>&1" \
    "true"

# Full-path with time wrapper and stdin redirect
check_is_codex "DG-P2" \
    "time ~/.claude/scripts/codex_worker.sh gpt-5.3-codex-spark < /tmp/file 2>&1" \
    "true"

# Full-path, time, stdin redirect, pipe to tail
check_is_codex "DG-P3" \
    "time ~/.claude/scripts/codex_worker.sh gpt-5.3-codex < /tmp/file 2>&1 | tail -80" \
    "true"

# Bare script name
check_is_codex "DG-P4" \
    "codex_worker.sh gpt-5.3-codex" \
    "true"

# Bare script name with pipe
check_is_codex "DG-P5" \
    "echo task | codex_worker.sh gpt-5.3-codex" \
    "true"

# Full-path sandbox_worker with pipe
check_is_codex "DG-P6" \
    "printf 'task' | ~/.claude/scripts/codex_sandbox_worker.sh gpt-5.5" \
    "true"

echo ""

# ---------------------------------------------------------------------------
# SECTION 4: _bash_is_codex_worker() — negative cases (false positives)
# ---------------------------------------------------------------------------
echo "=== SECTION 4: _bash_is_codex_worker() — negative cases ==="
echo ""

# grep — false positive
check_is_codex "DG-N1" "grep codex_worker.sh logs/" "false"

# head reading script — false positive
check_is_codex "DG-N2" "head -30 ~/.claude/scripts/codex_worker.sh 2>/dev/null" "false"

# vim editing — false positive
check_is_codex "DG-N3" "vim codex_worker.sh" "false"

# cat reading — false positive
check_is_codex "DG-N4" "cat codex_worker.sh" "false"

echo ""

# ---------------------------------------------------------------------------
# SECTION 5: Template and installed copies must be byte-identical
# ---------------------------------------------------------------------------
echo "=== SECTION 5: Template/installed byte-identity ==="
echo ""

if [[ -f "$MMC_INSTALLED" ]]; then
    if diff -q "$MMC_TEMPLATE" "$MMC_INSTALLED" > /dev/null 2>&1; then
        pass "SYNC-1" "model_metric_capture.py: template and installed are byte-identical"
    else
        fail "SYNC-1" "model_metric_capture.py: files DIFFER — diff $MMC_TEMPLATE $MMC_INSTALLED"
    fi
else
    fail "SYNC-1" "model_metric_capture.py: installed copy not found at $MMC_INSTALLED"
fi

if [[ -f "$DG_INSTALLED" ]]; then
    if diff -q "$DG_TEMPLATE" "$DG_INSTALLED" > /dev/null 2>&1; then
        pass "SYNC-2" "delegate_gate.py: template and installed are byte-identical"
    else
        fail "SYNC-2" "delegate_gate.py: files DIFFER — diff $DG_TEMPLATE $DG_INSTALLED"
    fi
else
    fail "SYNC-2" "delegate_gate.py: installed copy not found at $DG_INSTALLED"
fi

echo ""

# ---------------------------------------------------------------------------
# SECTION 6: No garbage rows in model_metrics
#
# Counts rows where provider='codex-cli' AND model is NOT in the known
# allowlist. Such rows would indicate the regex matched a non-model token
# (e.g., a shell metacharacter, path component, or bare number).
# Zero is the expected count.
# ---------------------------------------------------------------------------
echo "=== SECTION 6: No garbage rows in model_metrics ==="
echo ""

if [[ -f "$DB" ]]; then
    # Known good allowlist — must stay in sync with _CODEX_ALLOWLIST in model_metric_capture.py
    GARBAGE_COUNT=$(sqlite3 "$DB" \
        "SELECT COUNT(*) FROM model_metrics
         WHERE provider='codex-cli'
           AND model NOT IN (
               'gpt-5.5',
               'gpt-5.4',
               'gpt-5.4-mini',
               'gpt-5.3-codex',
               'gpt-5.3-codex-spark',
               'gpt-5.2'
           );" 2>/dev/null || echo "-1")

    if [[ "$GARBAGE_COUNT" == "0" ]]; then
        pass "DB-1" "model_metrics: 0 garbage codex-cli rows (no bad model tokens captured)"
    elif [[ "$GARBAGE_COUNT" == "-1" ]]; then
        fail "DB-1" "model_metrics: sqlite3 query failed (DB inaccessible or table missing)"
    else
        # Retrieve a sample of offending rows for diagnostics
        echo ""
        echo "  DIAGNOSTIC: garbage rows in model_metrics (provider=codex-cli, bad model):"
        sqlite3 "$DB" \
            "SELECT ts_utc, model, session_id FROM model_metrics
             WHERE provider='codex-cli'
               AND model NOT IN (
                   'gpt-5.5','gpt-5.4','gpt-5.4-mini',
                   'gpt-5.3-codex','gpt-5.3-codex-spark','gpt-5.2'
               )
             ORDER BY rowid DESC LIMIT 5;" 2>/dev/null | \
            while IFS='|' read -r ts model sid; do
                echo "    ts=$ts  model='$model'  session=${sid:0:12}"
            done
        echo ""
        fail "DB-1" "model_metrics: $GARBAGE_COUNT garbage codex-cli rows found (regex matched non-model token)"
    fi
else
    echo "  SKIP  [DB-1]  DB not found at $DB — skipping garbage-row check"
fi

echo ""

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
TOTAL=$((PASS + FAIL))
echo "================================================================"
echo "  Results: $PASS/$TOTAL passed"
if [[ "${#FAILURES[@]}" -gt 0 ]]; then
    echo "  Failed:  ${FAILURES[*]}"
fi
echo "================================================================"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
