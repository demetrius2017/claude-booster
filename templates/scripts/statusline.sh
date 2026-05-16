#!/usr/bin/env bash
# statusline.sh — prints the current workflow phase for Claude Code's status bar.
# Walks up from CWD looking for .claude/.phase; defaults to RECON if not found.
# Claude Code pipes JSON session data via stdin; we consume it to enrich output.
# MUST exit 0 always — Claude Code may suppress output or break on non-zero.

# Consume stdin from Claude Code (unblocks the pipe; JSON session data)
input=$(cat)

# Extract model name and context percentage from JSON if jq is available
model_info=""
if command -v jq >/dev/null 2>&1 && [ -n "$input" ]; then
    raw_model=$(printf '%s' "$input" | jq -r '.model.display_name // empty' 2>/dev/null)
    raw_ctx=$(printf '%s' "$input" | jq -r '.context_window.used_percentage // empty' 2>/dev/null)
    if [ -n "$raw_ctx" ]; then
        # Build progress bar: ▰ = filled, ▱ = empty, 20 blocks total
        pct=${raw_ctx%.*}  # truncate to integer
        filled=$(( pct * 20 / 100 ))
        [ "$filled" -gt 20 ] && filled=20
        [ "$filled" -lt 0 ] && filled=0
        bar=""
        for ((i=0; i<filled; i++)); do bar+="▰"; done
        for ((i=filled; i<20; i++)); do bar+="▱"; done
        if [ -n "$raw_model" ]; then
            model_info=" ${raw_model} ${bar} ${pct}%"
        else
            model_info=" ${bar} ${pct}%"
        fi
    elif [ -n "$raw_model" ]; then
        model_info=" ${raw_model}"
    fi
fi

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

printf '[%s]%s\n' "$phase" "$model_info"
exit 0
