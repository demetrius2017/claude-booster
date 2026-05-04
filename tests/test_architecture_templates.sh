#!/usr/bin/env bash
# Acceptance test for architecture template files.
# Verifier contract: tests observable behavior against the Artifact Contract.
# Exit 0 = all assertions pass; non-zero = first failure (set -e).

set -euo pipefail

REPO_ROOT="/Users/dmitrijnazarov/Projects/Claude_Booster"
ARCH_MD="${REPO_ROOT}/templates/ARCHITECTURE.md"
DEP_JSON="${REPO_ROOT}/templates/dep_manifest.json"
ADR_MD="${REPO_ROOT}/templates/docs/adr/ADR-TEMPLATE.md"

PASS=0
FAIL=0

_pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
_fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

# Helper: assert a grep pattern is found in a file.
# Usage: assert_contains FILE PATTERN DESCRIPTION
assert_contains() {
  local file="$1" pattern="$2" desc="$3"
  if grep -qE "$pattern" "$file" 2>/dev/null; then
    _pass "$desc"
  else
    _fail "$desc  (pattern not found: $pattern)"
  fi
}

# Helper: assert file exists and is non-empty.
assert_nonempty_file() {
  local file="$1" label="$2"
  if [[ -s "$file" ]]; then
    _pass "$label exists and is non-empty"
  else
    if [[ ! -e "$file" ]]; then
      _fail "$label: file does not exist ($file)"
    else
      _fail "$label: file is empty ($file)"
    fi
  fi
}

echo "========================================"
echo "Architecture template acceptance test"
echo "========================================"
echo ""

# ── Check 1-3: all three files exist and are non-empty ──────────────────────
echo "--- File existence ---"
assert_nonempty_file "$ARCH_MD"  "ARCHITECTURE.md"
assert_nonempty_file "$DEP_JSON" "dep_manifest.json"
assert_nonempty_file "$ADR_MD"   "ADR-TEMPLATE.md"
echo ""

# ── ARCHITECTURE.md checks ───────────────────────────────────────────────────
echo "--- ARCHITECTURE.md content ---"

# Check 4: contains mermaid diagram block
assert_contains "$ARCH_MD" '```mermaid' \
  "ARCHITECTURE.md: contains Mermaid diagram block"

# Check 5: dependency table with "Reads from" column header
assert_contains "$ARCH_MD" 'Reads from' \
  "ARCHITECTURE.md: dependency table has 'Reads from' column"

# Check 6: dependency table with "Writes to" column header
assert_contains "$ARCH_MD" 'Writes to' \
  "ARCHITECTURE.md: dependency table has 'Writes to' column"

# Check 7: invariants section with INV-01 through INV-08 (all eight must appear)
for inv in INV-01 INV-02 INV-03 INV-04 INV-05 INV-06 INV-07 INV-08; do
  assert_contains "$ARCH_MD" "$inv" \
    "ARCHITECTURE.md: invariants section contains $inv"
done

# Check 8: "Protected Paths" or "Derived" section
if grep -qE '(Protected Paths|Derived)' "$ARCH_MD" 2>/dev/null; then
  _pass "ARCHITECTURE.md: contains 'Protected Paths' or 'Derived' section"
else
  _fail "ARCHITECTURE.md: missing 'Protected Paths' or 'Derived' section"
fi

echo ""

# ── dep_manifest.json checks ─────────────────────────────────────────────────
echo "--- dep_manifest.json content ---"

# Check 9: valid JSON
if jq empty "$DEP_JSON" 2>/dev/null; then
  _pass "dep_manifest.json: valid JSON"
else
  _fail "dep_manifest.json: invalid JSON (jq parse failed)"
fi

# Check 10: top-level keys exist
for key in components data_patches_forbidden append_only_tables invariants; do
  if jq -e "has(\"$key\")" "$DEP_JSON" >/dev/null 2>&1; then
    _pass "dep_manifest.json: has top-level key '$key'"
  else
    _fail "dep_manifest.json: missing top-level key '$key'"
  fi
done

# Check 11: components has at least 5 entries, each with a "critical" field
component_count=$(jq 'if .components | type == "array" then .components | length
                      elif .components | type == "object" then .components | keys | length
                      else 0 end' "$DEP_JSON" 2>/dev/null || echo 0)
if [[ "$component_count" -ge 5 ]]; then
  _pass "dep_manifest.json: components has ${component_count} entries (≥5 required)"
else
  _fail "dep_manifest.json: components has ${component_count} entries (need ≥5)"
fi

# Each component must have a "critical" field (works for both array and object shapes)
critical_check=$(jq '
  if .components | type == "array" then
    [ .components[] | has("critical") ] | all
  elif .components | type == "object" then
    [ .components | to_entries[] | .value | has("critical") ] | all
  else false end
' "$DEP_JSON" 2>/dev/null || echo "false")

if [[ "$critical_check" == "true" ]]; then
  _pass "dep_manifest.json: every component has a 'critical' field"
else
  _fail "dep_manifest.json: one or more components missing 'critical' field"
fi

# Check 12: data_patches_forbidden has at least 3 entries
forbidden_count=$(jq 'if .data_patches_forbidden | type == "array" then .data_patches_forbidden | length
                      elif .data_patches_forbidden | type == "object" then .data_patches_forbidden | keys | length
                      else 0 end' "$DEP_JSON" 2>/dev/null || echo 0)
if [[ "$forbidden_count" -ge 3 ]]; then
  _pass "dep_manifest.json: data_patches_forbidden has ${forbidden_count} entries (≥3 required)"
else
  _fail "dep_manifest.json: data_patches_forbidden has ${forbidden_count} entries (need ≥3)"
fi

echo ""

# ── ADR-TEMPLATE.md checks ───────────────────────────────────────────────────
echo "--- ADR-TEMPLATE.md content ---"

# Check 13: "What NOT to change" section
assert_contains "$ADR_MD" 'What NOT to change' \
  "ADR-TEMPLATE.md: contains 'What NOT to change' section"

# Check 14: "Decision" section
assert_contains "$ADR_MD" 'Decision' \
  "ADR-TEMPLATE.md: contains 'Decision' section"

# Check 15: "Context" section
assert_contains "$ADR_MD" 'Context' \
  "ADR-TEMPLATE.md: contains 'Context' section"

# Check 16: "Consequences" section
assert_contains "$ADR_MD" 'Consequences' \
  "ADR-TEMPLATE.md: contains 'Consequences' section"

echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
echo "========================================"
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "========================================"

if [[ "$FAIL" -gt 0 ]]; then
  echo "ACCEPTANCE TEST FAILED"
  exit 1
fi

echo "ACCEPTANCE TEST PASSED"
exit 0
