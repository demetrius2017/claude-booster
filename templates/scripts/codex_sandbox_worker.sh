#!/usr/bin/env bash
set -euo pipefail

CODEX_BIN="/opt/homebrew/bin/codex"

# Argument check — mirror codex_worker.sh
if [[ $# -lt 1 ]]; then
    echo "usage: codex_sandbox_worker.sh <MODEL> [extra codex args...]" >&2
    exit 2
fi

MODEL="$1"; shift

if [[ ! -x "$CODEX_BIN" ]]; then
    echo "codex_sandbox_worker.sh: codex binary not found at $CODEX_BIN" >&2
    exit 127
fi

# Determine project root
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Create temp sandbox with guaranteed cleanup
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/codex_sandbox_XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT

# rsync project into sandbox (exclude heavy/irrelevant dirs)
rsync -a \
    --exclude='.git/' \
    --exclude='node_modules/' \
    --exclude='__pycache__/' \
    --exclude='.venv/' \
    --exclude='venv/' \
    --exclude='.next/' \
    --exclude='dist/' \
    --exclude='build/' \
    --exclude='.pytest_cache/' \
    --exclude='.mypy_cache/' \
    --exclude='.ruff_cache/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    --exclude='.claude/' \
    --exclude='*.egg-info/' \
    --exclude='.env*' \
    "$PROJECT_ROOT/" "$SANDBOX/"

# Initialize git baseline in sandbox
git -C "$SANDBOX" init -q
git -C "$SANDBOX" add -A
git -C "$SANDBOX" \
    -c user.name="codex-sandbox" \
    -c user.email="sandbox@localhost" \
    commit -q -m "baseline" --allow-empty

# Run Codex in sandbox — stdout→stderr so it doesn't mix with diff
CODEX_EXIT=0
"$CODEX_BIN" exec \
    --skip-git-repo-check \
    -C "$SANDBOX" \
    -s workspace-write \
    --ephemeral \
    -m "$MODEL" \
    "$@" - \
    >&2 || CODEX_EXIT=$?

if [[ "$CODEX_EXIT" -ne 0 ]]; then
    echo "codex_sandbox_worker.sh: codex exec failed (exit $CODEX_EXIT)" >&2
    exit 1
fi

# Stage any new files so git diff sees them
git -C "$SANDBOX" add -A

# Capture diff — only stdout output from this script
DIFF="$(git -C "$SANDBOX" diff --cached HEAD)"

if [[ -z "$DIFF" ]]; then
    exit 0
fi

printf '%s\n' "$DIFF"
