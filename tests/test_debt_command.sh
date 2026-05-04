#!/usr/bin/env bash
# Acceptance test for /debt command file.
# Verifier contract: tests OBSERVABLE BEHAVIOR of the artifact against the
# Artifact Contract. Does NOT inspect Worker's implementation strategy.
# Exit 0 = all assertions pass; non-zero = one or more failures.

set -uo pipefail

ARTIFACT="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/commands/debt.md"

PASS=0
FAIL=0

_pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
_fail() { echo "[FAIL] $1 — expected pattern: $2"; FAIL=$((FAIL + 1)); }

# assert_contains FILE PATTERN DESCRIPTION
assert_contains() {
  local file="$1" pattern="$2" desc="$3"
  if grep -qE "$pattern" "$file" 2>/dev/null; then
    _pass "$desc"
  else
    _fail "$desc" "$pattern"
  fi
}

echo "========================================"
echo "/debt command — acceptance test"
echo "Artifact: $ARTIFACT"
echo "========================================"
echo ""

# ── Check 1: file exists and is non-empty ────────────────────────────────────
echo "--- Check 1: file existence ---"
if [[ -s "$ARTIFACT" ]]; then
  _pass "File exists and is non-empty"
else
  if [[ ! -e "$ARTIFACT" ]]; then
    echo "[FAIL] File does not exist: $ARTIFACT"
  else
    echo "[FAIL] File exists but is empty: $ARTIFACT"
  fi
  FAIL=$((FAIL + 1))
fi
echo ""

# Guard: remaining checks only make sense if file is readable
if [[ ! -r "$ARTIFACT" ]]; then
  echo "FATAL: Cannot read artifact — aborting remaining checks."
  exit 1
fi

# ── Check 2: YAML frontmatter with description: field ───────────────────────
echo "--- Check 2: YAML frontmatter ---"
assert_contains "$ARTIFACT" '^description:' \
  "Has YAML frontmatter 'description:' field"
echo ""

# ── Check 3: argument-hint in frontmatter ───────────────────────────────────
echo "--- Check 3: argument-hint ---"
assert_contains "$ARTIFACT" '^argument-hint:' \
  "Has 'argument-hint:' in frontmatter"
echo ""

# ── Check 4: /debt list mode ─────────────────────────────────────────────────
echo "--- Check 4: /debt list mode ---"
assert_contains "$ARTIFACT" '(debt list|`list`|## list|### list|\blist\b.*mode)' \
  "Mentions /debt list mode"
echo ""

# ── Check 5: /debt add mode ──────────────────────────────────────────────────
echo "--- Check 5: /debt add mode ---"
assert_contains "$ARTIFACT" '(debt add|`add`|## add|### add|MODE: add|\badd\b.*mode)' \
  "Mentions /debt add mode"
echo ""

# ── Check 6: /debt work mode ─────────────────────────────────────────────────
echo "--- Check 6: /debt work mode ---"
assert_contains "$ARTIFACT" '(debt work|`work`|## work|### work|\bwork\b.*mode)' \
  "Mentions /debt work mode"
echo ""

# ── Check 7: /debt resolve mode ──────────────────────────────────────────────
echo "--- Check 7: /debt resolve mode ---"
assert_contains "$ARTIFACT" '(debt resolve|`resolve`|## resolve|### resolve|MODE: resolve|\bresolve\b.*mode)' \
  "Mentions /debt resolve mode"
echo ""

# ── Check 8: /debt review mode ───────────────────────────────────────────────
echo "--- Check 8: /debt review mode ---"
assert_contains "$ARTIFACT" '(debt review|`review`|## review|### review|\breview\b.*mode)' \
  "Mentions /debt review mode"
echo ""

# ── Check 9: priority levels HIGH, MED, LOW ──────────────────────────────────
echo "--- Check 9: priority levels ---"
assert_contains "$ARTIFACT" '\bHIGH\b' \
  "Mentions HIGH priority level"
assert_contains "$ARTIFACT" '\bMED\b' \
  "Mentions MED priority level"
assert_contains "$ARTIFACT" '\bLOW\b' \
  "Mentions LOW priority level"
echo ""

# ── Check 10: TaskList or TaskCreate integration ──────────────────────────────
echo "--- Check 10: Task integration ---"
assert_contains "$ARTIFACT" '(TaskList|TaskCreate)' \
  "Mentions TaskList or TaskCreate integration"
echo ""

# ── Check 11: /handover integration ──────────────────────────────────────────
echo "--- Check 11: /handover integration ---"
assert_contains "$ARTIFACT" '(/handover|handover)' \
  "Mentions /handover integration"
echo ""

# ── Check 12: storage file (.session_debts.json or similar) ──────────────────
echo "--- Check 12: storage file ---"
assert_contains "$ARTIFACT" '(\.session_debts\.json|debts\.json|debt.*\.json|\.json.*debt)' \
  "Mentions .session_debts.json or similar JSON storage file"
echo ""

# ── Check 13: git status check for uncommitted changes ───────────────────────
echo "--- Check 13: git status check ---"
assert_contains "$ARTIFACT" '(git status|uncommitted|untracked|dirty.*tree|working.*tree)' \
  "Mentions git status check for uncommitted changes"
echo ""

# ── Check 14: markdown table example ─────────────────────────────────────────
echo "--- Check 14: markdown table ---"
# A markdown table row has pipes: | col | col |
assert_contains "$ARTIFACT" '^\|.*\|.*\|' \
  "Contains a markdown table (pipe-delimited rows for /debt review output)"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "========================================"
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "========================================"

if [[ "$FAIL" -gt 0 ]]; then
  echo "ACCEPTANCE TEST FAILED"
  exit 1
fi

echo "ACCEPTANCE TEST PASSED"
exit 0
