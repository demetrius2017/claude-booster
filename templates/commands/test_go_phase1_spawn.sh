#!/usr/bin/env bash
# Acceptance test for go.md Phase 1 — FLOW DESIGNER provider-aware spawn instruction.
# Runs from repo root. Tests OBSERVABLE behavior only: section-scoped grep/awk + git baseline diff.
# Exit 0 iff all cases pass.
set -u

FILE="templates/commands/go.md"
PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1 — expected $2 got $3"; FAIL=$((FAIL + 1)); }

# Section extractor: lines strictly between '## Phase 1 — FLOW DESIGNER' and '## Phase 1B'.
extract_phase1() {
  awk '/^## Phase 1 — FLOW DESIGNER/{f=1;next} /^## Phase 1B/{f=0} f'
}

# --- Preconditions -----------------------------------------------------------

if [ ! -f "$FILE" ]; then
  echo "[FAIL] precondition: $FILE not found — expected file got missing"
  echo "Results: 0 passed, 1 failed"
  exit 1
fi

WT="$(extract_phase1 < "$FILE")"

# Case A (FM-011): extraction window is well-formed — exactly one '## Phase 1B' terminator.
n_term="$(grep -c '^## Phase 1B' "$FILE")"
if [ "$n_term" -eq 1 ]; then
  pass "A: exactly one '## Phase 1B' terminator header"
else
  fail "A: '## Phase 1B' terminator count" "1" "$n_term"
fi

# Case B (FM-011): extract terminated at the boundary, not run-to-EOF (no '## Phase 2' leaked in).
if printf '%s\n' "$WT" | grep -q '^## Phase 2'; then
  fail "B: extract bounded (no '## Phase 2' leaked)" "no '## Phase 2' line" "found one"
else
  pass "B: extract bounded — no '## Phase 2' line leaked into Phase-1 window"
fi

# --- Content assertions (all against the Phase-1 extract) --------------------

# Case C (FM-013): codex branch — 'codex-cli' present in Phase-1 extract.
if printf '%s\n' "$WT" | grep -q 'codex-cli'; then
  pass "C: 'codex-cli' present in Phase-1 extract"
else
  fail "C: 'codex-cli' in Phase-1 extract" "present" "absent"
fi

# Case D (FM-013): codex branch — 'codex_worker.sh' present in Phase-1 extract.
if printf '%s\n' "$WT" | grep -q 'codex_worker\.sh'; then
  pass "D: 'codex_worker.sh' present in Phase-1 extract"
else
  fail "D: 'codex_worker.sh' in Phase-1 extract" "present" "absent"
fi

# Case E (FM-013): codex-cli and codex_worker.sh are co-located (within ~6 lines).
#   Find min distance between any 'codex-cli' line and any 'codex_worker.sh' line.
codex_colocated="$(printf '%s\n' "$WT" | awk '
  /codex-cli/        { for (i in cw) { d=NR-i; if (d<0) d=-d; if (d<min) min=d }; cc[NR]=1; if (min<=6) found=1 }
  /codex_worker\.sh/ { for (i in cc) { d=NR-i; if (d<0) d=-d; if (d<min) min=d }; cw[NR]=1; if (min<=6) found=1 }
  BEGIN { min=999999 }
  END   { print (found?"yes":"no") }
')"
if [ "$codex_colocated" = "yes" ]; then
  pass "E: 'codex-cli' and 'codex_worker.sh' co-located within 6 lines"
else
  fail "E: codex-cli/codex_worker.sh co-location (<=6 lines)" "yes" "$codex_colocated"
fi

# Case F: anthropic branch — 'anthropic' present in Phase-1 extract.
if printf '%s\n' "$WT" | grep -q 'anthropic'; then
  pass "F: 'anthropic' present in Phase-1 extract"
else
  fail "F: 'anthropic' in Phase-1 extract" "present" "absent"
fi

# Case G: anthropic branch — 'Agent tool' reference present in Phase-1 extract.
if printf '%s\n' "$WT" | grep -qi 'Agent tool'; then
  pass "G: 'Agent tool' reference present in Phase-1 extract"
else
  fail "G: 'Agent tool' in Phase-1 extract" "present" "absent"
fi

# Case H: anthropic branch — 'Agent tool' co-located with 'anthropic' or the model/opus fallback (<=6 lines).
anth_colocated="$(printf '%s\n' "$WT" | awk '
  BEGIN { min=999999 }
  /[Aa]gent tool/                 { at[NR]=1 }
  /anthropic|opus|[Mm]odel/       { mk[NR]=1 }
  END {
    for (i in at) for (j in mk) { d=i-j; if (d<0) d=-d; if (d<min) min=d }
    print (min<=6 ? "yes" : "no")
  }
')"
if [ "$anth_colocated" = "yes" ]; then
  pass "H: 'Agent tool' co-located with anthropic/model/opus within 6 lines"
else
  fail "H: Agent-tool/anthropic-or-opus co-location (<=6 lines)" "yes" "$anth_colocated"
fi

# Case I (FM-012): negative — literal 'codex_sandbox_worker.sh' must NOT appear in Phase-1 extract.
#   Exact substring match on the full name; presence of 'codex_worker.sh' must NOT trip this.
if printf '%s\n' "$WT" | grep -qF 'codex_sandbox_worker.sh'; then
  fail "I: 'codex_sandbox_worker.sh' absent from Phase-1 extract" "absent" "present"
else
  pass "I: 'codex_sandbox_worker.sh' absent from Phase-1 extract (TEXT channel, not diff)"
fi

# --- Differential vs git baseline (FM-013 — cannot pass on unedited file) -----

# Case J: baseline Phase-1 extract must NOT contain 'codex-cli'; working tree MUST.
BASE="$(git show HEAD:"$FILE" 2>/dev/null | extract_phase1)"
base_has="no"; wt_has="no"
printf '%s\n' "$BASE" | grep -q 'codex-cli' && base_has="yes"
printf '%s\n' "$WT"   | grep -q 'codex-cli' && wt_has="yes"
if [ "$base_has" = "no" ] && [ "$wt_has" = "yes" ]; then
  pass "J: differential — 'codex-cli' absent in HEAD baseline, present in working tree"
else
  fail "J: differential codex-cli (baseline=no, worktree=yes)" "no/yes" "$base_has/$wt_has"
fi

# --- Preservation of pre-existing Phase-1 semantics --------------------------

# Case K: FD prompt sentinel preserved.
if printf '%s\n' "$WT" | grep -qF 'You are a Flow Designer agent'; then
  pass "K: 'You are a Flow Designer agent' sentinel preserved"
else
  fail "K: FD sentinel preserved" "present" "absent"
fi

# Case L: adjacent_findings instruction preserved.
if printf '%s\n' "$WT" | grep -q 'adjacent_findings'; then
  pass "L: 'adjacent_findings' instruction preserved"
else
  fail "L: 'adjacent_findings' preserved" "present" "absent"
fi

# Case M: 'run_in_background' negation semantics preserved.
if printf '%s\n' "$WT" | grep -q 'run_in_background'; then
  pass "M: 'run_in_background' semantics preserved"
else
  fail "M: 'run_in_background' preserved" "present" "absent"
fi

# Case N: 'Lead waits' semantics preserved.
if printf '%s\n' "$WT" | grep -qi 'Lead waits'; then
  pass "N: 'Lead waits' semantics preserved"
else
  fail "N: 'Lead waits' preserved" "present" "absent"
fi

# --- Summary -----------------------------------------------------------------

echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
