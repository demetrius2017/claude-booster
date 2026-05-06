#!/usr/bin/env bash
# Acceptance test for memory_mirror.py + memory_post_tool.py (mirroring path).
#
# Artifact Contract:
#   - memory_mirror.py <md-file>: mirrors frontmatter+body into rolling_memory.db
#   - Dedup: running twice creates exactly 1 entry (content_hash UNIQUE index)
#   - Graceful degradation: missing file, missing frontmatter → exit 0, no crash
#   - memory_post_tool.py: MEMORY.md path is excluded from mirroring; valid
#     memory/*.md path does not crash the script.
#
# Cleanup: all test entries carry TEST_SENTINEL in body and are deleted at exit.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MIRROR_SCRIPT="$REPO_ROOT/templates/scripts/memory_mirror.py"
POST_TOOL_SCRIPT="$REPO_ROOT/templates/scripts/memory_post_tool.py"
DB_PATH="$HOME/.claude/rolling_memory.db"
TEST_SENTINEL="MEMORY_MIRROR_TEST_SENTINEL_7f3a9c"

# ── colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}PASS${NC}  $1"; }
fail() { echo -e "${RED}FAIL${NC}  $1"; FAILED=1; }
info() { echo -e "${YELLOW}INFO${NC}  $1"; }

FAILED=0

# ── cleanup ───────────────────────────────────────────────────────────────────
cleanup() {
    # Remove temp files
    [[ -n "${TMPFILE:-}" ]] && rm -f "$TMPFILE"
    [[ -n "${TMPFILE_MALFORMED:-}" ]] && rm -f "$TMPFILE_MALFORMED"
    # Remove test entries from DB (by sentinel in content)
    if [[ -f "$DB_PATH" ]]; then
        sqlite3 "$DB_PATH" \
            "DELETE FROM agent_memory WHERE source='memory_mirror' AND content LIKE '%${TEST_SENTINEL}%';" \
            2>/dev/null || true
    fi
}
trap cleanup EXIT

# ── prerequisite checks ───────────────────────────────────────────────────────
info "Checking prerequisites..."

if [[ ! -f "$MIRROR_SCRIPT" ]]; then
    echo -e "${RED}ARTIFACT MISSING${NC}: $MIRROR_SCRIPT not found — Worker has not delivered yet."
    exit 1
fi

if [[ ! -f "$POST_TOOL_SCRIPT" ]]; then
    echo -e "${RED}ARTIFACT MISSING${NC}: $POST_TOOL_SCRIPT not found — Worker has not delivered yet."
    exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
    echo -e "${RED}ENV ERROR${NC}: rolling_memory.db not found at $DB_PATH"
    exit 1
fi

# ── create temp .md file with valid frontmatter ───────────────────────────────
TMPFILE=$(mktemp /tmp/test_memory_mirror_XXXXXX.md)
cat > "$TMPFILE" <<EOF
---
name: test_mirror_entry
description: Acceptance test entry for memory_mirror
type: feedback
---

This is the body of the test memory entry. ${TEST_SENTINEL}
It should appear in rolling_memory.db with type=feedback and source=memory_mirror.
EOF

info "Test file created at: $TMPFILE"

# ── TEST 1: basic mirror — entry appears in DB ────────────────────────────────
info "TEST 1: basic mirror → entry must appear in DB"

python3 "$MIRROR_SCRIPT" "$TMPFILE"
EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
    fail "TEST 1: memory_mirror.py exited $EXIT_CODE (expected 0)"
    FAILED=1
fi

ROW_COUNT=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM agent_memory WHERE source='memory_mirror' AND content LIKE '%${TEST_SENTINEL}%';" \
    2>/dev/null || echo "0")

if [[ "$ROW_COUNT" -ge 1 ]]; then
    pass "TEST 1: entry found in DB (count=$ROW_COUNT)"
else
    fail "TEST 1: no entry found in DB after running memory_mirror.py"
    fail "       source='memory_mirror', sentinel=${TEST_SENTINEL}"
fi

# ── TEST 2: type mapping — frontmatter type=feedback → DB type=feedback ───────
info "TEST 2: type mapping — frontmatter type=feedback should produce memory_type=feedback"

DB_TYPE=$(sqlite3 "$DB_PATH" \
    "SELECT memory_type FROM agent_memory WHERE source='memory_mirror' AND content LIKE '%${TEST_SENTINEL}%' LIMIT 1;" \
    2>/dev/null || echo "")

if [[ "$DB_TYPE" == "feedback" ]]; then
    pass "TEST 2: memory_type='feedback' correctly mapped"
else
    fail "TEST 2: expected memory_type='feedback', got '${DB_TYPE}'"
fi

# ── TEST 3: source field is 'memory_mirror' ───────────────────────────────────
info "TEST 3: source field must be 'memory_mirror'"

DB_SOURCE=$(sqlite3 "$DB_PATH" \
    "SELECT source FROM agent_memory WHERE content LIKE '%${TEST_SENTINEL}%' LIMIT 1;" \
    2>/dev/null || echo "")

if [[ "$DB_SOURCE" == "memory_mirror" ]]; then
    pass "TEST 3: source='memory_mirror' confirmed"
else
    fail "TEST 3: expected source='memory_mirror', got '${DB_SOURCE}'"
fi

# ── TEST 4: content contains the body ─────────────────────────────────────────
info "TEST 4: DB content must contain the body text"

DB_CONTENT=$(sqlite3 "$DB_PATH" \
    "SELECT content FROM agent_memory WHERE source='memory_mirror' AND content LIKE '%${TEST_SENTINEL}%' LIMIT 1;" \
    2>/dev/null || echo "")

if echo "$DB_CONTENT" | grep -q "$TEST_SENTINEL"; then
    pass "TEST 4: DB content contains expected body text"
else
    fail "TEST 4: DB content does not contain sentinel text"
    fail "       content sample: '${DB_CONTENT:0:200}'"
fi

# ── TEST 5: dedup — running mirror twice produces exactly 1 entry ─────────────
info "TEST 5: dedup — running mirror twice must yield exactly 1 DB entry"

python3 "$MIRROR_SCRIPT" "$TMPFILE"
EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
    fail "TEST 5: second run exited $EXIT_CODE (expected 0)"
fi

DEDUP_COUNT=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM agent_memory WHERE source='memory_mirror' AND content LIKE '%${TEST_SENTINEL}%';" \
    2>/dev/null || echo "0")

if [[ "$DEDUP_COUNT" -eq 1 ]]; then
    pass "TEST 5: dedup confirmed — exactly 1 entry after 2 runs"
else
    fail "TEST 5: expected 1 entry after dedup, found $DEDUP_COUNT"
fi

# ── TEST 6: graceful handling — missing file (no crash, exit 0) ───────────────
info "TEST 6: missing file must not crash (exit 0)"

python3 "$MIRROR_SCRIPT" "/tmp/this_file_does_not_exist_xyz.md"
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    pass "TEST 6: missing file handled gracefully (exit 0)"
else
    fail "TEST 6: memory_mirror.py crashed on missing file (exit $EXIT_CODE)"
fi

# ── TEST 7: graceful handling — file with no frontmatter (no crash, exit 0) ───
info "TEST 7: file with no frontmatter must not crash (exit 0)"

TMPFILE_MALFORMED=$(mktemp /tmp/test_memory_mirror_malformed_XXXXXX.md)
cat > "$TMPFILE_MALFORMED" <<EOF
This is just plain text with no YAML frontmatter.
No dashes. No metadata. ${TEST_SENTINEL}_malformed
EOF

# First, count current entries to verify no new entry is created
BEFORE_COUNT=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM agent_memory WHERE source='memory_mirror' AND content LIKE '%${TEST_SENTINEL}_malformed%';" \
    2>/dev/null || echo "0")

python3 "$MIRROR_SCRIPT" "$TMPFILE_MALFORMED"
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    pass "TEST 7: malformed file handled gracefully (exit 0)"
else
    fail "TEST 7: memory_mirror.py crashed on malformed file (exit $EXIT_CODE)"
fi

AFTER_COUNT=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM agent_memory WHERE source='memory_mirror' AND content LIKE '%${TEST_SENTINEL}_malformed%';" \
    2>/dev/null || echo "0")

# Malformed file (no frontmatter) should not create an entry — this is expected
# graceful behavior. We accept both 0 (skipped) and do not require entry creation.
if [[ "$AFTER_COUNT" -eq "$BEFORE_COUNT" ]]; then
    pass "TEST 7b: no spurious entry created for malformed file"
else
    # If an entry WAS created despite missing frontmatter, that's a soft warning —
    # the Artifact Contract only requires exit 0 / no crash.
    info "TEST 7b: note — malformed file created $AFTER_COUNT entry(ies) (Contract requires no crash, not skip)"
fi

# ── TEST 8: memory_post_tool.py — MEMORY.md exclusion ────────────────────────
info "TEST 8: memory_post_tool.py must not mirror MEMORY.md writes"

BEFORE_COUNT=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM agent_memory WHERE source='memory_mirror';" \
    2>/dev/null || echo "0")

MOCK_JSON=$(cat <<'ENDJSON'
{
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/Users/dmitrijnazarov/.claude/projects/some-project/memory/MEMORY.md",
    "content": "# Index"
  },
  "tool_response": {"output": "File written"},
  "session_id": "test-session-999",
  "cwd": "/Users/dmitrijnazarov/Projects/Claude_Booster"
}
ENDJSON
)

echo "$MOCK_JSON" | python3 "$POST_TOOL_SCRIPT"
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    pass "TEST 8: memory_post_tool.py did not crash on MEMORY.md write (exit 0)"
else
    fail "TEST 8: memory_post_tool.py exited $EXIT_CODE on MEMORY.md write (expected 0)"
fi

AFTER_COUNT=$(sqlite3 "$DB_PATH" \
    "SELECT COUNT(*) FROM agent_memory WHERE source='memory_mirror';" \
    2>/dev/null || echo "0")

if [[ "$AFTER_COUNT" -eq "$BEFORE_COUNT" ]]; then
    pass "TEST 8b: MEMORY.md exclusion confirmed — no new entries in DB"
else
    DIFF=$((AFTER_COUNT - BEFORE_COUNT))
    fail "TEST 8b: MEMORY.md write created $DIFF new DB entry(ies) — expected 0"
    fail "       MEMORY.md should be excluded from mirroring (it is the index, not a memory)"
fi

# ── TEST 9: memory_post_tool.py — valid memory/*.md write does not crash ──────
info "TEST 9: memory_post_tool.py must not crash on valid memory/*.md Write event"

MOCK_JSON_VALID=$(cat <<'ENDJSON'
{
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/Users/dmitrijnazarov/.claude/projects/some-project/memory/feedback_something.md",
    "content": "---\nname: test\ndescription: test\ntype: feedback\n---\ntest content"
  },
  "tool_response": {"output": "File written"},
  "session_id": "test-session-999",
  "cwd": "/Users/dmitrijnazarov/Projects/Claude_Booster"
}
ENDJSON
)

echo "$MOCK_JSON_VALID" | python3 "$POST_TOOL_SCRIPT"
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    pass "TEST 9: memory_post_tool.py exited 0 on valid memory/*.md Write event"
else
    fail "TEST 9: memory_post_tool.py crashed (exit $EXIT_CODE) on valid memory/*.md Write"
fi

# ── TEST 10: memory_post_tool.py — non-Write tool does not crash ──────────────
info "TEST 10: memory_post_tool.py must handle non-Write tool events (exit 0)"

MOCK_JSON_BASH=$(cat <<'ENDJSON'
{
  "tool_name": "Bash",
  "tool_input": {"command": "ls /tmp"},
  "tool_response": {"exit_code": 0, "stdout": "file1\nfile2"},
  "session_id": "test-session-999",
  "cwd": "/Users/dmitrijnazarov/Projects/Claude_Booster"
}
ENDJSON
)

echo "$MOCK_JSON_BASH" | python3 "$POST_TOOL_SCRIPT"
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    pass "TEST 10: non-Write tool handled correctly (exit 0)"
else
    fail "TEST 10: memory_post_tool.py crashed on Bash event (exit $EXIT_CODE)"
fi

# ── summary ───────────────────────────────────────────────────────────────────
echo ""
if [[ $FAILED -eq 0 ]]; then
    echo -e "${GREEN}ALL TESTS PASSED${NC}"
    exit 0
else
    echo -e "${RED}SOME TESTS FAILED${NC} — see FAIL lines above"
    exit 1
fi
