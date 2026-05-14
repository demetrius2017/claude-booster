#!/usr/bin/env bash
# Acceptance test for _infer_category() in model_tag_enforcer.py
#
# Tests observable behavior of the function via inline Python imports.
# All assertions compare actual return value to expected string.
#
# Groups:
#   1 — Explicit [category] tags override keyword inference
#   2 — New coding keywords (no explicit tag)
#   3 — Existing behavior preserved (regression tests)
#   4 — Tag priority over keyword
#   5 — high_blast_radius keyword in text (no tag)
#
# Exit 0 = all assertions passed
# Exit 1 = one or more assertions failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_FILE="$REPO_ROOT/templates/scripts/model_tag_enforcer.py"

# ---------------------------------------------------------------------------
# Guard — source file must exist
# ---------------------------------------------------------------------------

if [[ ! -f "$SOURCE_FILE" ]]; then
    echo "FATAL: source file not found: $SOURCE_FILE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Counters and results
# ---------------------------------------------------------------------------

PASS_COUNT=0
FAIL_COUNT=0
FAILURES=()

# ---------------------------------------------------------------------------
# Helper — assert one call
#
# Usage: assert_category <description> <subagent_type> <expected> <label>
#
# Calls _infer_category(description, subagent_type) via Python and compares
# the return value to expected.  Prints [PASS] or [FAIL] with diagnostics.
# ---------------------------------------------------------------------------

assert_category() {
    local desc="$1"
    local subagent_type="$2"
    local expected="$3"
    local label="$4"

    local actual
    actual="$(python3 - "$desc" "$subagent_type" <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'templates', 'scripts'))

# Resolve the templates/scripts path relative to repo root (passed via env)
repo_root = os.environ.get("REPO_ROOT", "")
sys.path.insert(0, os.path.join(repo_root, "templates", "scripts"))

from model_tag_enforcer import _infer_category

description = sys.argv[1]
subagent_type = sys.argv[2]
result = _infer_category(description, subagent_type)
print(result)
PYEOF
    )" 2>&1

    local py_exit=$?

    if [[ $py_exit -ne 0 ]]; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
        local msg="[FAIL] $label"$'\n'"       Python error: $actual"
        FAILURES+=("$msg")
        echo "$msg"
        return
    fi

    if [[ "$actual" == "$expected" ]]; then
        PASS_COUNT=$((PASS_COUNT + 1))
        echo "[PASS] $label"
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        local msg="[FAIL] $label"$'\n'"       expected: '$expected'"$'\n'"       actual:   '$actual'"
        FAILURES+=("$msg")
        echo "$msg"
    fi
}

# Export REPO_ROOT so the heredoc Python can read it
export REPO_ROOT

# ===========================================================================
# Group 1 — Explicit category tags override keyword inference
# ===========================================================================

echo ""
echo "=== Group 1: Explicit [category] tags override keyword inference ==="

assert_category "[high_blast_radius] Do auth work"  "" "high_blast_radius" \
    "[high_blast_radius] tag → high_blast_radius"

assert_category "[coding] Apply edits"              "" "coding" \
    "[coding] tag → coding (overrides 'apply' keyword)"

assert_category "[medium] Some worker task"         "" "medium" \
    "[medium] tag → medium (overrides 'worker' keyword)"

assert_category "[TRIVIAL] Quick lookup"            "" "trivial" \
    "[TRIVIAL] tag case-insensitive → trivial"

assert_category "[hard] Design architecture"        "" "hard" \
    "[hard] tag → hard"

assert_category "[consilium_bio] Debate options"    "" "consilium_bio" \
    "[consilium_bio] tag → consilium_bio"

assert_category "[audit_external] Review code"      "" "audit_external" \
    "[audit_external] tag → audit_external"

# ===========================================================================
# Group 2 — New coding keywords (no explicit tag)
# ===========================================================================

echo ""
echo "=== Group 2: New coding keywords (no explicit tag) ==="

assert_category "Apply order marker overlay to 5 files" "" "coding" \
    "'apply' in description → coding"

assert_category "Edit frontend component"               "" "coding" \
    "'edit' in description → coding"

assert_category "Update the API handler"                "" "coding" \
    "'update' in description → coding"

assert_category "Add new validation logic"              "" "coding" \
    "'add' in description → coding"

assert_category "Modify the config parser"              "" "coding" \
    "'modify' in description → coding"

assert_category "Change error handling in service"      "" "coding" \
    "'change' in description → coding"

# ===========================================================================
# Group 3 — Existing behavior preserved (regression tests)
# ===========================================================================

echo ""
echo "=== Group 3: Existing behavior preserved (regression) ==="

assert_category "Worker: implement feature"  "" "coding" \
    "'worker' keyword → coding"

assert_category "Verifier: test acceptance" "" "coding" \
    "'verifier' keyword → coding"

assert_category "Explore codebase"          "Explore" "recon" \
    "subagent_type=Explore → recon"

assert_category "Research topic X"          "" "medium" \
    "'research' keyword → medium"

assert_category "Review code quality"       "" "medium" \
    "'review' keyword → medium"

assert_category "consilium on architecture" "" "consilium_bio" \
    "'consilium' in desc → consilium_bio (beats 'architecture')"

assert_category "grep for patterns"         "" "trivial" \
    "'grep' in desc → trivial"

# NOTE: "Plan the migration" cannot return "hard" because "migration" is in
# _HIGH_BLAST_KEYWORDS and that check runs BEFORE the plan/architecture check.
# Using subagent_type="Plan" with a non-blast description to test the "hard" path.
assert_category "Plan the rollout"          "Plan" "hard" \
    "subagent_type=Plan (no blast kws) → hard"

assert_category "No matching keywords here" "" "medium" \
    "no keywords → default medium"

# ===========================================================================
# Group 4 — Tag priority over keyword
# ===========================================================================

echo ""
echo "=== Group 4: Tag priority over keyword ==="

assert_category "[medium] Worker: fix the bug" "" "medium" \
    "[medium] tag wins over 'worker'+'fix' keywords"

assert_category "[trivial] Apply simple edit"  "" "trivial" \
    "[trivial] tag wins over 'apply' keyword"

assert_category "[recon] Update search index"  "" "recon" \
    "[recon] tag wins over 'update' keyword"

# ===========================================================================
# Group 5 — high_blast_radius keyword in text (no tag)
# ===========================================================================

echo ""
echo "=== Group 5: high_blast_radius keyword in text (no tag) ==="

assert_category "high_blast_radius deploy to prod" "" "high_blast_radius" \
    "'high_blast_radius' literal in desc → high_blast_radius"

# ===========================================================================
# Summary
# ===========================================================================

echo ""
echo "========================================"
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed"
echo "========================================"

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo ""
    echo "--- Failure details ---"
    for f in "${FAILURES[@]}"; do
        echo "$f"
    done
    exit 1
fi

exit 0
