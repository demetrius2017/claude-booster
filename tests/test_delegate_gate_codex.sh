#!/usr/bin/env bash
# Acceptance test: codex_worker.sh as delegation signal in delegate_gate.py
#
# Artifact Contract:
#   When the Lead's Bash tool calls `codex_worker.sh <model>` or
#   `codex exec ... -m <model>`, delegate_gate.py must treat this as a
#   delegation signal — resetting the action budget counter to 0 — exactly
#   the same as an Agent or TaskCreate call.
#
# Observable behavior tested (no LLM judgment, pure exit-code assertions):
#   PASS cases  — delegation signal detected → exit 0, counter reset to 0
#   BLOCK cases — NOT delegation → counter incremented (or recon bypass used)
#
# Exit 0 = all tests pass, non-zero = at least one failure.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE_PY="$REPO_ROOT/templates/scripts/delegate_gate.py"
SCRIPT_DIR="$REPO_ROOT/templates/scripts"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Shared temp dir — cleaned up on exit
# ---------------------------------------------------------------------------
TMPDIR_BASE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_BASE"' EXIT

# ---------------------------------------------------------------------------
# Helper: set up an isolated project directory with a fresh counter file.
# Usage: setup_project <name> [counter_value]
# Returns: path to the project dir (printed)
# ---------------------------------------------------------------------------
setup_project() {
    local name="$1"
    local counter="${2:-0}"
    local dir="$TMPDIR_BASE/$name"
    mkdir -p "$dir/.claude"
    echo "$counter" > "$dir/.claude/.delegate_counter"
    echo "$dir"
}

# ---------------------------------------------------------------------------
# Helper: run the gate with a Bash tool payload and return the exit code.
# Usage: gate_bash_exit <project_dir> <command_string>
# ---------------------------------------------------------------------------
gate_bash_exit() {
    local proj="$1"
    local cmd="$2"
    local gate_home="$TMPDIR_BASE/claude_home_$$"
    mkdir -p "$gate_home/logs"

    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({
    'tool_name': 'Bash',
    'tool_input': {'command': sys.argv[1]},
    'cwd': sys.argv[2],
    'session_id': 'test-verifier-codex',
    'agent_id': '',
    'agent_type': ''
}))
" "$cmd" "$proj")

    env CLAUDE_HOME="$gate_home" \
        CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
        python3 "$GATE_PY" <<< "$payload" 2>/dev/null
    echo $?
}

# ---------------------------------------------------------------------------
# Helper: read counter value from a project dir.
# ---------------------------------------------------------------------------
read_counter() {
    local proj="$1"
    local file="$proj/.claude/.delegate_counter"
    if [[ -f "$file" ]]; then
        cat "$file" | tr -d '[:space:]'
    else
        echo "0"
    fi
}

# ---------------------------------------------------------------------------
# Direct Python test: _bash_is_codex_worker() pattern matching
# (unit-level, no full gate invocation needed — tests the inner function)
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 1: _bash_is_codex_worker() pattern matching ==="

check_codex_worker() {
    local cmd="$1"
    local expect_true="$2"  # "true" or "false"
    local label="$3"

    local tmp_file
    tmp_file=$(mktemp "$TMPDIR_BASE/cmd_XXXXXX.txt")
    printf '%s' "$cmd" > "$tmp_file"

    result=$(python3 - "$tmp_file" <<'PYEOF' 2>/dev/null
import sys
sys.path.insert(0, sys.argv[1].replace(sys.argv[1].split('/')[-1], '') )
# read script dir from env fallback
import os
script_dir = os.environ.get('_GATE_SCRIPT_DIR', '')
sys.path.insert(0, script_dir)
from delegate_gate import _bash_is_codex_worker
with open(sys.argv[1]) as f:
    cmd = f.read()
result = _bash_is_codex_worker(cmd)
print('true' if result else 'false')
PYEOF
)
    rm -f "$tmp_file"
    if [[ "$result" == "$expect_true" ]]; then
        pass "$label"
    else
        fail "$label (expected $expect_true, got $result for: $cmd)"
    fi
}

# Simpler direct helper using SCRIPT_DIR env
check_is_codex() {
    local cmd="$1"
    local expect_true="$2"
    local label="$3"

    local tmp_file
    tmp_file=$(mktemp "$TMPDIR_BASE/cmd_XXXXXX.txt")
    printf '%s' "$cmd" > "$tmp_file"

    result=$(python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from delegate_gate import _bash_is_codex_worker
with open('$tmp_file') as f:
    cmd = f.read()
print('true' if _bash_is_codex_worker(cmd) else 'false')
" 2>/dev/null)
    rm -f "$tmp_file"

    if [[ "$result" == "$expect_true" ]]; then
        pass "$label"
    else
        fail "$label (expected $expect_true, got $result for: $cmd)"
    fi
}

# PASS cases — must be detected as codex_worker delegation
check_is_codex "codex_worker.sh gpt-5.3-codex" "true" \
    "pattern: codex_worker.sh gpt-5.3-codex"
check_is_codex "codex_worker.sh gpt-5.5" "true" \
    "pattern: codex_worker.sh gpt-5.5 (different model)"
check_is_codex "codex_worker.sh gpt-5.3-codex-spark" "true" \
    "pattern: codex_worker.sh gpt-5.3-codex-spark (stdin pipe form)"
check_is_codex "codex exec -m gpt-5.5 --approval-policy on-failure" "true" \
    "pattern: codex exec -m gpt-5.5 with extra flags"
check_is_codex "codex exec --no-git -m gpt-5.3-codex 'write a function'" "true" \
    "pattern: codex exec --no-git -m (extra args before -m)"

# BLOCK cases — must NOT be detected as codex_worker delegation
check_is_codex "grep 'codex_worker' /some/file.sh" "false" \
    "non-delegation: grep mentioning codex_worker.sh"
check_is_codex "vim codex_worker.sh" "false" \
    "non-delegation: vim editing codex_worker.sh"
check_is_codex "cat /path/to/codex_worker.sh" "false" \
    "non-delegation: cat reading codex_worker.sh"
check_is_codex "codex --help" "false" \
    "non-delegation: codex --help (no -m flag)"
check_is_codex "codex exec --no-git 'run task'" "false" \
    "non-delegation: codex exec without -m"

# ---------------------------------------------------------------------------
# SECTION 2: Full gate integration — delegation signal resets counter
#
# Protocol: set counter to 1, invoke gate with codex command, assert
# exit 0 AND counter file contains 0.
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 2: Full gate — counter resets to 0 on delegation signal ==="

# --- Case 1: codex_worker.sh gpt-5.3-codex ---
P1=$(setup_project "p1_basic" 1)
exit1=$(gate_bash_exit "$P1" "codex_worker.sh gpt-5.3-codex < /dev/null")
counter1=$(read_counter "$P1")

if [[ "$exit1" == "0" ]]; then
    pass "S2-C1: codex_worker.sh gpt-5.3-codex → exit 0 (allowed)"
else
    fail "S2-C1: codex_worker.sh gpt-5.3-codex → exit $exit1 (expected 0)"
fi
if [[ "$counter1" == "0" ]]; then
    pass "S2-C1: counter reset to 0 after codex_worker.sh delegation"
else
    fail "S2-C1: counter is $counter1 (expected 0)"
fi

# --- Case 2: codex_worker.sh gpt-5.5 ---
P2=$(setup_project "p2_gpt55" 1)
exit2=$(gate_bash_exit "$P2" "codex_worker.sh gpt-5.5")
counter2=$(read_counter "$P2")

if [[ "$exit2" == "0" ]]; then
    pass "S2-C2: codex_worker.sh gpt-5.5 → exit 0 (allowed)"
else
    fail "S2-C2: codex_worker.sh gpt-5.5 → exit $exit2 (expected 0)"
fi
if [[ "$counter2" == "0" ]]; then
    pass "S2-C2: counter reset to 0 (gpt-5.5 model variant)"
else
    fail "S2-C2: counter is $counter2 (expected 0)"
fi

# --- Case 3: codex_worker.sh gpt-5.3-codex-spark (stdin pipe form) ---
P3=$(setup_project "p3_spark" 1)
exit3=$(gate_bash_exit "$P3" "printf 'task' | codex_worker.sh gpt-5.3-codex-spark")
counter3=$(read_counter "$P3")

if [[ "$exit3" == "0" ]]; then
    pass "S2-C3: printf piped to codex_worker.sh → exit 0 (allowed)"
else
    fail "S2-C3: printf piped to codex_worker.sh → exit $exit3 (expected 0)"
fi
if [[ "$counter3" == "0" ]]; then
    pass "S2-C3: counter reset to 0 (stdin pipe form)"
else
    fail "S2-C3: counter is $counter3 (expected 0)"
fi

# --- Case 4: codex exec -m gpt-5.5 --approval-policy on-failure ---
P4=$(setup_project "p4_codex_exec" 1)
exit4=$(gate_bash_exit "$P4" "codex exec -m gpt-5.5 --approval-policy on-failure")
counter4=$(read_counter "$P4")

if [[ "$exit4" == "0" ]]; then
    pass "S2-C4: codex exec -m gpt-5.5 → exit 0 (allowed)"
else
    fail "S2-C4: codex exec -m gpt-5.5 → exit $exit4 (expected 0)"
fi
if [[ "$counter4" == "0" ]]; then
    pass "S2-C4: counter reset to 0 (codex exec -m form)"
else
    fail "S2-C4: counter is $counter4 (expected 0)"
fi

# --- Case 5: codex exec --no-git -m gpt-5.3-codex 'write a function' ---
P5=$(setup_project "p5_codex_nongit" 1)
exit5=$(gate_bash_exit "$P5" "codex exec --no-git -m gpt-5.3-codex 'write a function'")
counter5=$(read_counter "$P5")

if [[ "$exit5" == "0" ]]; then
    pass "S2-C5: codex exec --no-git -m → exit 0 (allowed)"
else
    fail "S2-C5: codex exec --no-git -m → exit $exit5 (expected 0)"
fi
if [[ "$counter5" == "0" ]]; then
    pass "S2-C5: counter reset to 0 (extra args before -m)"
else
    fail "S2-C5: counter is $counter5 (expected 0)"
fi

# ---------------------------------------------------------------------------
# SECTION 3: Non-delegation Bash commands — NOT treated as delegation signals
#
# grep and cat match RECON patterns → allowed but counter NOT reset (stays 1).
# vim, codex --help, codex exec (no -m) → counted as action or recon.
#
# Key test: set counter=1, call the non-delegation command, assert counter
# was NOT reset to 0 (i.e., it's not treated as a delegation signal).
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 3: Non-delegation commands — counter NOT reset ==="

# --- Case 6: grep 'codex_worker' file.sh — recon, not delegation ---
P6=$(setup_project "p6_grep" 1)
exit6=$(gate_bash_exit "$P6" "grep 'codex_worker' /some/file.sh")
counter6=$(read_counter "$P6")

# grep is RECON → exit 0 allowed, but counter stays at 1 (not reset)
if [[ "$exit6" == "0" ]]; then
    pass "S3-C6: grep with codex_worker → exit 0 (recon bypass, allowed)"
else
    fail "S3-C6: grep with codex_worker → exit $exit6 (expected 0 for recon)"
fi
if [[ "$counter6" != "0" ]]; then
    pass "S3-C6: counter NOT reset (grep is recon, not delegation)"
else
    fail "S3-C6: counter was reset to 0 — grep should NOT be a delegation signal"
fi

# --- Case 7: vim codex_worker.sh — direct action, consumed budget ---
# vim is in ACTIONS (Bash) and NOT recon → increments counter
P7=$(setup_project "p7_vim" 0)
exit7=$(gate_bash_exit "$P7" "vim codex_worker.sh")
counter7=$(read_counter "$P7")

# vim is not recon and not delegation → should be counted (counter incremented from 0)
# exit 0 because we start at 0 and budget=1; counter becomes 1
if [[ "$exit7" == "0" ]]; then
    pass "S3-C7: vim codex_worker.sh → exit 0 (within budget)"
else
    fail "S3-C7: vim codex_worker.sh → exit $exit7 (expected 0, within budget from 0)"
fi
if [[ "$counter7" == "1" ]]; then
    pass "S3-C7: counter incremented to 1 (vim is a direct action, not delegation)"
else
    fail "S3-C7: counter is $counter7 (expected 1 — vim should consume budget, not reset it)"
fi

# --- Case 8: cat /path/to/codex_worker.sh — recon, not delegation ---
P8=$(setup_project "p8_cat" 1)
exit8=$(gate_bash_exit "$P8" "cat /path/to/codex_worker.sh")
counter8=$(read_counter "$P8")

if [[ "$exit8" == "0" ]]; then
    pass "S3-C8: cat codex_worker.sh → exit 0 (recon bypass)"
else
    fail "S3-C8: cat codex_worker.sh → exit $exit8 (expected 0 for recon)"
fi
if [[ "$counter8" != "0" ]]; then
    pass "S3-C8: counter NOT reset (cat is recon, not delegation)"
else
    fail "S3-C8: counter was reset to 0 — cat should NOT be a delegation signal"
fi

# --- Case 9: codex --help — not delegation (no -m flag) ---
# codex --help is not recon and not delegation; it's a Bash action → increments
P9=$(setup_project "p9_help" 0)
exit9=$(gate_bash_exit "$P9" "codex --help")
counter9=$(read_counter "$P9")

# codex --help is within budget from 0
if [[ "$exit9" == "0" ]]; then
    pass "S3-C9: codex --help → exit 0 (within budget, not delegation)"
else
    fail "S3-C9: codex --help → exit $exit9 (expected 0)"
fi
if [[ "$counter9" != "0" ]]; then
    pass "S3-C9: counter NOT reset by codex --help (no -m, not delegation)"
else
    fail "S3-C9: counter was reset to 0 — codex --help should NOT be delegation signal"
fi

# --- Case 10: codex exec --no-git 'run task' — no -m, not delegation ---
P10=$(setup_project "p10_exec_nom" 0)
exit10=$(gate_bash_exit "$P10" "codex exec --no-git 'run task'")
counter10=$(read_counter "$P10")

if [[ "$exit10" == "0" ]]; then
    pass "S3-C10: codex exec (no -m) → exit 0 (within budget, not delegation)"
else
    fail "S3-C10: codex exec (no -m) → exit $exit10 (expected 0)"
fi
if [[ "$counter10" != "0" ]]; then
    pass "S3-C10: counter NOT reset (codex exec without -m is not delegation)"
else
    fail "S3-C10: counter was reset to 0 — codex exec without -m should NOT be delegation"
fi

# ---------------------------------------------------------------------------
# SECTION 4: Budget exhaustion then delegation reset (integration scenario)
#
# Simulate a real workflow: Lead does one action (budget exhausted),
# then calls codex_worker.sh (delegation resets), then can do another action.
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 4: Integration — exhaust budget, delegate, resume ==="

PROJ_INT=$(setup_project "proj_integration" 0)

GATE_HOME_INT="$TMPDIR_BASE/claude_home_int"
mkdir -p "$GATE_HOME_INT/logs"

make_bash_payload() {
    local cwd="$1"
    local cmd="$2"
    python3 -c "
import json, sys
print(json.dumps({
    'tool_name': 'Bash',
    'tool_input': {'command': sys.argv[1]},
    'cwd': sys.argv[2],
    'session_id': 'test-integration',
    'agent_id': '',
    'agent_type': ''
}))
" "$cmd" "$cwd"
}

# Step 1: first action — should be allowed (counter goes from 0 → 1)
PAYLOAD1=$(make_bash_payload "$PROJ_INT" "vim somefile.py")
step1=$(env CLAUDE_HOME="$GATE_HOME_INT" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$PAYLOAD1" 2>/dev/null; echo $?)
if [[ "$step1" == "0" ]]; then
    pass "S4: first direct action (vim) allowed (budget 0→1)"
else
    fail "S4: first action returned $step1 (expected 0)"
fi

# Step 2: second action before delegation — should be BLOCKED (counter was 1)
PAYLOAD2=$(make_bash_payload "$PROJ_INT" "vim anotherfile.py")
step2=$(env CLAUDE_HOME="$GATE_HOME_INT" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$PAYLOAD2" 2>/dev/null; echo $?)
if [[ "$step2" == "2" ]]; then
    pass "S4: second direct action BLOCKED (budget exhausted, exit 2)"
else
    fail "S4: second action returned $step2 (expected 2 — budget exhausted)"
fi

# Step 3: delegation via codex_worker.sh — resets counter to 0
PAYLOAD3=$(make_bash_payload "$PROJ_INT" "codex_worker.sh gpt-5.3-codex")
step3=$(env CLAUDE_HOME="$GATE_HOME_INT" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$PAYLOAD3" 2>/dev/null; echo $?)
counter_after_delegation=$(read_counter "$PROJ_INT")

if [[ "$step3" == "0" ]]; then
    pass "S4: codex_worker.sh delegation allowed (exit 0)"
else
    fail "S4: codex_worker.sh delegation returned $step3 (expected 0)"
fi
if [[ "$counter_after_delegation" == "0" ]]; then
    pass "S4: counter reset to 0 after codex_worker.sh delegation"
else
    fail "S4: counter is $counter_after_delegation after delegation (expected 0)"
fi

# Step 4: next direct action after delegation — should be allowed again
PAYLOAD4=$(make_bash_payload "$PROJ_INT" "vim nextfile.py")
step4=$(env CLAUDE_HOME="$GATE_HOME_INT" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$PAYLOAD4" 2>/dev/null; echo $?)
if [[ "$step4" == "0" ]]; then
    pass "S4: action after delegation allowed (budget refreshed)"
else
    fail "S4: action after delegation returned $step4 (expected 0)"
fi

# ---------------------------------------------------------------------------
# SECTION 5: Integration — codex exec -m also resets via same pattern
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 5: Integration — codex exec -m delegation reset ==="

PROJ_EXEC=$(setup_project "proj_exec_int" 0)
GATE_HOME_EXEC="$TMPDIR_BASE/claude_home_exec"
mkdir -p "$GATE_HOME_EXEC/logs"

# Exhaust budget
PEXEC1=$(make_bash_payload "$PROJ_EXEC" "vim file.py")
e1=$(env CLAUDE_HOME="$GATE_HOME_EXEC" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$PEXEC1" 2>/dev/null; echo $?)
PEXEC2=$(make_bash_payload "$PROJ_EXEC" "vim file2.py")
e2=$(env CLAUDE_HOME="$GATE_HOME_EXEC" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$PEXEC2" 2>/dev/null; echo $?)

if [[ "$e2" == "2" ]]; then
    pass "S5: budget exhausted before codex exec delegation (exit 2)"
else
    fail "S5: budget exhaustion check failed (got $e2, expected 2)"
fi

# Delegate via codex exec -m
PEXEC_D=$(make_bash_payload "$PROJ_EXEC" "codex exec -m gpt-5.5 --approval-policy on-failure")
ed=$(env CLAUDE_HOME="$GATE_HOME_EXEC" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$PEXEC_D" 2>/dev/null; echo $?)
counter_exec=$(read_counter "$PROJ_EXEC")

if [[ "$ed" == "0" ]]; then
    pass "S5: codex exec -m delegation allowed (exit 0)"
else
    fail "S5: codex exec -m delegation returned $ed (expected 0)"
fi
if [[ "$counter_exec" == "0" ]]; then
    pass "S5: counter reset to 0 after codex exec -m delegation"
else
    fail "S5: counter is $counter_exec after codex exec -m (expected 0)"
fi

# Resume after delegation
PEXEC3=$(make_bash_payload "$PROJ_EXEC" "vim file3.py")
e3=$(env CLAUDE_HOME="$GATE_HOME_EXEC" CLAUDE_BOOSTER_SKIP_DELEGATE_GATE="" \
    python3 "$GATE_PY" <<< "$PEXEC3" 2>/dev/null; echo $?)
if [[ "$e3" == "0" ]]; then
    pass "S5: action after codex exec -m delegation allowed"
else
    fail "S5: action after codex exec -m delegation returned $e3 (expected 0)"
fi

# ---------------------------------------------------------------------------
# SECTION 6: Docstring — codex_worker documented in delegate_gate.py
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 6: Documentation — codex_worker in module docstring ==="

if head -90 "$GATE_PY" | grep -qi "codex_worker"; then
    pass "S6: delegate_gate.py first 90 lines mention 'codex_worker'"
else
    fail "S6: delegate_gate.py has no mention of 'codex_worker' in the first 90 lines"
fi

if head -90 "$GATE_PY" | grep -qi "codex exec"; then
    pass "S6: delegate_gate.py first 90 lines mention 'codex exec'"
else
    fail "S6: delegate_gate.py has no mention of 'codex exec' in the first 90 lines"
fi

# ---------------------------------------------------------------------------
# SECTION 7: CODEX_WORKER_PATTERNS constant exists in delegate_gate.py
# ---------------------------------------------------------------------------
echo ""
echo "=== SECTION 7: CODEX_WORKER_PATTERNS constant present ==="

if grep -q "CODEX_WORKER_PATTERNS" "$GATE_PY"; then
    pass "S7: CODEX_WORKER_PATTERNS constant exists in delegate_gate.py"
else
    fail "S7: CODEX_WORKER_PATTERNS not found in delegate_gate.py"
fi

if grep -q "_bash_is_codex_worker" "$GATE_PY"; then
    pass "S7: _bash_is_codex_worker() function exists"
else
    fail "S7: _bash_is_codex_worker() function not found in delegate_gate.py"
fi

# ---------------------------------------------------------------------------
# SUMMARY
# ---------------------------------------------------------------------------
echo ""
echo "========================================"
echo "Results: $PASS passed, $FAIL failed"
echo "========================================"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
