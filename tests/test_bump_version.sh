#!/usr/bin/env bash
# Acceptance test for templates/scripts/bump_version.py
# Creates a temporary git repo with controlled commits and exercises all CLI modes.
# Exit 0 if all assertions pass, exit 1 if any fail.
#
# Requirements: git, python3 (no extra packages — bump_version.py uses stdlib only)

set -euo pipefail

SCRIPT="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/bump_version.py"

PASS=0
FAIL=0

pass() {
    echo "  PASS: $1"
    PASS=$((PASS + 1))
}

fail() {
    echo "  FAIL: $1"
    echo "        Expected: $2"
    echo "        Got:      $3"
    FAIL=$((FAIL + 1))
}

# ── Temp repo setup ──────────────────────────────────────────────────────────

TMPDIR_REPO="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_REPO"' EXIT

# Deterministic git identity for all operations in this session
export GIT_AUTHOR_NAME="test"
export GIT_AUTHOR_EMAIL="test@test.com"
export GIT_COMMITTER_NAME="test"
export GIT_COMMITTER_EMAIL="test@test.com"

# All git and script calls operate inside the temp repo
cd "$TMPDIR_REPO"

git init -q
git config user.name  "test"
git config user.email "test@test.com"

echo "1.0.0" > VERSION
git add VERSION
git commit -q -m "chore: initial commit"
git tag -a v1.0.0 -m "v1.0.0"

# ── Helper ───────────────────────────────────────────────────────────────────

run_script() {
    # Returns output in $SCRIPT_OUT and exit code in $SCRIPT_RC
    SCRIPT_RC=0
    SCRIPT_OUT=$(python3 "$SCRIPT" "$@" 2>&1) || SCRIPT_RC=$?
}

assert_exit_0() {
    local label="$1"
    if [[ "$SCRIPT_RC" -eq 0 ]]; then
        pass "$label — exit 0"
    else
        fail "$label — exit 0" "exit code 0" "exit code $SCRIPT_RC"
    fi
}

assert_output_contains() {
    local label="$1" pattern="$2"
    if echo "$SCRIPT_OUT" | grep -qi "$pattern"; then
        pass "$label — output contains '$pattern'"
    else
        fail "$label — output contains '$pattern'" "'$pattern' in output" "output: $SCRIPT_OUT"
    fi
}

assert_version_file() {
    local label="$1" expected="$2"
    local actual
    actual="$(cat VERSION 2>/dev/null || echo '<missing>')"
    if [[ "$actual" == "$expected" ]]; then
        pass "$label — VERSION = $expected"
    else
        fail "$label — VERSION" "$expected" "$actual"
    fi
}

assert_tag_exists() {
    local label="$1" tag="$2"
    if git tag -l "$tag" | grep -q "^${tag}$"; then
        pass "$label — tag $tag exists"
    else
        fail "$label — tag $tag exists" "tag $tag in git tag -l" "tag not found"
    fi
}

assert_tag_absent() {
    local label="$1" tag="$2"
    if git tag -l "$tag" | grep -q "^${tag}$"; then
        fail "$label — tag $tag absent" "tag $tag NOT present" "tag found"
    else
        pass "$label — tag $tag absent"
    fi
}

# ── Section 1: --show when at tag ────────────────────────────────────────────

echo ""
echo "=== Scenario 1: --show when at tag ==="

run_script --show
assert_exit_0      "S1 --show"
assert_output_contains "S1 --show" "1.0.0"

# ── Section 2: No bumpable commits (docs only) ────────────────────────────────

echo ""
echo "=== Scenario 2: docs-only commit → no bump ==="

echo "readme update" >> README.md
git add README.md
git commit -q -m "docs: update readme"

run_script
assert_exit_0      "S2 no-bump"
# Accept any of the common phrasings a script might use
if echo "$SCRIPT_OUT" | grep -qiE "no version.bumping|no bumpable|nothing to bump|no commits|already at|up.to.date"; then
    pass "S2 output indicates no bump needed"
else
    fail "S2 output indicates no bump needed" \
         "phrase like 'no version-bumping commits'" \
         "output: $SCRIPT_OUT"
fi
assert_version_file "S2 VERSION unchanged" "1.0.0"
assert_tag_absent   "S2 no v1.0.1 tag"    "v1.0.1"

# ── Section 3: fix → patch bump ───────────────────────────────────────────────

echo ""
echo "=== Scenario 3: fix commit → patch bump ==="

echo "fix" >> fix.txt
git add fix.txt
git commit -q -m "fix: resolve null pointer"

run_script
assert_exit_0      "S3 fix→patch"
assert_version_file "S3 VERSION = 1.0.1" "1.0.1"
assert_tag_exists  "S3 v1.0.1 tag"  "v1.0.1"

# ── Section 4: feat → minor bump (resets patch) ───────────────────────────────

echo ""
echo "=== Scenario 4: feat commit → minor bump ==="

echo "feat" >> feat.txt
git add feat.txt
git commit -q -m "feat: add new feature"

run_script
assert_exit_0      "S4 feat→minor"
assert_version_file "S4 VERSION = 1.1.0" "1.1.0"
assert_tag_exists  "S4 v1.1.0 tag"  "v1.1.0"

# ── Section 5: breaking change → major bump ───────────────────────────────────

echo ""
echo "=== Scenario 5: breaking change → major bump ==="

echo "breaking" >> breaking.txt
git add breaking.txt
git commit -q -m "feat!: redesign API"

run_script
assert_exit_0      "S5 major bump"
assert_version_file "S5 VERSION = 2.0.0" "2.0.0"
assert_tag_exists  "S5 v2.0.0 tag"  "v2.0.0"

# ── Section 6: --dry-run does not modify ──────────────────────────────────────

echo ""
echo "=== Scenario 6: --dry-run does not modify ==="

echo "dryrun" >> dryrun.txt
git add dryrun.txt
git commit -q -m "fix: another fix"

run_script --dry-run
assert_exit_0      "S6 --dry-run exit 0"
assert_version_file "S6 VERSION unchanged at 2.0.0" "2.0.0"
assert_tag_absent  "S6 no v2.0.1 tag"  "v2.0.1"

# ── Section 7: --set explicit version ─────────────────────────────────────────

echo ""
echo "=== Scenario 7: --set explicit version ==="

run_script --set 3.5.0
assert_exit_0      "S7 --set exit 0"
assert_version_file "S7 VERSION = 3.5.0" "3.5.0"
assert_tag_exists  "S7 v3.5.0 tag"  "v3.5.0"

# ── Section 8: --bump forces type regardless of commit type ───────────────────

echo ""
echo "=== Scenario 8: --bump forces patch on docs-only commit ==="

echo "just docs" >> docs2.md
git add docs2.md
git commit -q -m "docs: just docs"

run_script --bump patch
assert_exit_0      "S8 --bump patch exit 0"
assert_version_file "S8 VERSION = 3.5.1" "3.5.1"
assert_tag_exists  "S8 v3.5.1 tag"  "v3.5.1"

# ── Section 9: already at tag (no new commits) ────────────────────────────────

echo ""
echo "=== Scenario 9: already at tag → exit 0, no change ==="

run_script
assert_exit_0      "S9 no-op"
# Accept any phrasing indicating "nothing to do"
if echo "$SCRIPT_OUT" | grep -qiE "already|no new commits|no version.bumping|no bumpable|nothing to bump|up.to.date"; then
    pass "S9 output indicates already at tag"
else
    fail "S9 output indicates already at tag" \
         "phrase like 'already' or 'no new commits'" \
         "output: $SCRIPT_OUT"
fi
assert_version_file "S9 VERSION still 3.5.1" "3.5.1"

# ── Summary ──────────────────────────────────────────────────────────────────

TOTAL=$((PASS + FAIL))
echo ""
echo "================================================"
echo "  $PASS/$TOTAL assertions passed"
echo "================================================"

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi

exit 0
