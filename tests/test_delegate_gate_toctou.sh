#!/usr/bin/env bash
# Acceptance test: _atomic_increment / _atomic_reset / _read_counter in delegate_gate.py
#
# Tests observable behavior of the fcntl.flock-based atomic counter:
#   1. Sequential increments return 1, 2, 3
#   2. _atomic_reset sets counter to 0; next increment returns 1
#   3. _read_counter reads value without modifying it
#   4. TOCTOU / race test: two parallel increments must produce {1, 2} not {1, 1}
#   5. Missing-directory handled (mkdir on demand)
#   6. Corrupted counter file handled (non-integer content → treats as 0)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$REPO_ROOT/templates/scripts"
PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Helpers that call into delegate_gate module via python3 -c
# Each call creates a fresh interpreter — no shared state in-process.
# ---------------------------------------------------------------------------

py_increment() {
    # $1 = tmpdir path; prints the returned integer
    python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from delegate_gate import _atomic_increment
from pathlib import Path
print(_atomic_increment(Path('$1')))
"
}

py_reset() {
    # $1 = tmpdir path
    python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from delegate_gate import _atomic_reset
from pathlib import Path
_atomic_reset(Path('$1'))
"
}

py_read() {
    # $1 = tmpdir path; prints current counter value
    python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from delegate_gate import _read_counter
from pathlib import Path
print(_read_counter(Path('$1')))
"
}

# ---------------------------------------------------------------------------
# Test 1 — missing directory is created automatically by _atomic_increment
# ---------------------------------------------------------------------------
T1=$(mktemp -d)
rm -rf "$T1/.claude"   # ensure subdir doesn't exist yet

v1=$(py_increment "$T1")
if [[ "$v1" == "1" ]]; then
    pass "T1: missing .claude/ dir handled; first increment returns 1"
else
    fail "T1: expected 1, got $v1"
fi
rm -rf "$T1"

# ---------------------------------------------------------------------------
# Test 2 — sequential increments: 1, 2, 3
# ---------------------------------------------------------------------------
T2=$(mktemp -d)

v1=$(py_increment "$T2")
v2=$(py_increment "$T2")
v3=$(py_increment "$T2")

if [[ "$v1" == "1" && "$v2" == "2" && "$v3" == "3" ]]; then
    pass "T2: sequential increments return 1, 2, 3"
else
    fail "T2: expected 1 2 3, got $v1 $v2 $v3"
fi
rm -rf "$T2"

# ---------------------------------------------------------------------------
# Test 3 — _atomic_reset zeros the counter; next increment returns 1
# ---------------------------------------------------------------------------
T3=$(mktemp -d)

py_increment "$T3" > /dev/null
py_increment "$T3" > /dev/null  # counter is now 2
py_reset     "$T3"
v_after=$(py_increment "$T3")   # should be 1 again

if [[ "$v_after" == "1" ]]; then
    pass "T3: reset to 0; next increment returns 1"
else
    fail "T3: expected 1 after reset, got $v_after"
fi
rm -rf "$T3"

# ---------------------------------------------------------------------------
# Test 4 — _read_counter does not modify counter
# ---------------------------------------------------------------------------
T4=$(mktemp -d)

py_increment "$T4" > /dev/null  # counter = 1
before=$(py_read "$T4")
py_read "$T4" > /dev/null       # should not change it
py_read "$T4" > /dev/null
after=$(py_read "$T4")

if [[ "$before" == "1" && "$after" == "1" ]]; then
    pass "T4: _read_counter is non-destructive (read 3 times, counter still 1)"
else
    fail "T4: expected before=1 after=1, got before=$before after=$after"
fi
rm -rf "$T4"

# ---------------------------------------------------------------------------
# Test 5 — corrupted counter file (non-integer) treated as 0
# ---------------------------------------------------------------------------
T5=$(mktemp -d)
mkdir -p "$T5/.claude"
printf 'not-a-number\n' > "$T5/.claude/.delegate_counter"

v=$(py_increment "$T5")  # should treat corrupt as 0 → return 1
if [[ "$v" == "1" ]]; then
    pass "T5: corrupted counter file handled; increment returns 1"
else
    fail "T5: expected 1 after corrupt file, got $v"
fi
rm -rf "$T5"

# ---------------------------------------------------------------------------
# Test 6 — TOCTOU / race test (critical — proves the lock works)
#
# Strategy: run two parallel _atomic_increment processes, capture their
# return values, assert the multiset is {1, 2} — never {1, 1}.
# Repeat 5 times to stress the race window.
# ---------------------------------------------------------------------------

RACE_FAIL=0

for iteration in 1 2 3 4 5; do
    TDIR=$(mktemp -d)

    # Capture output of each subprocess into a temp file
    OUT1=$(mktemp)
    OUT2=$(mktemp)

    # Spawn two Python processes that each do one _atomic_increment
    python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from delegate_gate import _atomic_increment
from pathlib import Path
print(_atomic_increment(Path('$TDIR')))
" > "$OUT1" &
    PID1=$!

    python3 -c "
import sys
sys.path.insert(0, '$SCRIPT_DIR')
from delegate_gate import _atomic_increment
from pathlib import Path
print(_atomic_increment(Path('$TDIR')))
" > "$OUT2" &
    PID2=$!

    wait $PID1
    wait $PID2

    R1=$(cat "$OUT1" | tr -d '[:space:]')
    R2=$(cat "$OUT2" | tr -d '[:space:]')
    rm -f "$OUT1" "$OUT2"

    # Sort the two values so we can compare order-independently
    SORTED=$(echo -e "$R1\n$R2" | sort -n | tr '\n' ' ' | tr -d ' ')

    if [[ "$SORTED" == "12" ]]; then
        : # good
    else
        echo "  RACE iter $iteration: got ($R1, $R2) — expected {1,2}"
        RACE_FAIL=$((RACE_FAIL + 1))
    fi

    rm -rf "$TDIR"
done

if [[ "$RACE_FAIL" -eq 0 ]]; then
    pass "T6: TOCTOU race test — 5/5 iterations produced {1,2}, not {1,1}"
else
    fail "T6: TOCTOU race test — $RACE_FAIL/5 iterations produced wrong values (lock not working or partial)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
exit 0
