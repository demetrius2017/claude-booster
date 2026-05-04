#!/usr/bin/env bash
# Acceptance test for arch_freshness.py (PostToolUse hook)
# Tests observable behavior only — does NOT read or reference the implementation.
#
# The hook warns (stderr, non-blocking) when source files are edited in a session
# but ARCHITECTURE.md has NOT been updated in that same session.
#
# Exit 0 = all scenarios passed
# Exit 1 = one or more scenarios failed
#
# Key invariant: the hook ALWAYS exits 0 (PostToolUse is non-blocking).
# The observable signal is stderr content: presence/absence of a warning
# containing "arch_freshness" or "WARNING".

set -uo pipefail

HOOK_PATH="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/arch_freshness.py"
PASS_COUNT=0
FAIL_COUNT=0
RESULTS=()

# ---------------------------------------------------------------------------
# Setup — temp directories
# ---------------------------------------------------------------------------

TMPDIR_WITH_ARCH="$(mktemp -d)"    # project that HAS ARCHITECTURE.md
TMPDIR_NO_ARCH="$(mktemp -d)"      # project WITHOUT ARCHITECTURE.md
TMPDIR_T="$(mktemp -d)"            # temp storage for transcript JSONL files

trap 'rm -rf "$TMPDIR_WITH_ARCH" "$TMPDIR_NO_ARCH" "$TMPDIR_T"' EXIT

# Create ARCHITECTURE.md in the "with arch" project
touch "$TMPDIR_WITH_ARCH/ARCHITECTURE.md"

# ---------------------------------------------------------------------------
# Transcript builders — produce JSONL files the hook reads
#
# The hook scans for Edit/Write tool_use blocks in assistant messages to
# detect what was edited in the session.
#
# Format per line (compact JSONL — hook parses line-by-line):
#   {"message":{"role":"assistant","content":[{"type":"tool_use","name":"Edit","input":{"file_path":"..."}}]}}
# ---------------------------------------------------------------------------

make_edit_entry() {
    # make_edit_entry <file_path>  →  one JSONL line: Edit tool_use
    local fp="$1"
    jq -cn --arg fp "$fp" \
        '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Edit","input":{"file_path":$fp,"old_string":"x","new_string":"y"}}]}}'
}

make_write_entry() {
    # make_write_entry <file_path>  →  one JSONL line: Write tool_use
    local fp="$1"
    jq -cn --arg fp "$fp" \
        '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Write","input":{"file_path":$fp,"content":"new content"}}]}}'
}

make_bash_entry() {
    # make_bash_entry <command>  →  one JSONL line: Bash tool_use (no edit)
    local cmd="$1"
    jq -cn --arg cmd "$cmd" \
        '{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":$cmd}}]}}'
}

# ---------------------------------------------------------------------------
# Runner
#
# run_scenario <label> <payload_json> <expect_warning: "yes"|"no"> [ENV=val...]
#
# Invokes the hook once via stdin, captures exit code and stderr separately.
# Asserts: exit code is always 0 AND stderr has/lacks warning text.
# ---------------------------------------------------------------------------

run_scenario() {
    local label="$1"
    local payload="$2"
    local expect_warning="$3"    # "yes" or "no"
    shift 3
    local extra_env=("$@")

    local stderr_f
    stderr_f="$(mktemp)"
    local stdout_f
    stdout_f="$(mktemp)"

    local actual_exit=0
    if [[ ${#extra_env[@]} -gt 0 ]]; then
        env "${extra_env[@]}" python3 "$HOOK_PATH" <<< "$payload" \
            >"$stdout_f" 2>"$stderr_f"
        actual_exit=$?
    else
        python3 "$HOOK_PATH" <<< "$payload" \
            >"$stdout_f" 2>"$stderr_f"
        actual_exit=$?
    fi

    local stderr_content
    stderr_content="$(<"$stderr_f")"
    rm -f "$stderr_f" "$stdout_f"

    local ok=true
    local msgs=()

    # Assert 1: exit code must always be 0 (PostToolUse is non-blocking)
    if [[ "$actual_exit" != "0" ]]; then
        ok=false
        msgs+=("exit=$actual_exit (expected 0)")
    fi

    # Assert 2: stderr warning presence/absence
    if [[ "$expect_warning" == "yes" ]]; then
        if ! grep -qi -e "arch_freshness" -e "WARNING" -e "ARCHITECTURE" <<< "$stderr_content"; then
            ok=false
            msgs+=("expected warning in stderr — got none (stderr='${stderr_content:0:120}')")
        fi
    else
        if grep -qi -e "arch_freshness" -e "WARNING" <<< "$stderr_content"; then
            ok=false
            msgs+=("expected NO warning in stderr — got one (stderr='${stderr_content:0:120}')")
        fi
    fi

    if $ok; then
        RESULTS+=("  PASS  [$label]")
        (( PASS_COUNT++ )) || true
    else
        RESULTS+=("  FAIL  [$label] — ${msgs[*]}")
        (( FAIL_COUNT++ )) || true
    fi
}

# ---------------------------------------------------------------------------
# Scenario 1: Edit source file, no ARCHITECTURE.md edit in transcript → warning
#
# The transcript records only a source file edit. ARCHITECTURE.md exists in the
# project but was not touched this session. The hook must emit a staleness warning.
# ---------------------------------------------------------------------------

TRANSCRIPT_1="$TMPDIR_T/t1_src_only.jsonl"
make_edit_entry "$TMPDIR_WITH_ARCH/src/module.py" > "$TRANSCRIPT_1"

run_scenario \
    "1. Edit source file, no ARCHITECTURE.md edit → warning" \
    "$(jq -n \
        --arg fp "$TMPDIR_WITH_ARCH/src/module.py" \
        --arg cwd "$TMPDIR_WITH_ARCH" \
        --arg tp "$TRANSCRIPT_1" \
        --arg sid "s1-$(date +%s%N)" \
        '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid}')" \
    "yes"

# ---------------------------------------------------------------------------
# Scenario 2: Edit source file, ARCHITECTURE.md IS edited in transcript → no warning
#
# Transcript contains both a source edit and an ARCHITECTURE.md edit in the same
# session. The hook must recognise the arch doc was updated and stay silent.
# ---------------------------------------------------------------------------

TRANSCRIPT_2="$TMPDIR_T/t2_src_and_arch.jsonl"
make_edit_entry "$TMPDIR_WITH_ARCH/src/module.py" > "$TRANSCRIPT_2"
make_edit_entry "$TMPDIR_WITH_ARCH/ARCHITECTURE.md" >> "$TRANSCRIPT_2"

run_scenario \
    "2. Edit source + ARCHITECTURE.md also edited → no warning" \
    "$(jq -n \
        --arg fp "$TMPDIR_WITH_ARCH/src/module.py" \
        --arg cwd "$TMPDIR_WITH_ARCH" \
        --arg tp "$TRANSCRIPT_2" \
        --arg sid "s2-$(date +%s%N)" \
        '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid}')" \
    "no"

# ---------------------------------------------------------------------------
# Scenario 3: Edit allowlisted path (*.md) → no warning
#
# Editing a markdown file (e.g. README.md, CHANGELOG.md) is not a source edit —
# the hook only watches non-doc source file edits as triggers.
# ---------------------------------------------------------------------------

TRANSCRIPT_3="$TMPDIR_T/t3_md_only.jsonl"
make_edit_entry "$TMPDIR_WITH_ARCH/README.md" > "$TRANSCRIPT_3"

run_scenario \
    "3. Edit allowlisted *.md file (README.md) → no warning" \
    "$(jq -n \
        --arg fp "$TMPDIR_WITH_ARCH/README.md" \
        --arg cwd "$TMPDIR_WITH_ARCH" \
        --arg tp "$TRANSCRIPT_3" \
        --arg sid "s3-$(date +%s%N)" \
        '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid}')" \
    "no"

# ---------------------------------------------------------------------------
# Scenario 4: Non-Edit tool (Bash) → no warning
#
# The hook fires after every tool. When the triggering tool is Bash, it should
# check nothing and exit silently — only Edit/Write are relevant.
# ---------------------------------------------------------------------------

TRANSCRIPT_4="$TMPDIR_T/t4_bash.jsonl"
make_bash_entry "ls -la" > "$TRANSCRIPT_4"

run_scenario \
    "4. Non-Edit tool (Bash) → no warning" \
    "$(jq -n \
        --arg cmd "ls -la" \
        --arg cwd "$TMPDIR_WITH_ARCH" \
        --arg tp "$TRANSCRIPT_4" \
        --arg sid "s4-$(date +%s%N)" \
        '{"tool_name":"Bash","tool_input":{"command":$cmd},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid}')" \
    "no"

# ---------------------------------------------------------------------------
# Scenario 5: No ARCHITECTURE.md exists in project → no warning (fail-open)
#
# If a project hasn't adopted arch docs yet, the hook must not punish it.
# No ARCHITECTURE.md → nothing to be stale → exit 0, silent.
# ---------------------------------------------------------------------------

TRANSCRIPT_5="$TMPDIR_T/t5_no_arch.jsonl"
make_edit_entry "$TMPDIR_NO_ARCH/src/service.py" > "$TRANSCRIPT_5"

run_scenario \
    "5. No ARCHITECTURE.md in project → no warning (fail-open)" \
    "$(jq -n \
        --arg fp "$TMPDIR_NO_ARCH/src/service.py" \
        --arg cwd "$TMPDIR_NO_ARCH" \
        --arg tp "$TRANSCRIPT_5" \
        --arg sid "s5-$(date +%s%N)" \
        '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid}')" \
    "no"

# ---------------------------------------------------------------------------
# Scenario 6: CLAUDE_BOOSTER_SKIP_ARCH_GATE=1 → no warning
#
# Explicit env bypass must suppress all output, even when the arch doc is stale.
# ---------------------------------------------------------------------------

TRANSCRIPT_6="$TMPDIR_T/t6_env_bypass.jsonl"
make_edit_entry "$TMPDIR_WITH_ARCH/src/core.py" > "$TRANSCRIPT_6"

run_scenario \
    "6. CLAUDE_BOOSTER_SKIP_ARCH_GATE=1 bypass → no warning" \
    "$(jq -n \
        --arg fp "$TMPDIR_WITH_ARCH/src/core.py" \
        --arg cwd "$TMPDIR_WITH_ARCH" \
        --arg tp "$TRANSCRIPT_6" \
        --arg sid "s6-$(date +%s%N)" \
        '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid}')" \
    "no" \
    "CLAUDE_BOOSTER_SKIP_ARCH_GATE=1"

# ---------------------------------------------------------------------------
# Scenario 7: Sub-agent context (agent_id present) → no warning
#
# Sub-agents bypass the gate — the Lead (orchestrator) is responsible for arch
# doc updates, not individual delegates whose scope is narrowly defined.
# ---------------------------------------------------------------------------

TRANSCRIPT_7="$TMPDIR_T/t7_subagent.jsonl"
make_edit_entry "$TMPDIR_WITH_ARCH/src/worker.py" > "$TRANSCRIPT_7"

run_scenario \
    "7. Sub-agent context (agent_id set) → no warning" \
    "$(jq -n \
        --arg fp "$TMPDIR_WITH_ARCH/src/worker.py" \
        --arg cwd "$TMPDIR_WITH_ARCH" \
        --arg tp "$TRANSCRIPT_7" \
        --arg sid "s7-$(date +%s%N)" \
        --arg aid "agent-abc123-subworker" \
        '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid,"agent_id":$aid}')" \
    "no"

# ---------------------------------------------------------------------------
# Scenario 8: Once-per-session — second edit does NOT re-warn
#
# The hook fires at most once per session. If it already warned this session,
# subsequent calls with the same session_id must be silent (developer already knows).
#
# Observable property: invoke the hook twice in the same session (same session_id,
# same transcript with no arch edit). The SECOND call must not warn again.
# Both calls must exit 0.
# ---------------------------------------------------------------------------

TRANSCRIPT_8="$TMPDIR_T/t8_once_per_session.jsonl"
make_edit_entry "$TMPDIR_WITH_ARCH/src/engine.py" > "$TRANSCRIPT_8"

SID_8="session-s8-fixed-$(date +%s%N)"   # fixed session_id shared by both calls

PAYLOAD_8="$(jq -n \
    --arg fp "$TMPDIR_WITH_ARCH/src/engine.py" \
    --arg cwd "$TMPDIR_WITH_ARCH" \
    --arg tp "$TRANSCRIPT_8" \
    --arg sid "$SID_8" \
    '{"tool_name":"Edit","tool_input":{"file_path":$fp,"old_string":"x","new_string":"y"},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid}')"

# First call
STDERR_8A="$(mktemp)"
STDOUT_8A="$(mktemp)"
python3 "$HOOK_PATH" <<< "$PAYLOAD_8" >"$STDOUT_8A" 2>"$STDERR_8A"
EXIT_8A=$?

# Second call — same session_id, same payload (no arch edit in transcript)
STDERR_8B="$(mktemp)"
STDOUT_8B="$(mktemp)"
python3 "$HOOK_PATH" <<< "$PAYLOAD_8" >"$STDOUT_8B" 2>"$STDERR_8B"
EXIT_8B=$?

CONTENT_8B="$(<"$STDERR_8B")"
rm -f "$STDERR_8A" "$STDOUT_8A" "$STDERR_8B" "$STDOUT_8B"

SCENARIO_8_OK=true
SCENARIO_8_MSGS=()

if [[ "$EXIT_8A" != "0" ]]; then
    SCENARIO_8_OK=false
    SCENARIO_8_MSGS+=("first call exit=$EXIT_8A (expected 0)")
fi
if [[ "$EXIT_8B" != "0" ]]; then
    SCENARIO_8_OK=false
    SCENARIO_8_MSGS+=("second call exit=$EXIT_8B (expected 0)")
fi
if grep -qi -e "arch_freshness" -e "WARNING" <<< "$CONTENT_8B"; then
    SCENARIO_8_OK=false
    SCENARIO_8_MSGS+=("second call emitted warning — once-per-session violated (stderr='${CONTENT_8B:0:120}')")
fi

if $SCENARIO_8_OK; then
    RESULTS+=("  PASS  [8. Once-per-session — second call is silent]")
    (( PASS_COUNT++ )) || true
else
    RESULTS+=("  FAIL  [8. Once-per-session] — ${SCENARIO_8_MSGS[*]}")
    (( FAIL_COUNT++ )) || true
fi

# ---------------------------------------------------------------------------
# Scenario 9: Write tool (not Edit) on source file, no arch update → warning
#
# The hook must watch both Edit and Write — Write also modifies source files and
# is equally relevant as a trigger for architecture freshness.
# ---------------------------------------------------------------------------

TRANSCRIPT_9="$TMPDIR_T/t9_write_src.jsonl"
make_write_entry "$TMPDIR_WITH_ARCH/src/new_module.py" > "$TRANSCRIPT_9"

run_scenario \
    "9. Write tool on source file, no arch update → warning" \
    "$(jq -n \
        --arg fp "$TMPDIR_WITH_ARCH/src/new_module.py" \
        --arg cwd "$TMPDIR_WITH_ARCH" \
        --arg tp "$TRANSCRIPT_9" \
        --arg sid "s9-$(date +%s%N)" \
        '{"tool_name":"Write","tool_input":{"file_path":$fp,"content":"new content"},"cwd":$cwd,"transcript_path":$tp,"session_id":$sid}')" \
    "yes"

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "=============================="
echo " arch_freshness.py — Acceptance Test Results"
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
