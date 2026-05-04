#!/usr/bin/env bash
# Acceptance test for dep_guard.py (PreToolUse hook)
# Tests observable behavior only — does NOT read or reference the implementation.
#
# The hook blocks Edit/Write on files marked critical in dep_manifest.json
# unless the transcript contains dependency review evidence.
#
# Exit 0 = all scenarios passed
# Exit 1 = one or more scenarios failed

set -uo pipefail

HOOK_PATH="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/dep_guard.py"
PASS_COUNT=0
FAIL_COUNT=0
RESULTS=()

# ---------------------------------------------------------------------------
# Setup — temp directory tree
# ---------------------------------------------------------------------------

TMPDIR_TEST="$(mktemp -d)"
NO_MANIFEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TEST" "$NO_MANIFEST_DIR"' EXIT

# Create .claude/dep_manifest.json with two components:
#   - "engine" → critical, owns engine/core.py
#   - "docs"   → not critical, owns docs/overview.md
mkdir -p "$TMPDIR_TEST/.claude"

jq -n '{
  components: {
    engine: {
      file: "engine/core.py",
      reads_from: ["db:positions"],
      writes_to: ["db:nav_snapshots"],
      called_by: ["scheduler"],
      critical: true,
      notes: "Core trading engine — all changes require dep review"
    },
    docs: {
      file: "docs/overview.md",
      reads_from: [],
      writes_to: [],
      called_by: [],
      critical: false,
      notes: "User documentation — no dep review needed"
    }
  }
}' > "$TMPDIR_TEST/.claude/dep_manifest.json"

# ---------------------------------------------------------------------------
# Transcript helpers — produce JSONL files the hook reads
# ---------------------------------------------------------------------------

make_transcript() {
    # make_transcript <path> <content>
    # Each line in JSONL: {"message":{"role":"assistant","content":[{"type":"text","text":"..."}]}}
    # NOTE: -c (compact) is mandatory — the hook parses line-by-line (real JSONL format).
    # Pretty-printed multi-line JSON breaks the hook's line-by-line scanner.
    local path="$1"
    local text="$2"
    jq -cn --arg t "$text" \
        '{"message":{"role":"assistant","content":[{"type":"text","text":$t}]}}' > "$path"
}

# ---------------------------------------------------------------------------
# Payload builders (all JSON via jq — no quoting hacks)
# ---------------------------------------------------------------------------

# Edit payload for a given file path, with optional transcript_path
edit_payload() {
    local file_path="$1"
    local transcript_path="${2:-}"
    if [[ -n "$transcript_path" ]]; then
        jq -n \
            --arg fp "$file_path" \
            --arg cwd "$TMPDIR_TEST" \
            --arg tp "$transcript_path" \
            '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"transcript_path":$tp}'
    else
        jq -n \
            --arg fp "$file_path" \
            --arg cwd "$TMPDIR_TEST" \
            '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd}'
    fi
}

# Write payload for a given file path, with optional transcript_path
write_payload() {
    local file_path="$1"
    local transcript_path="${2:-}"
    if [[ -n "$transcript_path" ]]; then
        jq -n \
            --arg fp "$file_path" \
            --arg cwd "$TMPDIR_TEST" \
            --arg tp "$transcript_path" \
            '{"tool_name":"Write","tool_input":{"file_path":$fp,"content":"new content"},"cwd":$cwd,"transcript_path":$tp}'
    else
        jq -n \
            --arg fp "$file_path" \
            --arg cwd "$TMPDIR_TEST" \
            '{"tool_name":"Write","tool_input":{"file_path":$fp,"content":"new content"},"cwd":$cwd}'
    fi
}

# Bash payload (non-Edit/Write tool)
bash_payload() {
    local cmd="$1"
    jq -n --arg cmd "$cmd" --arg cwd "$TMPDIR_TEST" \
        '{"tool_name":"Bash","tool_input":{"command":$cmd},"cwd":$cwd}'
}

# Edit payload with agent_id (sub-agent context bypass)
agent_edit_payload() {
    local file_path="$1"
    jq -n \
        --arg fp "$file_path" \
        --arg cwd "$TMPDIR_TEST" \
        --arg aid "agent-abc123-subworker" \
        '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"agent_id":$aid}'
}

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

run_hook() {
    local label="$1"
    local expected_exit="$2"
    local payload="$3"
    shift 3
    local extra_env=("$@")

    local actual_exit
    if [[ ${#extra_env[@]} -gt 0 ]]; then
        actual_exit=$(cd "$TMPDIR_TEST" && \
            env "${extra_env[@]}" python3 "$HOOK_PATH" <<< "$payload" \
            >/dev/null 2>&1; echo $?)
    else
        actual_exit=$(cd "$TMPDIR_TEST" && \
            python3 "$HOOK_PATH" <<< "$payload" \
            >/dev/null 2>&1; echo $?)
    fi

    if [[ "$actual_exit" -eq "$expected_exit" ]]; then
        RESULTS+=("  PASS  [$label] — expected exit $expected_exit, got $actual_exit")
        (( PASS_COUNT++ )) || true
    else
        RESULTS+=("  FAIL  [$label] — expected exit $expected_exit, got $actual_exit")
        (( FAIL_COUNT++ )) || true
    fi
}

# ---------------------------------------------------------------------------
# Scenario 1: Edit on critical file WITHOUT review evidence → exit 2 (block)
# ---------------------------------------------------------------------------
run_hook \
    "1. Edit critical file, no evidence" \
    2 \
    "$(edit_payload "engine/core.py")"

# ---------------------------------------------------------------------------
# Scenario 2: Edit on critical file WITH "dep_manifest" in transcript → exit 0
# ---------------------------------------------------------------------------
TRANSCRIPT_2="$TMPDIR_TEST/transcript_dep_manifest.jsonl"
make_transcript "$TRANSCRIPT_2" "Reviewed dep_manifest for affected components before proceeding."

run_hook \
    "2. Edit critical file, 'dep_manifest' in transcript" \
    0 \
    "$(edit_payload "engine/core.py" "$TRANSCRIPT_2")"

# ---------------------------------------------------------------------------
# Scenario 3: Edit on critical file WITH "[dep-reviewed]" marker → exit 0
# ---------------------------------------------------------------------------
TRANSCRIPT_3="$TMPDIR_TEST/transcript_dep_reviewed.jsonl"
make_transcript "$TRANSCRIPT_3" "[dep-reviewed] All downstream callers checked, no breaking changes."

run_hook \
    "3. Edit critical file, '[dep-reviewed]' marker in transcript" \
    0 \
    "$(edit_payload "engine/core.py" "$TRANSCRIPT_3")"

# ---------------------------------------------------------------------------
# Scenario 4a: Edit on critical file WITH "downstream" in transcript → exit 0
# ---------------------------------------------------------------------------
TRANSCRIPT_4A="$TMPDIR_TEST/transcript_downstream.jsonl"
make_transcript "$TRANSCRIPT_4A" "Checked downstream consumers — the interface is stable, no breakage expected."

run_hook \
    "4a. Edit critical file, 'downstream' in transcript" \
    0 \
    "$(edit_payload "engine/core.py" "$TRANSCRIPT_4A")"

# ---------------------------------------------------------------------------
# Scenario 4b: Edit on critical file WITH "affected" in transcript → exit 0
# ---------------------------------------------------------------------------
TRANSCRIPT_4B="$TMPDIR_TEST/transcript_affected.jsonl"
make_transcript "$TRANSCRIPT_4B" "Identified all affected modules: none depend on this code path."

run_hook \
    "4b. Edit critical file, 'affected' in transcript" \
    0 \
    "$(edit_payload "engine/core.py" "$TRANSCRIPT_4B")"

# ---------------------------------------------------------------------------
# Scenario 5: Edit on NON-critical file (docs component) → exit 0
# ---------------------------------------------------------------------------
run_hook \
    "5. Edit non-critical file (docs/overview.md)" \
    0 \
    "$(edit_payload "docs/overview.md")"

# ---------------------------------------------------------------------------
# Scenario 6: Edit on file NOT in manifest at all → exit 0
# ---------------------------------------------------------------------------
run_hook \
    "6. Edit file not in manifest (utils/helpers.py)" \
    0 \
    "$(edit_payload "utils/helpers.py")"

# ---------------------------------------------------------------------------
# Scenario 7: Non-Edit tool (Bash) on critical file path → exit 0 (ignored)
# ---------------------------------------------------------------------------
run_hook \
    "7. Bash tool on critical path — ignored" \
    0 \
    "$(bash_payload "cat engine/core.py")"

# ---------------------------------------------------------------------------
# Scenario 8: No manifest exists → exit 0 (fail-open)
# ---------------------------------------------------------------------------
no_manifest_payload=$(jq -n \
    --arg cwd "$NO_MANIFEST_DIR" \
    '{"tool_name":"Edit","tool_input":{"file_path":"engine/core.py","old_string":"x","new_string":"y"},"cwd":$cwd}')

no_manifest_exit=$(cd "$NO_MANIFEST_DIR" && \
    python3 "$HOOK_PATH" <<< "$no_manifest_payload" \
    >/dev/null 2>&1; echo $?)

if [[ "$no_manifest_exit" -eq 0 ]]; then
    RESULTS+=("  PASS  [8. No manifest exists — fail-open] — expected exit 0, got $no_manifest_exit")
    (( PASS_COUNT++ )) || true
else
    RESULTS+=("  FAIL  [8. No manifest exists — fail-open] — expected exit 0, got $no_manifest_exit")
    (( FAIL_COUNT++ )) || true
fi

# ---------------------------------------------------------------------------
# Scenario 9: CLAUDE_BOOSTER_SKIP_DEP_GUARD=1 bypass → exit 0
# ---------------------------------------------------------------------------
run_hook \
    "9. CLAUDE_BOOSTER_SKIP_DEP_GUARD=1 bypass on critical file" \
    0 \
    "$(edit_payload "engine/core.py")" \
    "CLAUDE_BOOSTER_SKIP_DEP_GUARD=1"

# ---------------------------------------------------------------------------
# Scenario 10: Allowlisted path (*.md) → exit 0 (bypass via glob)
# ---------------------------------------------------------------------------
run_hook \
    "10. Allowlisted *.md path (README.md)" \
    0 \
    "$(edit_payload "README.md")"

# ---------------------------------------------------------------------------
# Scenario 11: Sub-agent context (agent_id present) → exit 0 (bypass)
# ---------------------------------------------------------------------------
agent_exit=$(cd "$TMPDIR_TEST" && \
    python3 "$HOOK_PATH" <<< "$(agent_edit_payload "engine/core.py")" \
    >/dev/null 2>&1; echo $?)

if [[ "$agent_exit" -eq 0 ]]; then
    RESULTS+=("  PASS  [11. Sub-agent context bypass (agent_id)] — expected exit 0, got $agent_exit")
    (( PASS_COUNT++ )) || true
else
    RESULTS+=("  FAIL  [11. Sub-agent context bypass (agent_id)] — expected exit 0, got $agent_exit")
    (( FAIL_COUNT++ )) || true
fi

# ---------------------------------------------------------------------------
# Bonus: Write (not Edit) on critical file without evidence → exit 2
# ---------------------------------------------------------------------------
# Use engine/core.py — same critical file as other scenarios. engine/config.py
# is NOT in the manifest so the hook would fail-open (exit 0) for that path.
run_hook \
    "bonus. Write on critical file, no evidence" \
    2 \
    "$(write_payload "engine/core.py")"

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "=============================="
echo " dep_guard.py — Acceptance Test Results"
echo "=============================="
for r in "${RESULTS[@]}"; do
    echo "$r"
done
echo "------------------------------"
echo " Total: $((PASS_COUNT + FAIL_COUNT))  PASS: $PASS_COUNT  FAIL: $FAIL_COUNT"
echo "=============================="
echo ""

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "OVERALL: FAIL"
    exit 1
else
    echo "OVERALL: PASS"
    exit 0
fi
