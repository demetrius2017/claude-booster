#!/usr/bin/env bash
# Acceptance test for delegate_gate.py phase-awareness and ^ anchor fix.
#
# Artifact Contract: Add phase awareness to delegate_gate.py so RECON and
# PLAN phases are exempt from the delegation budget, and fix the `^` anchor
# bug in RECON_BASH_PATTERNS so compound commands like `cd /path && ls` match.
#
# Tests OBSERVABLE BEHAVIOR only. Exit 0 = PASS, non-zero = FAIL.
# All subprocess calls use Python to invoke delegate_gate.py directly.

set -e

GATE="python3 /Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/delegate_gate.py"
GATE_PY="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/delegate_gate.py"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------
TMPDIR_BASE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_BASE"' EXIT

# ---------------------------------------------------------------------------
# Helper: invoke gate with a JSON payload, returns exit code
# Accepts env overrides as first arg (optional assoc, passed as env key=val)
# ---------------------------------------------------------------------------
run_gate() {
    local json="$1"
    local project_dir="$2"
    local extra_env="${3:-}"

    # Override CLAUDE_HOME to isolate log output in temp dir
    local gate_home="$TMPDIR_BASE/claude_home"
    mkdir -p "$gate_home/logs"

    env CLAUDE_HOME="$gate_home" \
        CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
        $extra_env \
        $GATE <<< "$json" 2>/dev/null
}

run_gate_exit() {
    local json="$1"
    local extra_env="${2:-}"
    local gate_home="$TMPDIR_BASE/claude_home"
    mkdir -p "$gate_home/logs"

    env CLAUDE_HOME="$gate_home" \
        CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
        $extra_env \
        $GATE <<< "$json" 2>/dev/null
    echo $?
}

# ---------------------------------------------------------------------------
# Helper: get exit code without set -e aborting
# ---------------------------------------------------------------------------
gate_exit_code() {
    local json="$1"
    local gate_home="$2"
    local extra_env="${3:-}"

    env CLAUDE_HOME="$gate_home" \
        CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
        $extra_env \
        $GATE <<< "$json" 2>/dev/null
    echo "$?"
}

# ---------------------------------------------------------------------------
# SECTION 1: _bash_is_recon pattern fix (^ anchor bug)
# Test via Python import — checks the function directly.
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 1: _bash_is_recon pattern fix ==="

check_recon() {
    local cmd="$1"
    local expect_true="$2"  # "true" or "false"
    local label="$3"

    # Write the command to a temp file to avoid quoting/substitution issues
    local tmp_cmd_file
    tmp_cmd_file=$(mktemp "$TMPDIR_BASE/cmd_XXXXXX.txt")
    printf '%s' "$cmd" > "$tmp_cmd_file"

    result=$(python3 - "$tmp_cmd_file" <<'PYEOF' 2>/dev/null
import sys
sys.path.insert(0, '/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts')
from delegate_gate import _bash_is_recon
with open(sys.argv[1]) as f:
    cmd = f.read()
result = _bash_is_recon(cmd)
print('true' if result else 'false')
PYEOF
)
    rm -f "$tmp_cmd_file"
    if [[ "$result" == "$expect_true" ]]; then
        pass "$label"
    else
        fail "$label (expected $expect_true, got $result for cmd: $cmd)"
    fi
}

# These three must be True after the ^ anchor fix
check_recon "cd /path && ls foo" "true"  "compound 'cd && ls' is recon"
check_recon "cd /path; grep bar baz" "true"  "compound 'cd; grep' is recon"
check_recon "ls foo" "true"  "standalone 'ls foo' still recon (no regression)"

# Safety check: dangerous command after && must NOT be recon
check_recon "cd /path && rm -rf foo" "false"  "compound 'cd && rm -rf' is NOT recon"
check_recon "python3 script.py && rm -rf foo" "false"  "generic compound with rm -rf is NOT recon"

# ---------------------------------------------------------------------------
# SECTION 2: Phase exemption via main() — RECON phase
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 2: Phase exemption — RECON ==="

setup_project() {
    local name="$1"
    local dir="$TMPDIR_BASE/$name"
    mkdir -p "$dir/.claude"
    echo "$dir"
}

make_bash_payload() {
    local cwd="$1"
    local cmd="${2:-python3 ./custom_script.py}"   # non-recon, non-delegation command
    printf '{"tool_name":"Bash","tool_input":{"command":"%s"},"cwd":"%s","session_id":"test-session-001"}' \
        "$cmd" "$cwd"
}

make_edit_payload() {
    local cwd="$1"
    printf '{"tool_name":"Edit","tool_input":{"file_path":"%s/src/app.py"},"cwd":"%s","session_id":"test-session-001"}' \
        "$cwd" "$cwd"
}

# --- RECON phase: both calls must be allowed (exit 0) ---
PROJ_RECON=$(setup_project "proj_recon")
echo "RECON" > "$PROJ_RECON/.claude/.phase"

# Reset counter to 0 (pristine state)
echo "0" > "$PROJ_RECON/.claude/.delegate_counter"

GATE_HOME="$TMPDIR_BASE/claude_home_recon"
mkdir -p "$GATE_HOME/logs"

PAYLOAD=$(make_edit_payload "$PROJ_RECON")

exit1=$(env CLAUDE_HOME="$GATE_HOME" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$PAYLOAD" 2>/dev/null; echo $?)
if [[ "$exit1" == "0" ]]; then
    pass "RECON phase: first Edit call allowed (exit 0)"
else
    fail "RECON phase: first Edit call blocked (exit $exit1), expected 0"
fi

# Second call — must also be allowed (counter not incremented under RECON)
exit2=$(env CLAUDE_HOME="$GATE_HOME" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$PAYLOAD" 2>/dev/null; echo $?)
if [[ "$exit2" == "0" ]]; then
    pass "RECON phase: second Edit call still allowed (counter not incremented)"
else
    fail "RECON phase: second Edit call blocked (exit $exit2), expected 0"
fi

# Verify counter was NOT incremented (should stay at 0)
counter_after=$(cat "$PROJ_RECON/.claude/.delegate_counter" 2>/dev/null || echo "0")
if [[ "$counter_after" == "0" ]]; then
    pass "RECON phase: counter stayed at 0 after two calls"
else
    fail "RECON phase: counter incremented to $counter_after (should be 0)"
fi

# ---------------------------------------------------------------------------
# SECTION 3: Phase exemption — PLAN phase
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 3: Phase exemption — PLAN ==="

PROJ_PLAN=$(setup_project "proj_plan")
echo "PLAN" > "$PROJ_PLAN/.claude/.phase"
echo "0" > "$PROJ_PLAN/.claude/.delegate_counter"

GATE_HOME_PLAN="$TMPDIR_BASE/claude_home_plan"
mkdir -p "$GATE_HOME_PLAN/logs"

PLAN_PAYLOAD=$(make_edit_payload "$PROJ_PLAN")

exit_plan1=$(env CLAUDE_HOME="$GATE_HOME_PLAN" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$PLAN_PAYLOAD" 2>/dev/null; echo $?)
exit_plan2=$(env CLAUDE_HOME="$GATE_HOME_PLAN" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$PLAN_PAYLOAD" 2>/dev/null; echo $?)

if [[ "$exit_plan1" == "0" ]] && [[ "$exit_plan2" == "0" ]]; then
    pass "PLAN phase: both calls allowed (budget exemption applies)"
else
    fail "PLAN phase: call1=$exit_plan1, call2=$exit_plan2 (expected both 0)"
fi

# Case-insensitive: lowercase "recon" must also exempt
PROJ_LOWER=$(setup_project "proj_lower")
echo "recon" > "$PROJ_LOWER/.claude/.phase"
echo "0" > "$PROJ_LOWER/.claude/.delegate_counter"

GATE_HOME_LOWER="$TMPDIR_BASE/claude_home_lower"
mkdir -p "$GATE_HOME_LOWER/logs"

LOWER_PAYLOAD=$(make_edit_payload "$PROJ_LOWER")
exit_lower=$(env CLAUDE_HOME="$GATE_HOME_LOWER" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$LOWER_PAYLOAD" 2>/dev/null; echo $?)
if [[ "$exit_lower" == "0" ]]; then
    pass "RECON phase (lowercase): exemption is case-insensitive"
else
    fail "RECON phase (lowercase): blocked (exit $exit_lower), expected 0"
fi

# ---------------------------------------------------------------------------
# SECTION 4: IMPLEMENT phase — budget enforced (gate fires as before)
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 4: IMPLEMENT phase — budget enforced ==="

PROJ_IMPL=$(setup_project "proj_implement")
echo "IMPLEMENT" > "$PROJ_IMPL/.claude/.phase"
echo "0" > "$PROJ_IMPL/.claude/.delegate_counter"

GATE_HOME_IMPL="$TMPDIR_BASE/claude_home_impl"
mkdir -p "$GATE_HOME_IMPL/logs"

IMPL_PAYLOAD=$(make_edit_payload "$PROJ_IMPL")

exit_impl1=$(env CLAUDE_HOME="$GATE_HOME_IMPL" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$IMPL_PAYLOAD" 2>/dev/null; echo $?)
exit_impl2=$(env CLAUDE_HOME="$GATE_HOME_IMPL" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$IMPL_PAYLOAD" 2>/dev/null; echo $?)

if [[ "$exit_impl1" == "0" ]]; then
    pass "IMPLEMENT phase: first Edit allowed (within budget)"
else
    fail "IMPLEMENT phase: first Edit blocked (exit $exit_impl1), expected 0"
fi

if [[ "$exit_impl2" == "2" ]]; then
    pass "IMPLEMENT phase: second Edit blocked (budget exhausted, exit 2)"
else
    fail "IMPLEMENT phase: second Edit returned $exit_impl2, expected 2"
fi

# ---------------------------------------------------------------------------
# SECTION 5: No .phase file — backward compat (gate fires as before)
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 5: No .phase file — backward compat ==="

PROJ_NOPHASE=$(setup_project "proj_nophase")
# No .phase file created
echo "0" > "$PROJ_NOPHASE/.claude/.delegate_counter"

GATE_HOME_NOPHASE="$TMPDIR_BASE/claude_home_nophase"
mkdir -p "$GATE_HOME_NOPHASE/logs"

NO_PAYLOAD=$(make_edit_payload "$PROJ_NOPHASE")

exit_no1=$(env CLAUDE_HOME="$GATE_HOME_NOPHASE" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$NO_PAYLOAD" 2>/dev/null; echo $?)
exit_no2=$(env CLAUDE_HOME="$GATE_HOME_NOPHASE" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" $GATE <<< "$NO_PAYLOAD" 2>/dev/null; echo $?)

if [[ "$exit_no1" == "0" ]]; then
    pass "No .phase file: first Edit allowed (within budget)"
else
    fail "No .phase file: first Edit blocked (exit $exit_no1), expected 0"
fi

if [[ "$exit_no2" == "2" ]]; then
    pass "No .phase file: second Edit blocked (budget enforced, exit 2)"
else
    fail "No .phase file: second Edit returned $exit_no2, expected 2"
fi

# ---------------------------------------------------------------------------
# SECTION 6: Decision logging — "phase" appears in reason for RECON-exempt call
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 6: Decision log — phase in reason field ==="

PROJ_LOG=$(setup_project "proj_log")
echo "RECON" > "$PROJ_LOG/.claude/.phase"
echo "0" > "$PROJ_LOG/.claude/.delegate_counter"

GATE_HOME_LOG="$TMPDIR_BASE/claude_home_log"
mkdir -p "$GATE_HOME_LOG/logs"

LOG_PAYLOAD=$(make_edit_payload "$PROJ_LOG")

env CLAUDE_HOME="$GATE_HOME_LOG" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    $GATE <<< "$LOG_PAYLOAD" 2>/dev/null || true

LOG_FILE="$GATE_HOME_LOG/logs/delegate_gate_decisions.jsonl"
if [[ -f "$LOG_FILE" ]]; then
    # Find the last "allow" decision and check for "phase" in the reason
    if python3 - <<PYEOF 2>/dev/null
import json
found = False
with open("$LOG_FILE") as f:
    for line in f:
        try:
            rec = json.loads(line.strip())
            if rec.get("decision") == "allow" and "phase" in str(rec.get("reason", "")).lower():
                found = True
        except Exception:
            pass
import sys
sys.exit(0 if found else 1)
PYEOF
    then
        pass "Decision log: RECON-phase allow decision contains 'phase' in reason"
    else
        # Show what was logged for diagnostics
        echo "  Log contents:"
        python3 -c "
import json
try:
    with open('$LOG_FILE') as f:
        for line in f:
            try:
                rec = json.loads(line.strip())
                print('  decision=%s reason=%s' % (rec.get('decision','?'), rec.get('reason','?')))
            except: pass
except: pass
" 2>/dev/null || true
        fail "Decision log: no 'allow' decision with 'phase' in reason found in log"
    fi
else
    fail "Decision log: log file not created at $LOG_FILE"
fi

# ---------------------------------------------------------------------------
# SECTION 7: start.md — phase set to RECON before diagnostic steps
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 7: start.md sets phase to RECON ==="

START_MD="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/commands/start.md"

if [[ ! -f "$START_MD" ]]; then
    fail "start.md not found at $START_MD"
else
    # Check that RECON phase is set — look for ".phase" or "RECON" reference
    # in the first half of the document (before the main diagnostic steps)
    if grep -qi "\.phase\|set.*phase.*recon\|phase.*recon\|recon.*phase" "$START_MD"; then
        pass "start.md contains phase/RECON reference"
    else
        fail "start.md has no mention of .phase or RECON phase-setting"
    fi
fi

# ---------------------------------------------------------------------------
# SECTION 8: docstring — "phase" documented in first 80 lines of delegate_gate.py
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 8: Module docstring documents phase-awareness ==="

if head -80 "$GATE_PY" | grep -qi "phase"; then
    pass "delegate_gate.py first 80 lines mention 'phase' (docstring updated)"
else
    fail "delegate_gate.py first 80 lines have no mention of 'phase'"
fi

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
echo "Results: $PASS passed, $FAIL failed"
echo "========================================"

if [[ $FAIL -gt 0 ]]; then
    exit 1
fi
exit 0
