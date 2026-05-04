#!/usr/bin/env bash
# Acceptance test: financial_dml_guard.py is wired as a PreToolUse/Bash hook
# in settings.json.template.
#
# Exit 0  — all assertions pass.
# Exit 1  — one or more assertions fail (details printed to stderr).

set -euo pipefail

TEMPLATE="/Users/dmitrijnazarov/Projects/Claude_Booster/templates/settings.json.template"
PASS=0
FAIL=0

_pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
_fail() { echo "[FAIL] $1" >&2; FAIL=$((FAIL + 1)); }

# ── Guard: template must exist ────────────────────────────────────────────────
if [[ ! -f "$TEMPLATE" ]]; then
  echo "[FATAL] Template not found: $TEMPLATE" >&2
  exit 1
fi

# ── Assertion 1: file mentions financial_dml_guard.py at all ─────────────────
if grep -q "financial_dml_guard.py" "$TEMPLATE"; then
  _pass "financial_dml_guard.py present in template"
else
  _fail "financial_dml_guard.py NOT found in template"
fi

# ── Assertion 2: financial_dml_guard.py lives under a PreToolUse/Bash hook ───
#
# Strategy: extract the PreToolUse array from the JSON (after substituting
# ${...} placeholders), then confirm that the Bash-matcher block contains
# financial_dml_guard.py.
#
# We substitute every ${VAR} with "PLACEHOLDER" so python3's json.loads
# accepts the file without complaints about dollar-brace syntax.

python3 - "$TEMPLATE" <<'PYEOF'
import sys, re, json

template_path = sys.argv[1]
with open(template_path) as f:
    raw = f.read()

# Replace ${...} tokens with a plain string so JSON parses cleanly.
substituted = re.sub(r'\$\{[^}]+\}', 'PLACEHOLDER', raw)

try:
    data = json.loads(substituted)
except json.JSONDecodeError as e:
    print(f"[FAIL-JSON] {e}", file=sys.stderr)
    sys.exit(1)

pre_tool_use = data.get("hooks", {}).get("PreToolUse", [])
if not pre_tool_use:
    print("[FAIL] hooks.PreToolUse is absent or empty", file=sys.stderr)
    sys.exit(1)

# Find a block whose matcher is exactly "Bash" (not "Bash|..." composites)
bash_blocks = [
    block for block in pre_tool_use
    if block.get("matcher") == "Bash"
]

if not bash_blocks:
    print("[FAIL] No PreToolUse block with matcher=='Bash' found", file=sys.stderr)
    sys.exit(1)

# Check that at least one hook command in those blocks references financial_dml_guard.py
found_dml = False
for block in bash_blocks:
    for hook in block.get("hooks", []):
        if "financial_dml_guard.py" in hook.get("command", ""):
            found_dml = True
            break

if found_dml:
    print("[PASS] financial_dml_guard.py is in a PreToolUse/Bash matcher block")
else:
    print("[FAIL] financial_dml_guard.py is NOT inside any PreToolUse block with matcher=='Bash'", file=sys.stderr)
    sys.exit(1)
PYEOF

# python3 exited non-zero → FAIL already printed; propagate
if [[ $? -ne 0 ]]; then
  FAIL=$((FAIL + 1))
else
  # assertion 2 already printed PASS inside the python script; count it here
  PASS=$((PASS + 1))
fi

# ── Assertion 3: financial_dml_guard entry has correct source field ───────────
#
# The source value in the raw template should be 'booster@${BOOSTER_VERSION}'.
# We check the raw text directly (not the substituted JSON) so the literal
# placeholder is what we confirm exists.

if grep -A5 "financial_dml_guard.py" "$TEMPLATE" | grep -q '"source".*booster@\${BOOSTER_VERSION}'; then
  _pass "financial_dml_guard.py hook has source: booster@\${BOOSTER_VERSION}"
else
  _fail "financial_dml_guard.py hook is missing correct source field (expected booster@\${BOOSTER_VERSION})"
fi

# ── Assertion 4: verify_gate.py still present (existing hook not removed) ─────
if grep -q "verify_gate.py" "$TEMPLATE"; then
  _pass "verify_gate.py still present (existing hook intact)"
else
  _fail "verify_gate.py was removed from template — existing hook must not be deleted"
fi

# ── Assertion 5: template is valid JSON after placeholder substitution ─────────
python3 - "$TEMPLATE" <<'PYEOF'
import sys, re, json

template_path = sys.argv[1]
with open(template_path) as f:
    raw = f.read()

substituted = re.sub(r'\$\{[^}]+\}', 'PLACEHOLDER', raw)

try:
    json.loads(substituted)
    print("[PASS] Template is valid JSON (after placeholder substitution)")
    sys.exit(0)
except json.JSONDecodeError as e:
    print(f"[FAIL] Template is NOT valid JSON: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

if [[ $? -ne 0 ]]; then
  FAIL=$((FAIL + 1))
else
  PASS=$((PASS + 1))
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
