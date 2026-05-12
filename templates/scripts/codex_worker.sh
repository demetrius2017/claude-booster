#!/usr/bin/env bash
set -euo pipefail

CODEX_BIN="/opt/homebrew/bin/codex"

if [[ $# -lt 1 ]]; then
    echo "usage: codex_worker.sh <MODEL> [extra args...]" >&2
    exit 2
fi

MODEL="$1"
shift

if [[ ! -x "$CODEX_BIN" ]]; then
    echo "codex_worker.sh: codex binary not found at $CODEX_BIN" >&2
    exit 127
fi

exec "$CODEX_BIN" exec --skip-git-repo-check -m "$MODEL" "$@" -
