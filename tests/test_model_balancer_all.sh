#!/usr/bin/env bash
# test_model_balancer_all.sh — aggregate runner for all model_balancer Day-1 suites.
# Exit 0 iff all 5 sub-suites exit 0; else 1.
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"

SUITES=(
  "test_model_metrics_migration.sh:8"
  "test_model_metric_capture.sh:11"
  "test_model_balancer.sh:32"
  "test_hooks_registration.sh:10"
  "test_session_start_balancer_summary.sh:20"
)

echo "=== model_balancer Day-1 — full smoke ==="
echo ""

SUITE_PASS=0
SUITE_FAIL=0
AGG_PASS=0
AGG_TOTAL=0
FAILED_OUTPUTS=""

i=0
for entry in "${SUITES[@]}"; do
  i=$((i + 1))
  script="${entry%%:*}"
  expected="${entry##*:}"
  path="$DIR/$script"

  # Run the sub-suite, capture output + exit code
  output="$(bash "$path" 2>&1)"
  rc=$?

  # Parse "X/N passed" or "X passed" patterns from the sub-suite summary line.
  # Handles:
  #   "Result: 8/8 passed"
  #   "Results: 8 passed"
  #   "Results: 8 passed, 0 failed"
  parsed_pass=""
  parsed_total=""
  if parsed=$(echo "$output" | grep -oE '[0-9]+/[0-9]+ passed' | tail -1); [[ -n "$parsed" ]]; then
    parsed_pass="${parsed%%/*}"
    parsed_total="${parsed#*/}"; parsed_total="${parsed_total%% *}"
  elif parsed=$(echo "$output" | grep -oE '[0-9]+ passed' | tail -1); [[ -n "$parsed" ]]; then
    parsed_pass="${parsed%% *}"
    parsed_total="$expected"
  fi

  # Fall back to expected count if parsing failed
  actual_pass="${parsed_pass:-$expected}"
  actual_total="${parsed_total:-$expected}"
  if [[ $rc -ne 0 ]]; then
    actual_pass="${parsed_pass:-?}"
    actual_total="${parsed_total:-$expected}"
  fi

  # Label
  LABEL=$(printf "%-50s" "[$i/5] $script")
  if [[ $rc -eq 0 ]]; then
    echo "$LABEL PASS ($actual_pass/$actual_total)"
    SUITE_PASS=$((SUITE_PASS + 1))
    AGG_PASS=$((AGG_PASS + actual_pass))
    AGG_TOTAL=$((AGG_TOTAL + actual_total))
  else
    echo "$LABEL FAIL ($actual_pass/$actual_total)"
    SUITE_FAIL=$((SUITE_FAIL + 1))
    AGG_TOTAL=$((AGG_TOTAL + actual_total))
    FAILED_OUTPUTS="${FAILED_OUTPUTS}
--- STDOUT/STDERR for $script ---
${output}
---"
  fi
done

echo ""

# Print failed suite outputs
if [[ -n "$FAILED_OUTPUTS" ]]; then
  printf "%s\n" "$FAILED_OUTPUTS"
  echo ""
fi

SUITE_TOTAL=$((SUITE_PASS + SUITE_FAIL))
echo "=== Aggregate: $SUITE_PASS/$SUITE_TOTAL suites PASS, $AGG_PASS/$AGG_TOTAL assertions ==="

if [[ $SUITE_FAIL -gt 0 ]]; then
  echo "EXIT 1"
  exit 1
else
  echo "EXIT 0"
  exit 0
fi
