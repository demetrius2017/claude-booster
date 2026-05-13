#!/usr/bin/env bash
# statusline.sh — prints the current workflow phase for Claude Code's status bar.
# Walks up from CWD looking for .claude/.phase; defaults to RECON if not found.
# MUST exit 0 always — Claude Code may suppress output or break on non-zero.

phase="RECON"
dir="${PWD:-$HOME}"

while true; do
    phase_file="$dir/.claude/.phase"
    if [ -f "$phase_file" ]; then
        read -r content < "$phase_file" 2>/dev/null
        content="${content//[[:space:]]/}"
        if [ -n "$content" ]; then
            phase="$content"
        fi
        break
    fi
    parent="${dir%/*}"
    # ${dir%/*} yields "" when dir is "/foo" — normalize to root
    [ -z "$parent" ] && parent="/"
    if [ "$parent" = "$dir" ]; then
        break
    fi
    dir="$parent"
done

printf '[%s]\n' "$phase"
exit 0
