#!/usr/bin/env bash
# test_codex_quota_wiring.sh — Acceptance test: Codex Pro quota wiring in model_balancer.py
#
# Verifies the observable contract from the Artifact Contract:
#   A1 — _score_candidate accepts budget_pcts dict; codex-cli gets non-zero budget when pct > 0
#   A2 — _get_codex_quota_pct() returns 0.0 gracefully when no ~/.codex/state_*.sqlite exists
#   A3 — `model_balancer.py status` output contains the string "codex_pro_quota"
#   A4 — `model_balancer.py decide` writes codex_pro_weekly_used_pct into inputs_snapshot
#   A5 — Template and installed copies are identical (diff must be empty)
#   A6 — All existing model_balancer tests still pass
#
# Exit 0 if all pass, exit 1 if any fail.

set -uo pipefail

LIVE_SCRIPT="$HOME/.claude/scripts/model_balancer.py"
MIRROR_SCRIPT="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/scripts/model_balancer.py"
LIVE_JSON="$HOME/.claude/model_balancer.json"
BOOSTER_ROOT="/Users/dmitrijnazarov/Projects/Claude_Booster"
TESTS_DIR="$BOOSTER_ROOT/tests"

PASS=0
FAIL=0

pass_a() { printf "  PASS  %s\n" "$1"; PASS=$((PASS + 1)); }
fail_a() { printf "  FAIL  %s  (%s)\n" "$1" "$2"; FAIL=$((FAIL + 1)); }

# ── Temp dir for all side-effect isolation ────────────────────────────────────
TMPDIR_TEST=$(mktemp -d /tmp/codex_quota_wiring_test.XXXXXX)
BALANCER_BAK="$TMPDIR_TEST/balancer.bak.json"
FAKE_CODEX_DIR="$TMPDIR_TEST/fake_codex"
TEMP_BALANCER_JSON="$TMPDIR_TEST/balancer_a4.json"
mkdir -p "$FAKE_CODEX_DIR"

cleanup() {
  # Restore live balancer JSON if it was backed up
  if [[ -f "$BALANCER_BAK" ]]; then
    cp "$BALANCER_BAK" "$LIVE_JSON" 2>/dev/null || true
  fi
  rm -rf "$TMPDIR_TEST"
}
trap cleanup EXIT

# Backup live balancer JSON before any decide() call that might touch it
cp "$LIVE_JSON" "$BALANCER_BAK" 2>/dev/null || true

echo "codex_quota_wiring acceptance test:"
echo ""

# ──────────────────────────────────────────────────────────────────────────────
# A1 — _score_candidate accepts budget_pcts dict; codex-cli gets non-zero budget
#      When budget_pcts["codex-cli"] > 0, codex-cli score is LOWER than with 0.
# ──────────────────────────────────────────────────────────────────────────────

A1_PY="$TMPDIR_TEST/a1.py"
cat > "$A1_PY" << 'PYEOF'
import sys, importlib.util

spec = importlib.util.spec_from_file_location("model_balancer", sys.argv[1])
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)

# Call _score_candidate with the new budget_pcts keyword argument.
# Contract: when codex-cli has 50% quota used, its score must be LOWER than 0%.
try:
    score_zero = mb._score_candidate(
        "codex-cli", "gpt-5.5-codex", 1000.0, 2000.0,
        budget_pcts={"codex-cli": 0.0}
    )
    score_half = mb._score_candidate(
        "codex-cli", "gpt-5.5-codex", 1000.0, 2000.0,
        budget_pcts={"codex-cli": 0.5}
    )
    if score_half < score_zero:
        print("OK: codex budget pressure reduces score (zero={:.4f} half={:.4f})".format(
            score_zero, score_half))
    else:
        print("FAIL: score_half={:.4f} not < score_zero={:.4f}".format(score_half, score_zero))
except TypeError as e:
    print("FAIL: budget_pcts param not accepted — {}".format(e))
except AttributeError as e:
    print("FAIL: _score_candidate missing — {}".format(e))
PYEOF

A1_OUT=$(python3 "$A1_PY" "$LIVE_SCRIPT" 2>&1)
if echo "$A1_OUT" | grep -q "^OK:"; then
  pass_a "A1  _score_candidate budget_pcts: codex-cli pct>0 lowers score"
else
  fail_a "A1  _score_candidate budget_pcts: codex-cli pct>0 lowers score" "$A1_OUT"
fi

# ──────────────────────────────────────────────────────────────────────────────
# A2 — _get_codex_quota_pct() returns 0.0 when no ~/.codex/state_*.sqlite exists
#      Isolation: mock Path.home() to a temp dir with no .codex/ subdirectory.
# ──────────────────────────────────────────────────────────────────────────────

A2_PY="$TMPDIR_TEST/a2.py"
cat > "$A2_PY" << 'PYEOF'
import sys, importlib.util
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location("model_balancer", sys.argv[1])
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)

# _get_codex_quota_pct(prior: dict) — test two cases:
#
# Case 1: cap=0 — function should early-out and return 0.0 without touching FS.
# Case 2: cap>0 but ~/.codex/ contains no state_*.sqlite — graceful 0.0.
#   We achieve isolation by monkeypatching Path.home() to point at FAKE_CODEX_DIR's
#   parent so .codex/ doesn't exist there.

fake_home = Path(sys.argv[2]).parent  # parent of FAKE_CODEX_DIR (the temp root)

prior_no_cap  = {"codex_pro_weekly_tokens_cap": 0}
prior_with_cap = {"codex_pro_weekly_tokens_cap": 9_999_999}

try:
    # Case 1: cap=0 → early-out, always 0.0
    result_nocap = mb._get_codex_quota_pct(prior_no_cap)

    # Case 2: cap>0 but fake home has no .codex/*.sqlite → graceful 0.0
    with mock.patch.object(Path, "home", return_value=fake_home):
        result_withcap = mb._get_codex_quota_pct(prior_with_cap)

    ok_nocap   = isinstance(result_nocap,   float) and result_nocap   == 0.0
    ok_withcap = isinstance(result_withcap, float) and result_withcap == 0.0

    if ok_nocap and ok_withcap:
        print("OK: cap=0→{!r}  cap>0/no-db→{!r}".format(result_nocap, result_withcap))
    else:
        if not ok_nocap:
            print("FAIL: cap=0 returned {!r} (expected 0.0 float)".format(result_nocap))
        if not ok_withcap:
            print("FAIL: cap>0/no-db returned {!r} (expected 0.0 float)".format(result_withcap))
except AttributeError as e:
    print("FAIL: _get_codex_quota_pct not found — {}".format(e))
except Exception as e:
    print("FAIL: raised {} — {}".format(type(e).__name__, e))
PYEOF

A2_OUT=$(python3 "$A2_PY" "$LIVE_SCRIPT" "$FAKE_CODEX_DIR" 2>&1)
if echo "$A2_OUT" | grep -q "^OK:"; then
  pass_a "A2  _get_codex_quota_pct returns 0.0 gracefully with absent DB"
else
  fail_a "A2  _get_codex_quota_pct returns 0.0 gracefully with absent DB" "$A2_OUT"
fi

# ──────────────────────────────────────────────────────────────────────────────
# A3 — `model_balancer.py status` output contains "codex_pro_quota"
# ──────────────────────────────────────────────────────────────────────────────

A3_OUT=$(python3 "$LIVE_SCRIPT" status 2>&1) || true
if echo "$A3_OUT" | grep -q "codex_pro_quota"; then
  pass_a "A3  'model_balancer.py status' output contains 'codex_pro_quota'"
else
  fail_a "A3  'model_balancer.py status' output contains 'codex_pro_quota'" "output='$A3_OUT'"
fi

# ──────────────────────────────────────────────────────────────────────────────
# A4 — `model_balancer.py decide` writes codex_pro_weekly_used_pct into inputs_snapshot
#      Run against a temp balancer JSON (stale decision_date) to force re-evaluation.
#      CLAUDE_BALANCER_DISABLE_ACTIVE=1 to skip DB queries; CLAUDE_MODEL_BALANCER_PATH
#      overrides the live path so the live file is untouched.
# ──────────────────────────────────────────────────────────────────────────────

python3 - > "$TEMP_BALANCER_JSON" 2>/dev/null << 'PYEOF'
import json
data = {
    "schema_version": 2,
    "decision_date": "2000-01-01",
    "valid_until": "2000-01-02T00:00:00Z",
    "weight_profile": "balanced",
    "rationale": "seed for A4 test",
    "routing": {
        "trivial":        {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "recon":          {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "medium":         {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "coding":         {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "hard":           {"provider": "anthropic", "model": "claude-opus-4-7"},
        "consilium_bio":  {"provider": "anthropic", "model": "claude-opus-4-7"},
        "audit_external": {"provider": "pal",       "model": "gpt-5.5"},
        "lead":           {"provider": "anthropic", "model": "claude-opus-4-7"},
        "high_blast_radius": {"provider": "anthropic", "model": "claude-sonnet-4-6"}
    }
}
print(json.dumps(data, indent=2))
PYEOF

A4_OUT=$(
  CLAUDE_MODEL_BALANCER_PATH="$TEMP_BALANCER_JSON" \
  CLAUDE_BALANCER_DISABLE_ACTIVE=1 \
  CODEX_STATE_DIR="$FAKE_CODEX_DIR" \
  python3 "$LIVE_SCRIPT" decide --force 2>&1
) || true

A4_SNAPSHOT=$(python3 - "$TEMP_BALANCER_JSON" << 'PYEOF' 2>&1
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
snap = d.get('inputs_snapshot', {})
val = snap.get('codex_pro_weekly_used_pct', '__MISSING__')
print(val)
PYEOF
)

if [[ "$A4_SNAPSHOT" == "__MISSING__" ]]; then
  fail_a "A4  decide writes codex_pro_weekly_used_pct into inputs_snapshot" \
    "key absent from inputs_snapshot (decide output: $A4_OUT)"
elif python3 -c "import sys; float(sys.argv[1])" "$A4_SNAPSHOT" 2>/dev/null; then
  pass_a "A4  decide writes codex_pro_weekly_used_pct into inputs_snapshot (value=$A4_SNAPSHOT)"
else
  fail_a "A4  decide writes codex_pro_weekly_used_pct into inputs_snapshot" \
    "value='$A4_SNAPSHOT' is not a float (decide output: $A4_OUT)"
fi

# ──────────────────────────────────────────────────────────────────────────────
# A5 — Template and installed copies are identical (diff must be empty)
# ──────────────────────────────────────────────────────────────────────────────

if [[ ! -f "$MIRROR_SCRIPT" ]]; then
  fail_a "A5  template and installed copies identical" "template missing: $MIRROR_SCRIPT"
elif diff -q "$LIVE_SCRIPT" "$MIRROR_SCRIPT" >/dev/null 2>&1; then
  pass_a "A5  template and installed copies identical"
else
  DIFF_LINES=$(diff "$LIVE_SCRIPT" "$MIRROR_SCRIPT" | head -20)
  fail_a "A5  template and installed copies identical" "$(printf 'diff non-empty:\n%s' "$DIFF_LINES")"
fi

# ──────────────────────────────────────────────────────────────────────────────
# A6 — All existing model_balancer tests still pass
# ──────────────────────────────────────────────────────────────────────────────

A6_OUT=$(bash "$TESTS_DIR/test_model_balancer_all.sh" 2>&1)
A6_EC=$?
if [[ $A6_EC -eq 0 ]]; then
  pass_a "A6  existing model_balancer tests all pass"
else
  TAIL=$(printf '%s' "$A6_OUT" | tail -20)
  fail_a "A6  existing model_balancer tests all pass" "$(printf 'exit=%d last lines:\n%s' "$A6_EC" "$TAIL")"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────────────────────────────────────

echo ""
printf "Total: %d passed, %d failed\n" "$PASS" "$FAIL"

if [[ $FAIL -gt 0 ]]; then
  exit 1
else
  exit 0
fi
