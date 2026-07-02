#!/usr/bin/env bash
# Acceptance test: /code-review supports an explicit reviewer model selector.

set -euo pipefail

ROOT="/Users/dmitrijnazarov/Projects/Claude_Booster"
CMD="$ROOT/templates/commands/code-review.md"
SKILL="$ROOT/templates/codex/skills/code-review/SKILL.md"
PROMPT="$ROOT/templates/codex/prompts/code-review.md"
RUNNER="$ROOT/templates/codex/skills/booster-command/SKILL.md"
AGENTS="$ROOT/AGENTS.md"

for f in "$CMD" "$SKILL" "$PROMPT" "$RUNNER" "$AGENTS"; do
  [[ -s "$f" ]] || {
    echo "FAIL missing file: $f"
    exit 1
  }
done

grep -q 'code-review fable' "$CMD"
grep -q 'Review model routing' "$CMD"
grep -q 'review_model=fable' "$CMD"
grep -q 'do not reinterpret the request as `/fable` or `/consilium`' "$CMD"
grep -q 'Review model: <review_model or default>' "$CMD"
grep -q '\[model\] \[topic\] \[--model <model>\]' "$PROMPT"
grep -q 'optional review model' "$SKILL"
grep -q 'code-review \[model\]' "$RUNNER"
grep -q 'code-review \[model\]' "$AGENTS"

echo "PASS code-review model selector contract"
