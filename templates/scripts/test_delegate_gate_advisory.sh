#!/usr/bin/env bash
# Acceptance test for delegate_gate.py advisory-mode conversion.
#
# Independent Verifier test. Tests OBSERVABLE BEHAVIOR of the deployed hook
# /Users/dmitrijnazarov/.claude/scripts/delegate_gate.py against the Artifact
# Contract: the per-window budget gate becomes a NON-BLOCKING ADVISORY gate.
# Over-budget actions return exit 0 + stdout JSON {"additionalContext": ...}
# instead of exit 2. The only normal input that yields exit 2 is malformed
# stdin (fail-closed preserved). The .delegate_mode bypass machinery is retired.
#
# Invocation:  bash test_delegate_gate_advisory.sh
# Exit:        0 iff ALL cases pass, non-zero otherwise.
# Deterministic, no network, uses an isolated temp project dir, cleans up.
#
# The test does NOT read or judge the implementation source for correctness;
# it drives the hook via stdin and inspects exit code / stdout / state files.
# (Two grep-based source assertions are explicitly part of the Artifact
# Contract: retired-machinery absence + DECISION_ADVISORY presence + byte
# identity — these are contract requirements, not implementation judgments.)

set -u

GATE="/Users/dmitrijnazarov/.claude/scripts/delegate_gate.py"
TEMPLATE_GATE="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/delegate_gate.py"
COMMON="/Users/dmitrijnazarov/.claude/scripts/_gate_common.py"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "[FAIL] $1 — $2"; FAIL=$((FAIL+1)); }

# --- Isolated temp project dir (never touches the real repo's .claude) ---
PROJ="$(mktemp -d)"
mkdir -p "$PROJ/.claude" "$PROJ/src"
COUNTER_FILE="$PROJ/.claude/.delegate_counter"
PHASE_FILE="$PROJ/.claude/.phase"
MODE_FILE="$PROJ/.claude/.delegate_mode"
# Non-allowlisted target file: lives in src/, .py, not under /tests//tmp//docs/,
# no /.claude/ in the path, no README/.md/.txt/.log. Reaches the budget path.
TARGET="$PROJ/src/code.py"

cleanup() { rm -rf "$PROJ"; }
trap cleanup EXIT

# Guard: the temp project root must NOT itself match the ALLOWLIST_PATHS,
# otherwise an over-budget Edit would be allowlist-allowed and never reach the
# budget branch — making the headline assertions meaningless. Fail closed.
python3 - "$TARGET" <<'PYEOF'
import re, sys
ALLOWLIST_PATHS = [
    r"/docs/", r"/doc/", r"/reports/", r"/audits/", r"/tests/", r"/test/",
    r"/\.claude/", r"\.md$", r"\.txt$", r"README", r"CLAUDE\.md$",
    r"/scratch/", r"/tmp/", r"\.log$",
]
p = sys.argv[1]
for pat in ALLOWLIST_PATHS:
    if re.search(pat, p):
        sys.stderr.write(f"target path {p} matches allowlist {pat}; cannot reach budget path\n")
        sys.exit(3)
sys.exit(0)
PYEOF
if [[ $? -ne 0 ]]; then
    echo "[FAIL] setup — temp project path is allowlisted; environment ambiguous, failing closed"
    echo "Results: 0 passed, 1 failed"
    exit 1
fi

# Always start IMPLEMENT phase so the budget path is reachable.
seed_implement() { printf 'IMPLEMENT\n' > "$PHASE_FILE"; }
set_counter()    { printf '%s\n' "$1" > "$COUNTER_FILE"; }
read_counter()   { [[ -f "$COUNTER_FILE" ]] && tr -d '[:space:]' < "$COUNTER_FILE" || echo ""; }

# run_gate <json> [extra env assignments...]
# Writes stdout to $OUT, returns the gate's exit code in $RC.
OUT=""
RC=""
run_gate() {
    local json="$1"; shift
    local tmpout
    tmpout="$(mktemp)"
    # Force budget=1 deterministically regardless of caller environment.
    OUT="$(printf '%s' "$json" | env CLAUDE_BOOSTER_DELEGATE_BUDGET=1 "$@" python3 "$GATE" 2>/dev/null)"
    RC=$?
    printf '%s' "$OUT" > "$tmpout"
    rm -f "$tmpout"
}

# json_has_additionalContext <stdout> — exit 0 iff stdout is exactly one JSON
# object with a non-empty string key "additionalContext".
json_has_additionalContext() {
    _GATE_OUT="$1" python3 <<'PYEOF'
import os, json
raw = os.environ.get("_GATE_OUT", "").strip()
if not raw:
    raise SystemExit(1)
try:
    obj = json.loads(raw)
except Exception:
    raise SystemExit(2)
if not isinstance(obj, dict):
    raise SystemExit(3)
v = obj.get("additionalContext")
if not isinstance(v, str) or not v.strip():
    raise SystemExit(4)
raise SystemExit(0)
PYEOF
}

# json_is_single_object <stdout> — verify it parses as ONE json value and there
# is no trailing second line of content.
json_is_single_object() {
    _GATE_OUT="$1" python3 <<'PYEOF'
import os, json
raw = os.environ.get("_GATE_OUT", "")
stripped = raw.strip()
if not stripped:
    raise SystemExit(1)
# must be exactly one JSON document (no concatenated second object)
try:
    obj, idx = json.JSONDecoder().raw_decode(stripped)
except Exception:
    raise SystemExit(2)
# trailing content after the first JSON document → fail
if stripped[idx:].strip():
    raise SystemExit(3)
if not isinstance(obj, dict):
    raise SystemExit(4)
raise SystemExit(0)
PYEOF
}

# ===================================================================
# CASE 1 — Over-budget counted action → exit 0 AND advisory JSON stdout
# ===================================================================
seed_implement
set_counter 1   # == budget
run_gate "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"
if [[ "$RC" == "2" ]]; then
    fail "C1 over-budget exit code" "expected 0 (advisory), got 2 (HARD BLOCK — cascade risk)"
elif [[ "$RC" != "0" ]]; then
    fail "C1 over-budget exit code" "expected 0, got $RC"
elif json_has_additionalContext "$OUT"; then
    pass "C1 over-budget Edit → exit 0 + stdout JSON with non-empty 'additionalContext'"
else
    fail "C1 over-budget advisory stdout" "stdout not a JSON object with non-empty 'additionalContext'; got: [$OUT]"
fi

# Invariant: over_budget ⟹ exit_code != 2
if [[ "$RC" == "2" ]]; then
    fail "C1 invariant over_budget⟹!=2" "exit code was 2"
else
    pass "C1 invariant: over_budget ⟹ exit_code != 2"
fi

# ===================================================================
# CASE 2 — Cascade impossibility: every counted action over budget → exit 0
# ===================================================================
declare -a C2_JSON=(
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"make build\"},\"cwd\":\"$PROJ\"}"
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"
  "{\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"
  "{\"tool_name\":\"NotebookEdit\",\"tool_input\":{\"notebook_path\":\"$PROJ/src/nb.ipynb\"},\"cwd\":\"$PROJ\"}"
)
declare -a C2_LABEL=("Bash:make build" "Edit" "Write" "NotebookEdit")
c2_ok=1
for i in "${!C2_JSON[@]}"; do
    seed_implement
    set_counter 1
    run_gate "${C2_JSON[$i]}"
    if [[ "$RC" != "0" ]]; then
        fail "C2 cascade ${C2_LABEL[$i]}" "over-budget yielded exit $RC (expected 0; 2 would cancel siblings)"
        c2_ok=0
    fi
done
if [[ "$c2_ok" == "1" ]]; then
    pass "C2 cascade impossibility: all 4 over-budget counted actions → exit 0 (never 2)"
fi

# ===================================================================
# CASE 3 — Malformed stdin → exit 2 (fail-closed preserved)
# ===================================================================
# 3a: non-JSON garbage
run_gate 'not json{{{'
RC_A=$RC
ERR_A="$(printf 'not json{{{' | env CLAUDE_BOOSTER_DELEGATE_BUDGET=1 python3 "$GATE" 2>&1 1>/dev/null)"
# 3b: a JSON array (valid JSON, non-dict)
run_gate '["a","b"]'
RC_B=$RC
if [[ "$RC_A" == "2" && "$RC_B" == "2" ]]; then
    if printf '%s' "$ERR_A" | grep -qiE "malformed|block"; then
        pass "C3 malformed stdin (garbage + JSON array) → exit 2, stderr mentions malformed/blocking"
    else
        fail "C3 malformed stderr" "exit 2 OK but stderr lacked 'malformed'/'block': [$ERR_A]"
    fi
else
    fail "C3 malformed stdin" "expected exit 2 for both, got garbage=$RC_A array=$RC_B"
fi

# Invariant: parse_ok==False ⟹ exit_code==2
if [[ "$RC_A" == "2" && "$RC_B" == "2" ]]; then
    pass "C3 invariant: parse_ok==False ⟹ exit_code==2"
else
    fail "C3 invariant parse_fail⟹2" "garbage=$RC_A array=$RC_B"
fi

# ===================================================================
# CASE 4 — Advisory stdout is exactly ONE clean JSON object
# ===================================================================
seed_implement
set_counter 1
run_gate "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"
if [[ "$RC" == "0" ]] && json_is_single_object "$OUT" && json_has_additionalContext "$OUT"; then
    pass "C4 advisory stdout = exactly one JSON object, key 'additionalContext'"
else
    fail "C4 single clean JSON" "rc=$RC; stdout=[$OUT]"
fi

# ===================================================================
# CASE 5 — Delegation tools reset the counter to 0
# ===================================================================
# Agent
seed_implement
set_counter 5
run_gate "{\"tool_name\":\"Agent\",\"tool_input\":{\"description\":\"x\",\"prompt\":\"y\"},\"cwd\":\"$PROJ\"}"
RC_AG=$RC; CTR_AG="$(read_counter)"
# TaskCreate
seed_implement
set_counter 5
run_gate "{\"tool_name\":\"TaskCreate\",\"tool_input\":{\"description\":\"x\"},\"cwd\":\"$PROJ\"}"
RC_TC=$RC; CTR_TC="$(read_counter)"
if [[ "$RC_AG" == "0" && "$CTR_AG" == "0" && "$RC_TC" == "0" && "$CTR_TC" == "0" ]]; then
    pass "C5 delegation tools (Agent, TaskCreate) → exit 0 AND counter reset to 0"
else
    fail "C5 delegation reset" "Agent rc=$RC_AG ctr=$CTR_AG; TaskCreate rc=$RC_TC ctr=$CTR_TC (want rc=0 ctr=0)"
fi

# ===================================================================
# CASE 6 — .delegate_mode=off:<sid> is IGNORED (no bypass machinery)
# ===================================================================
seed_implement
set_counter 1
printf 'off:S1\n' > "$MODE_FILE"
run_gate "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"session_id\":\"S1\",\"cwd\":\"$PROJ\"}"
RC_MODE=$RC; OUT_MODE="$OUT"
rm -f "$MODE_FILE"
# Behavior with the file present must equal the no-file over-budget advisory:
# exit 0 + advisory JSON. (If bypass were honoured we'd also see exit 0, but the
# distinguishing proof is the SOURCE grep below: the machinery must be gone.)
mode_behavior_ok=0
if [[ "$RC_MODE" == "0" ]] && json_has_additionalContext "$OUT_MODE"; then
    mode_behavior_ok=1
fi
# Source grep: retired machinery must be absent from the gate source.
GREP_CT="$(grep -c '_mode_disabled\|MODE_FILE_REL' "$GATE" 2>/dev/null || true)"
if [[ "$mode_behavior_ok" == "1" && "$GREP_CT" == "0" ]]; then
    pass "C6 .delegate_mode=off:S1 ignored → over-budget still exit-0 advisory; source has 0 _mode_disabled/MODE_FILE_REL"
else
    fail "C6 .delegate_mode ignored" "advisory_ok=$mode_behavior_ok (rc=$RC_MODE); grep _mode_disabled/MODE_FILE_REL count=$GREP_CT (want 0)"
fi

# ===================================================================
# CASE 7 — Sub-agent auto-skip: over-budget → exit 0, counter unchanged
# ===================================================================
seed_implement
set_counter 7
run_gate "{\"agent_id\":\"x\",\"agent_type\":\"general-purpose\",\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"
RC_SA=$RC; CTR_SA="$(read_counter)"
if [[ "$RC_SA" == "0" && "$CTR_SA" == "7" ]]; then
    pass "C7 sub-agent (agent_id/agent_type set) over-budget → exit 0, counter unchanged (=7)"
else
    fail "C7 sub-agent auto-skip" "rc=$RC_SA (want 0), counter=$CTR_SA (want unchanged 7)"
fi

# ===================================================================
# CASE 8 — Within-budget allow: counter 0→1, exit 0, NO advisory stdout
# ===================================================================
seed_implement
set_counter 0
run_gate "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"
RC_WB=$RC; CTR_WB="$(read_counter)"
wb_ok=1
[[ "$RC_WB" == "0" ]] || { fail "C8 within-budget exit" "expected 0, got $RC_WB"; wb_ok=0; }
[[ "$CTR_WB" == "1" ]] || { fail "C8 within-budget counter" "expected 1, got $CTR_WB"; wb_ok=0; }
# Within budget must NOT emit an advisory additionalContext (that's the
# over-budget signal only). Empty stdout is correct.
if json_has_additionalContext "$OUT"; then
    fail "C8 within-budget stdout" "in-budget path emitted advisory JSON; stdout=[$OUT]"
    wb_ok=0
fi
[[ "$wb_ok" == "1" ]] && pass "C8 within-budget Edit → exit 0, counter 0→1, no advisory stdout"

# ===================================================================
# CASE 9 — Byte identity + constant presence
# ===================================================================
# 9a: deployed gate byte-identical to template
if diff -q "$GATE" "$TEMPLATE_GATE" >/dev/null 2>&1; then
    pass "C9a deployed delegate_gate.py byte-identical to template"
else
    fail "C9a byte identity" "deployed != template (diff non-empty)"
fi
# 9b: _gate_common.py defines DECISION_ADVISORY at least once
ADV_CT="$(grep -c 'DECISION_ADVISORY' "$COMMON" 2>/dev/null || echo 0)"
if [[ "$ADV_CT" -ge 1 ]]; then
    pass "C9b _gate_common.py defines DECISION_ADVISORY ($ADV_CT occurrence(s))"
else
    fail "C9b DECISION_ADVISORY present" "grep -c DECISION_ADVISORY in _gate_common.py = $ADV_CT (want >=1)"
fi
# 9c: DECISION_BYPASS_HONOURED and DECISION_BYPASS_REFUSED still present in _gate_common.py
BH_CT="$(grep -c 'DECISION_BYPASS_HONOURED' "$COMMON" 2>/dev/null || echo 0)"
BR_CT="$(grep -c 'DECISION_BYPASS_REFUSED' "$COMMON" 2>/dev/null || echo 0)"
if [[ "$BH_CT" -ge 1 && "$BR_CT" -ge 1 ]]; then
    pass "C9c _gate_common.py still defines DECISION_BYPASS_HONOURED & DECISION_BYPASS_REFUSED"
else
    fail "C9c bypass constants retained" "HONOURED=$BH_CT REFUSED=$BR_CT (both want >=1)"
fi

# ===================================================================
# CASE 10 — Parse-clean: all three sources parse as valid Python
# ===================================================================
c10_ok=1
for f in "$GATE" "$TEMPLATE_GATE" "$COMMON"; do
    if ! python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" >/dev/null 2>&1; then
        fail "C10 parse-clean" "ast.parse failed for $f"
        c10_ok=0
    fi
done
[[ "$c10_ok" == "1" ]] && pass "C10 parse-clean: delegate_gate.py (deployed+template) and _gate_common.py all ast.parse OK"

# ===================================================================
# HEADLINE — prove NO normal input yields exit 2 except malformed stdin
# (recon bash, allowlist, delegation, sub-agent, within & over budget all → 0)
# ===================================================================
declare -a NORMAL=(
  "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status\"},\"cwd\":\"$PROJ\"}"                                  # recon bash
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$PROJ/docs/x.md\"},\"cwd\":\"$PROJ\"}"                          # allowlist path
  "{\"tool_name\":\"Agent\",\"tool_input\":{\"description\":\"d\"},\"cwd\":\"$PROJ\"}"                                     # delegation
  "{\"tool_name\":\"Read\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"                                  # free tool
  "{\"agent_id\":\"a\",\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"               # sub-agent
  "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TARGET\"},\"cwd\":\"$PROJ\"}"                                  # over-budget (counter=1)
)
headline_ok=1
for j in "${NORMAL[@]}"; do
    seed_implement
    set_counter 1
    run_gate "$j"
    if [[ "$RC" == "2" ]]; then
        fail "HEADLINE no-block-on-normal" "a normal input returned exit 2: $j"
        headline_ok=0
    fi
done
[[ "$headline_ok" == "1" ]] && pass "HEADLINE: no normal input yields exit 2 (only malformed stdin does)"

# ===================================================================
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" == "0" ]]
