#!/usr/bin/env bash
# Acceptance test: verify model_metric_capture + model_balancer hooks are registered
# in ~/.claude/settings.json (PostToolUse and SessionStart respectively).
#
# Assertions:
#   C1.  settings.json exists and size > 1KB
#   C2.  Parses as valid JSON (no syntax errors)
#   C3.  Contains ≥1 reference to model_metric_capture
#   C4.  Contains ≥1 reference to "model_balancer.py decide"
#   C5.  model_metric_capture command lives inside PostToolUse section (JSON walk)
#   C6.  model_balancer.py decide command lives inside SessionStart section (JSON walk)
#   C7.  Both referenced scripts exist and are executable on disk
#   C8.  Template mirror parity (skipped with NOTE if template absent)
#   C9.  File starts with '{' and ends with '}' (no corruption)
#
# Exit 0 = PASS (all assertions satisfied)
# Exit 1 = FAIL (one or more assertions failed)

set -uo pipefail

SETTINGS="$HOME/.claude/settings.json"
TEMPLATE="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/settings.json.template"
SCRIPT_CAPTURE="$HOME/.claude/scripts/model_metric_capture.py"
SCRIPT_BALANCER="$HOME/.claude/scripts/model_balancer.py"

PASS=0
FAIL=0
NOTES=()

pass() { echo "  PASS  $1"; (( PASS++ )); }
fail() { echo "  FAIL  $1"; (( FAIL++ )); }
note() { NOTES+=("  NOTE  $1"); }

# ── Temp file for baseline snapshot (cleanup-only trap — does NOT restore settings) ──
BASELINE="/tmp/hooks_test_baseline_$$.json"
cp "$SETTINGS" "$BASELINE" 2>/dev/null || true
cleanup() { rm -f "$BASELINE"; }
trap cleanup EXIT

echo "=== test_hooks_registration.sh ==="
echo "  settings : $SETTINGS"
echo "  baseline : $BASELINE"
echo ""

# ── C1: exists + size > 1KB ──────────────────────────────────────────────────
if [[ -f "$SETTINGS" ]]; then
    sz=$(wc -c < "$SETTINGS" | tr -d ' ')
    if (( sz > 1024 )); then
        pass "C1: settings.json exists, size=${sz} bytes (>1KB)"
    else
        fail "C1: settings.json exists but size=${sz} is ≤ 1KB"
    fi
else
    fail "C1: settings.json not found at $SETTINGS"
fi

# ── C2: valid JSON ────────────────────────────────────────────────────────────
if python3 -c "import json, sys; json.load(open('$SETTINGS'))" 2>/dev/null; then
    pass "C2: settings.json parses as valid JSON"
else
    fail "C2: settings.json is NOT valid JSON"
fi

# ── C3: grep for model_metric_capture ────────────────────────────────────────
cnt_capture=$(grep -c "model_metric_capture" "$SETTINGS" 2>/dev/null || echo 0)
if (( cnt_capture >= 1 )); then
    pass "C3: model_metric_capture found (${cnt_capture} occurrences)"
else
    fail "C3: model_metric_capture NOT found in settings.json"
fi

# ── C4: grep for model_balancer.py decide ────────────────────────────────────
cnt_balancer=$(grep -c "model_balancer\.py decide" "$SETTINGS" 2>/dev/null || echo 0)
if (( cnt_balancer >= 1 )); then
    pass "C4: 'model_balancer.py decide' found (${cnt_balancer} occurrences)"
else
    fail "C4: 'model_balancer.py decide' NOT found in settings.json"
fi

# ── C5: model_metric_capture is in PostToolUse section (JSON walk) ────────────
python3 - <<'PY'
import json, sys
with open('/Users/dmitrijnazarov/.claude/settings.json') as f:
    s = json.load(f)
posttool = s.get('hooks', {}).get('PostToolUse', [])
found = False
for entry in posttool:
    sub = entry.get('hooks', []) if isinstance(entry, dict) else []
    for h in sub:
        cmd = h.get('command', '') if isinstance(h, dict) else ''
        if 'model_metric_capture' in cmd:
            found = True
            break
    if found:
        break
sys.exit(0 if found else 1)
PY
if [[ $? -eq 0 ]]; then
    pass "C5: model_metric_capture found inside PostToolUse section"
else
    fail "C5: model_metric_capture NOT found inside PostToolUse section"
fi

# ── C6: model_balancer.py decide is in SessionStart section (JSON walk) ──────
python3 - <<'PY'
import json, sys
with open('/Users/dmitrijnazarov/.claude/settings.json') as f:
    s = json.load(f)
session_start = s.get('hooks', {}).get('SessionStart', [])
found = False
for entry in session_start:
    sub = entry.get('hooks', []) if isinstance(entry, dict) else []
    for h in sub:
        cmd = h.get('command', '') if isinstance(h, dict) else ''
        if 'model_balancer.py decide' in cmd:
            found = True
            break
    if found:
        break
sys.exit(0 if found else 1)
PY
if [[ $? -eq 0 ]]; then
    pass "C6: 'model_balancer.py decide' found inside SessionStart section"
else
    fail "C6: 'model_balancer.py decide' NOT found inside SessionStart section"
fi

# ── C7: scripts exist and are executable ──────────────────────────────────────
if [[ -x "$SCRIPT_CAPTURE" ]]; then
    pass "C7a: $SCRIPT_CAPTURE exists and is executable"
else
    fail "C7a: $SCRIPT_CAPTURE missing or not executable"
fi
if [[ -x "$SCRIPT_BALANCER" ]]; then
    pass "C7b: $SCRIPT_BALANCER exists and is executable"
else
    fail "C7b: $SCRIPT_BALANCER missing or not executable"
fi

# ── C8: template mirror parity ────────────────────────────────────────────────
if [[ -f "$TEMPLATE" ]]; then
    tc=$(grep -c "model_metric_capture" "$TEMPLATE" 2>/dev/null; true)
    tb=$(grep -c "model_balancer\.py decide" "$TEMPLATE" 2>/dev/null; true)
    tc=${tc//[^0-9]/}; tc=${tc:-0}
    tb=${tb//[^0-9]/}; tb=${tb:-0}
    if (( tc >= 1 )) && (( tb >= 1 )); then
        pass "C8: template mirror contains both hook references"
    elif (( tc < 1 )); then
        fail "C8: template missing model_metric_capture (found ${tc})"
    else
        fail "C8: template missing 'model_balancer.py decide' (found ${tb})"
    fi
else
    note "C8: template absent at $TEMPLATE — parity check skipped (PASS)"
    pass "C8: template absent — skipped"
fi

# ── C9: first byte '{', last non-whitespace '}' ───────────────────────────────
python3 - <<'PY'
import sys
with open('/Users/dmitrijnazarov/.claude/settings.json', 'rb') as f:
    content = f.read()
first = chr(content[0]) if content else ''
last = content.rstrip().decode('utf-8', errors='replace')[-1] if content.rstrip() else ''
sys.exit(0 if first == '{' and last == '}' else 1)
PY
if [[ $? -eq 0 ]]; then
    pass "C9: file starts with '{' and ends with '}' (no corruption)"
else
    fail "C9: file has unexpected first/last bytes (possible corruption)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
for n in "${NOTES[@]:-}"; do echo "$n"; done
echo ""
echo "=== RESULT: ${PASS} passed, ${FAIL} failed ==="
if (( FAIL == 0 )); then
    echo "EXIT 0 — PASS"
    exit 0
else
    echo "EXIT 1 — FAIL"
    exit 1
fi
