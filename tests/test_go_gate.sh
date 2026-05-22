#!/usr/bin/env bash
# Acceptance test: go_gate.py — all 12 decision paths.
#
# Decision tree under test:
#  P1  sub-agent context (agent_id/agent_type set)    → allow (DECISION_AUTO_SKIP)
#  P2  CLAUDE_BOOSTER_SKIP_GO_GATE=1                  → allow
#  P3  tool_name != 'Agent'                            → allow
#  P4  no project root (cwd resolves to nothing)       → allow (fail-open)
#  P5  subagent_type in {Explore, Plan}                → allow
#  P6  description prefix "Explore:"/"Plan:"           → allow (intent detection)
#  P7  phase != IMPLEMENT                              → allow
#  P8  .go_active marker present                       → allow
#  P9  no coding keywords in description+prompt        → allow
#  P10 recon-intent keyword in description overrides   → allow
#  P11 model=haiku                                     → allow (recon tier)
#  P12 block — IMPLEMENT + no marker + coding keywords + no recon intent + not haiku

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/templates/scripts"
GATE_PY="$SCRIPTS_DIR/go_gate.py"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "FAIL: $1"; FAIL=$((FAIL + 1)); }

# ---------------------------------------------------------------------------
# Global temp dir — cleaned up on exit
# ---------------------------------------------------------------------------
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

CLAUDE_HOME_DIR="$WORK_DIR/claude_home"
mkdir -p "$CLAUDE_HOME_DIR/logs"

# ---------------------------------------------------------------------------
# setup_project <name> [phase]
#   Creates a temp project directory with .claude/ structure.
#   Echoes the project path.
# ---------------------------------------------------------------------------
setup_project() {
    local name="$1"
    local phase="${2:-IMPLEMENT}"
    local dir="$WORK_DIR/$name"
    mkdir -p "$dir/.claude"
    echo "$phase" > "$dir/.claude/.phase"
    echo "$dir"
}

# ---------------------------------------------------------------------------
# run_gate <json> [extra_env_pairs...]
#   Pipes json to go_gate.py and returns the exit code.
#   Always isolates logs in CLAUDE_HOME_DIR.
#   Extra env vars must be of the form KEY=VALUE (no spaces inside).
# ---------------------------------------------------------------------------
run_gate() {
    local json="$1"
    local extra_env=""
    if [[ $# -ge 2 ]]; then
        extra_env="$2"
    fi

    set +e
    if [[ -n "$extra_env" ]]; then
        env CLAUDE_HOME="$CLAUDE_HOME_DIR" \
            CLAUDE_BOOSTER_SKIP_GO_GATE="" \
            $extra_env \
            python3 "$GATE_PY" <<< "$json" 2>/dev/null
    else
        env CLAUDE_HOME="$CLAUDE_HOME_DIR" \
            CLAUDE_BOOSTER_SKIP_GO_GATE="" \
            python3 "$GATE_PY" <<< "$json" 2>/dev/null
    fi
    local ec=$?
    set -e
    echo $ec
}

# ---------------------------------------------------------------------------
# Baseline payload helpers — produce clean JSON strings.
#   All default to IMPLEMENT phase; override by pointing cwd at another project.
# ---------------------------------------------------------------------------

# Build a JSON payload for an Agent spawn.
# Usage: agent_json <cwd> <description> [subagent_type] [model] [prompt]
agent_json() {
    local cwd="$1"
    local desc="$2"
    local subtype="${3:-}"
    local model="${4:-sonnet}"
    local prompt="${5:-}"

    # Build tool_input fields conditionally
    local tool_input
    tool_input=$(python3 -c "
import json, sys
ti = {
    'description': sys.argv[1],
    'model': sys.argv[2],
}
if sys.argv[3]:
    ti['subagent_type'] = sys.argv[3]
if sys.argv[4]:
    ti['prompt'] = sys.argv[4]
print(json.dumps(ti))
" "$desc" "$model" "$subtype" "$prompt")

    python3 -c "
import json, sys
payload = {
    'tool_name': 'Agent',
    'tool_input': json.loads(sys.argv[1]),
    'cwd': sys.argv[2],
    'session_id': 'test-session',
}
print(json.dumps(payload))
" "$tool_input" "$cwd"
}

# Build a non-Agent tool payload.
non_agent_json() {
    local cwd="$1"
    local tool="${2:-Bash}"
    python3 -c "
import json, sys
payload = {
    'tool_name': sys.argv[2],
    'tool_input': {'command': 'ls'},
    'cwd': sys.argv[1],
    'session_id': 'test-session',
}
print(json.dumps(payload))
" "$cwd" "$tool"
}

# ---------------------------------------------------------------------------
# ─── PATH 1: Sub-agent context → allow (DECISION_AUTO_SKIP) ─────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 1: Sub-agent context → auto-skip ==="

PROJ_P1=$(setup_project "p1")

# agent_id set
JSON=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {'description': 'Worker: fix bug', 'model': 'sonnet'},
    'cwd': '$PROJ_P1',
    'session_id': 'test',
    'agent_id': 'abc123',
}))
")
EC=$(run_gate "$JSON")
if [[ "$EC" == "0" ]]; then
    pass "P1a: agent_id set → exit 0 (auto-skip)"
else
    fail "P1a: agent_id set → expected exit 0, got $EC"
fi

# agent_type set
JSON=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {'description': 'Worker: implement feature', 'model': 'opus'},
    'cwd': '$PROJ_P1',
    'session_id': 'test',
    'agent_type': 'general-purpose',
}))
")
EC=$(run_gate "$JSON")
if [[ "$EC" == "0" ]]; then
    pass "P1b: agent_type set → exit 0 (auto-skip)"
else
    fail "P1b: agent_type set → expected exit 0, got $EC"
fi

# ---------------------------------------------------------------------------
# ─── PATH 2: CLAUDE_BOOSTER_SKIP_GO_GATE=1 → allow ──────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 2: CLAUDE_BOOSTER_SKIP_GO_GATE=1 → allow ==="

PROJ_P2=$(setup_project "p2")
JSON=$(agent_json "$PROJ_P2" "Worker: implement the whole feature" "" "opus")

EC=$(run_gate "$JSON" "CLAUDE_BOOSTER_SKIP_GO_GATE=1")
if [[ "$EC" == "0" ]]; then
    pass "P2: CLAUDE_BOOSTER_SKIP_GO_GATE=1 → exit 0"
else
    fail "P2: CLAUDE_BOOSTER_SKIP_GO_GATE=1 → expected exit 0, got $EC"
fi

# Confirm that without the env var the same payload would block (establishes baseline)
EC_NO_SKIP=$(run_gate "$JSON")
if [[ "$EC_NO_SKIP" == "2" ]]; then
    pass "P2-baseline: without skip env var, same payload blocks (exit 2)"
else
    fail "P2-baseline: without skip env var, expected exit 2, got $EC_NO_SKIP"
fi

# ---------------------------------------------------------------------------
# ─── PATH 3: tool_name != 'Agent' → allow ────────────────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 3: Non-Agent tool → allow ==="

PROJ_P3=$(setup_project "p3")

for tool in Bash Read Edit Write Glob Grep; do
    JSON=$(non_agent_json "$PROJ_P3" "$tool")
    EC=$(run_gate "$JSON")
    if [[ "$EC" == "0" ]]; then
        pass "P3-$tool: tool=$tool → exit 0"
    else
        fail "P3-$tool: tool=$tool → expected exit 0, got $EC"
    fi
done

# ---------------------------------------------------------------------------
# ─── PATH 4: No project root → allow (fail-open) ─────────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 4: No project root → fail-open allow ==="

# Use a cwd that has no .git or .claude ancestor
NO_PROJECT_DIR="$WORK_DIR/no_project_subdir/deep/nested"
mkdir -p "$NO_PROJECT_DIR"

JSON=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {'description': 'Worker: implement feature', 'model': 'sonnet'},
    'cwd': '$NO_PROJECT_DIR',
    'session_id': 'test',
}))
")
EC=$(run_gate "$JSON")
if [[ "$EC" == "0" ]]; then
    pass "P4: cwd with no .git/.claude ancestor → exit 0 (fail-open)"
else
    fail "P4: cwd with no .git/.claude ancestor → expected exit 0, got $EC"
fi

# Also test with a non-existent path as cwd
# Note: empty cwd falls back to os.getcwd() which may resolve to a real project root.
# A genuinely non-existent path is safer for testing the fail-open path.
JSON=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {'description': 'Worker: fix bug', 'model': 'sonnet'},
    'cwd': '/tmp/definitely-does-not-exist-xyz-12345/nested/path',
    'session_id': 'test',
}))
")
EC=$(run_gate "$JSON")
if [[ "$EC" == "0" ]]; then
    pass "P4b: non-existent cwd path → exit 0 (fail-open, no project root)"
else
    fail "P4b: non-existent cwd path → expected exit 0, got $EC"
fi

# ---------------------------------------------------------------------------
# ─── PATH 5: subagent_type in {Explore, Plan} → allow ────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 5: subagent_type Explore/Plan → allow ==="

PROJ_P5=$(setup_project "p5")

for subtype in Explore Plan; do
    JSON=$(agent_json "$PROJ_P5" "Worker: implement feature with many changes" "$subtype" "opus")
    EC=$(run_gate "$JSON")
    if [[ "$EC" == "0" ]]; then
        pass "P5-$subtype: subagent_type=$subtype → exit 0"
    else
        fail "P5-$subtype: subagent_type=$subtype → expected exit 0, got $EC"
    fi
done

# Verify that a coding subagent_type (general-purpose) is NOT exempt via P5
JSON=$(agent_json "$PROJ_P5" "Worker: implement feature" "general-purpose" "sonnet")
EC=$(run_gate "$JSON")
if [[ "$EC" == "2" ]]; then
    pass "P5-negative: subagent_type=general-purpose not exempt (exit 2)"
else
    fail "P5-negative: subagent_type=general-purpose expected exit 2, got $EC"
fi

# ---------------------------------------------------------------------------
# ─── PATH 6: Description prefix "Explore:" or "Plan:" → allow ───────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 6: Description prefix Explore:/Plan: → allow ==="

PROJ_P6=$(setup_project "p6")

declare -a PREFIX_TESTS=(
    "Explore: find the relevant files"
    "Plan: design the new architecture"
    "explore: search codebase for patterns"
    "EXPLORE:check what exists"
    "plan investigate approach"
)
declare -a PREFIX_EXPECTS=(0 0 0 0 0)

i=0
for desc in "${PREFIX_TESTS[@]}"; do
    expected="${PREFIX_EXPECTS[$i]}"
    JSON=$(agent_json "$PROJ_P6" "$desc" "" "sonnet")
    EC=$(run_gate "$JSON")
    if [[ "$EC" == "$expected" ]]; then
        pass "P6[$i]: desc='${desc:0:30}...' → exit $expected"
    else
        fail "P6[$i]: desc='${desc:0:30}...' → expected exit $expected, got $EC"
    fi
    i=$((i+1))
done

# Gerund forms (Exploring, Planning) should NOT be exempt via P6
for desc in "Exploring the codebase to implement new feature" "Planning to implement changes"; do
    JSON=$(agent_json "$PROJ_P6" "$desc" "" "sonnet")
    EC=$(run_gate "$JSON")
    # "Exploring" has "implement" keyword → should block; "Planning" has "implement" → block
    if [[ "$EC" == "2" ]]; then
        pass "P6-gerund: '$desc' → NOT exempt via prefix (exit 2)"
    else
        fail "P6-gerund: '$desc' → expected exit 2 (gerund not exempt), got $EC"
    fi
done

# ---------------------------------------------------------------------------
# ─── PATH 7: Phase != IMPLEMENT → allow ──────────────────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 7: Phase != IMPLEMENT → allow ==="

for phase in RECON PLAN AUDIT VERIFY MERGE ""; do
    proj_name="p7_$(echo "${phase:-none}" | tr '[:upper:]' '[:lower:]')"
    PROJ=$(setup_project "$proj_name" "$phase")
    # For empty phase, remove the file to simulate missing
    if [[ -z "$phase" ]]; then
        rm -f "$PROJ/.claude/.phase"
    fi
    JSON=$(agent_json "$PROJ" "Worker: implement feature" "" "sonnet")
    EC=$(run_gate "$JSON")
    if [[ "$EC" == "0" ]]; then
        pass "P7-${phase:-no_file}: phase='$phase' → exit 0 (gate inactive)"
    else
        fail "P7-${phase:-no_file}: phase='$phase' → expected exit 0, got $EC"
    fi
done

# Lowercase "implement" as phase → gate inactive (not == "IMPLEMENT" after strip+upper)
# Actually "implement".upper() == "IMPLEMENT" so it WOULD enforce. Test uppercase variant.
PROJ_IMPL_LC=$(setup_project "p7_impl_lc" "implement")
JSON=$(agent_json "$PROJ_IMPL_LC" "Worker: implement feature" "" "sonnet")
EC=$(run_gate "$JSON")
if [[ "$EC" == "2" ]]; then
    pass "P7-lowercase_implement: 'implement' phase uppercases to IMPLEMENT → gate active (exit 2)"
else
    fail "P7-lowercase_implement: 'implement' phase expected exit 2, got $EC"
fi

# ---------------------------------------------------------------------------
# ─── PATH 8: .go_active marker present → allow ───────────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 8: .go_active marker present → allow ==="

PROJ_P8=$(setup_project "p8")
touch "$PROJ_P8/.claude/.go_active"

JSON=$(agent_json "$PROJ_P8" "Worker: implement the feature" "" "sonnet")
EC=$(run_gate "$JSON")
if [[ "$EC" == "0" ]]; then
    pass "P8: .go_active marker present → exit 0"
else
    fail "P8: .go_active marker present → expected exit 0, got $EC"
fi

# Confirm: removing marker causes block
rm -f "$PROJ_P8/.claude/.go_active"
EC=$(run_gate "$JSON")
if [[ "$EC" == "2" ]]; then
    pass "P8-removed: after removing .go_active → exit 2"
else
    fail "P8-removed: after removing .go_active → expected exit 2, got $EC"
fi

# ---------------------------------------------------------------------------
# ─── PATH 9: No coding keywords → allow ──────────────────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 9: No coding keywords in description+prompt → allow ==="

PROJ_P9=$(setup_project "p9")

declare -a NO_KW_DESCS=(
    "Summarize the architecture"
    "Report on system status"
    "List all configuration options"
    "Describe the data flow"
    ""
)
for desc in "${NO_KW_DESCS[@]}"; do
    JSON=$(agent_json "$PROJ_P9" "$desc" "" "sonnet")
    EC=$(run_gate "$JSON")
    if [[ "$EC" == "0" ]]; then
        pass "P9: desc='${desc:-empty}' → no coding keywords → exit 0"
    else
        fail "P9: desc='${desc:-empty}' → expected exit 0, got $EC"
    fi
done

# No coding keywords in description, but prompt also has none
JSON=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {
        'description': 'Summarize output',
        'prompt': 'Tell me about the system',
        'model': 'sonnet',
    },
    'cwd': '$PROJ_P9',
    'session_id': 'test',
}))
")
EC=$(run_gate "$JSON")
if [[ "$EC" == "0" ]]; then
    pass "P9-with-prompt: no coding kw in desc+prompt → exit 0"
else
    fail "P9-with-prompt: no coding kw in desc+prompt → expected exit 0, got $EC"
fi

# ---------------------------------------------------------------------------
# ─── PATH 10: Recon-intent keyword overrides coding keywords → allow ─────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 10: Recon-intent keyword overrides coding keywords → allow ==="

PROJ_P10=$(setup_project "p10")

# Classic mixed case: contains a coding keyword AND a recon keyword in description
declare -a RECON_INTENT_TESTS=(
    "Find the file that needs to be fixed"
    "Search codebase for the bug to fix"
    "Check if the update was applied correctly"
    "Review and verify the changes made"
    "Inspect the deploy logs for errors"
    "Analyze then apply the patch"
    "Grep for the worker implementation"
    "ssh prod server to check restart status"
    "diagnose the issue and implement fix"
    "audit the implementation for defects"
    "implement and deploy the new endpoint"
)
for desc in "${RECON_INTENT_TESTS[@]}"; do
    JSON=$(agent_json "$PROJ_P10" "$desc" "" "sonnet")
    EC=$(run_gate "$JSON")
    if [[ "$EC" == "0" ]]; then
        pass "P10: '$desc' → recon-intent overrides coding kw → exit 0"
    else
        fail "P10: '$desc' → expected exit 0, got $EC"
    fi
done

# ---------------------------------------------------------------------------
# ─── PATH 11: model=haiku → allow (recon tier) ───────────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 11: model=haiku → allow (recon tier) ==="

PROJ_P11=$(setup_project "p11")

# With coding keywords (would normally block) but model=haiku → allow
declare -a HAIKU_DESCS=(
    "Worker: implement feature"
    "fix the broken function"
    "refactor the module"
    "update the configuration"
)
for desc in "${HAIKU_DESCS[@]}"; do
    JSON=$(agent_json "$PROJ_P11" "$desc" "" "haiku")
    EC=$(run_gate "$JSON")
    if [[ "$EC" == "0" ]]; then
        pass "P11: haiku + coding kw ('$desc') → exit 0"
    else
        fail "P11: haiku + coding kw ('$desc') → expected exit 0, got $EC"
    fi
done

# Confirm: same descriptions with non-haiku models block
for desc in "Worker: implement feature" "fix the broken function"; do
    for model in sonnet opus claude-3; do
        JSON=$(agent_json "$PROJ_P11" "$desc" "" "$model")
        EC=$(run_gate "$JSON")
        if [[ "$EC" == "2" ]]; then
            pass "P11-$model: model=$model + coding kw → exit 2 (not exempt)"
        else
            fail "P11-$model: model=$model + coding kw → expected exit 2, got $EC"
        fi
    done
done

# ---------------------------------------------------------------------------
# ─── PATH 12: Block — all conditions met ─────────────────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== PATH 12: Block — IMPLEMENT + no marker + coding keywords + no recon intent + not haiku ==="

PROJ_P12=$(setup_project "p12")
# Confirm no .go_active marker
rm -f "$PROJ_P12/.claude/.go_active"

declare -a BLOCK_DESCS=(
    "Worker: implement the new caching layer"
    "Verifier: write acceptance tests for the fix"
    "fix the regression in the payment module"
    "refactor the database connection pool"
    "write code for the authentication handler"
    "apply the migration script"
    "edit the configuration file to add new settings"
    "modify the router to add new endpoints"
    "add the missing validation logic"
    "change the API response format"
    "update the user model schema"
    "[sonnet] Worker: implement feature X"
    "implement the new endpoint"
    # Note: descriptions with recon-intent words like "deploy" route via P10
    # e.g. "implement and deploy" → allows (deploy is recon-intent) — that is P10 behavior
)
for desc in "${BLOCK_DESCS[@]}"; do
    JSON=$(agent_json "$PROJ_P12" "$desc" "" "sonnet")
    EC=$(run_gate "$JSON")
    if [[ "$EC" == "2" ]]; then
        pass "P12: '${desc:0:50}' → blocked (exit 2)"
    else
        fail "P12: '${desc:0:50}' → expected exit 2, got $EC"
    fi
done

# Coding keyword in prompt (not description) also triggers block
JSON=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {
        'description': 'General task',
        'prompt': 'implement a new endpoint for user registration',
        'model': 'sonnet',
    },
    'cwd': '$PROJ_P12',
    'session_id': 'test',
}))
")
EC=$(run_gate "$JSON")
if [[ "$EC" == "2" ]]; then
    pass "P12-prompt-kw: coding keyword in prompt only → blocked (exit 2)"
else
    fail "P12-prompt-kw: coding keyword in prompt only → expected exit 2, got $EC"
fi

# coding kw in description AND prompt — double coverage → still block
JSON=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {
        'description': 'fix the bug',
        'prompt': 'implement the fix in the payment module',
        'model': 'opus',
    },
    'cwd': '$PROJ_P12',
    'session_id': 'test',
}))
")
EC=$(run_gate "$JSON")
if [[ "$EC" == "2" ]]; then
    pass "P12-both-kw: coding kw in desc+prompt → blocked (exit 2)"
else
    fail "P12-both-kw: coding kw in desc+prompt → expected exit 2, got $EC"
fi

# ---------------------------------------------------------------------------
# ─── EDGE CASES ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------
echo ""
echo "=== EDGE CASES ==="

PROJ_EDGE=$(setup_project "edge")

# Empty stdin → fail-open (gate must not crash Claude)
set +e
EC=$(echo "" | env CLAUDE_HOME="$CLAUDE_HOME_DIR" CLAUDE_BOOSTER_SKIP_GO_GATE="" \
    python3 "$GATE_PY" 2>/dev/null; echo $?)
set -e
if [[ "$EC" == "0" ]]; then
    pass "EDGE-empty-stdin: empty stdin → exit 0 (fail-open)"
else
    fail "EDGE-empty-stdin: empty stdin → expected exit 0, got $EC"
fi

# Malformed JSON → fail-open
set +e
EC=$(echo "not-json-at-all{" | env CLAUDE_HOME="$CLAUDE_HOME_DIR" CLAUDE_BOOSTER_SKIP_GO_GATE="" \
    python3 "$GATE_PY" 2>/dev/null; echo $?)
set -e
if [[ "$EC" == "0" ]]; then
    pass "EDGE-malformed-json: malformed JSON → exit 0 (fail-open)"
else
    fail "EDGE-malformed-json: malformed JSON → expected exit 0, got $EC"
fi

# Empty JSON object → fail-open (tool_name empty, not "Agent")
set +e
EC=$(echo "{}" | env CLAUDE_HOME="$CLAUDE_HOME_DIR" CLAUDE_BOOSTER_SKIP_GO_GATE="" \
    python3 "$GATE_PY" 2>/dev/null; echo $?)
set -e
if [[ "$EC" == "0" ]]; then
    pass "EDGE-empty-object: {} → exit 0 (tool != Agent)"
else
    fail "EDGE-empty-object: {} → expected exit 0, got $EC"
fi

# JSON array (non-dict) → fail-open
set +e
EC=$(echo "[]" | env CLAUDE_HOME="$CLAUDE_HOME_DIR" CLAUDE_BOOSTER_SKIP_GO_GATE="" \
    python3 "$GATE_PY" 2>/dev/null; echo $?)
set -e
if [[ "$EC" == "0" ]]; then
    pass "EDGE-json-array: [] → exit 0 (non-dict handled)"
else
    fail "EDGE-json-array: [] → expected exit 0, got $EC"
fi

# P5/P6 interaction: subagent_type=Explore with coding description → P5 wins (allow)
JSON=$(agent_json "$PROJ_EDGE" "Worker: implement feature" "Explore" "sonnet")
EC=$(run_gate "$JSON")
if [[ "$EC" == "0" ]]; then
    pass "EDGE-explore-with-coding-desc: subagent_type=Explore + coding desc → P5 exempt (exit 0)"
else
    fail "EDGE-explore-with-coding-desc: subagent_type=Explore + coding desc → expected exit 0, got $EC"
fi

# P6 description prefix with no space after colon
JSON=$(agent_json "$PROJ_EDGE" "Explore:look at the codebase" "" "sonnet")
EC=$(run_gate "$JSON")
if [[ "$EC" == "0" ]]; then
    pass "EDGE-explore-no-space: 'Explore:look...' → P6 prefix exempt (exit 0)"
else
    fail "EDGE-explore-no-space: 'Explore:look...' → expected exit 0, got $EC"
fi

# P10 + P12 interaction check: recon keyword only in prompt (not description) → block
# (recon-intent check only applies to description, not combined text)
JSON=$(python3 -c "
import json
print(json.dumps({
    'tool_name': 'Agent',
    'tool_input': {
        'description': 'implement the feature',
        'prompt': 'find and check all existing tests',
        'model': 'sonnet',
    },
    'cwd': '$PROJ_EDGE',
    'session_id': 'test',
}))
")
EC=$(run_gate "$JSON")
if [[ "$EC" == "2" ]]; then
    pass "EDGE-recon-in-prompt-only: recon kw only in prompt → block (description checked for recon intent)"
else
    fail "EDGE-recon-in-prompt-only: recon kw only in prompt → expected exit 2 (description drives recon check), got $EC"
fi

# Decision log written (verify telemetry fires on block)
PROJ_LOG=$(setup_project "log_check")
rm -f "$PROJ_LOG/.claude/.go_active"
LOG_CLAUDE_HOME="$WORK_DIR/claude_home_log"
mkdir -p "$LOG_CLAUDE_HOME/logs"

JSON=$(agent_json "$PROJ_LOG" "Worker: implement feature" "" "sonnet")
set +e
env CLAUDE_HOME="$LOG_CLAUDE_HOME" CLAUDE_BOOSTER_SKIP_GO_GATE="" \
    python3 "$GATE_PY" <<< "$JSON" 2>/dev/null
set -e

LOG_FILE="$LOG_CLAUDE_HOME/logs/go_gate_decisions.jsonl"
if [[ -f "$LOG_FILE" ]]; then
    if python3 -c "
import json, sys
with open('$LOG_FILE') as f:
    for line in f:
        try:
            rec = json.loads(line.strip())
            if rec.get('gate') == 'go' and rec.get('decision') == 'block':
                sys.exit(0)
        except Exception:
            pass
sys.exit(1)
" 2>/dev/null; then
        pass "EDGE-telemetry: block decision written to go_gate_decisions.jsonl"
    else
        fail "EDGE-telemetry: no block decision found in go_gate_decisions.jsonl"
    fi
else
    fail "EDGE-telemetry: go_gate_decisions.jsonl not created"
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
