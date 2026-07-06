#!/usr/bin/env bash
# fable_tmux_status.sh — compact tmux status widget.
# Shows remaining Max rate-limit windows (5h/7d) and Fable spend (today + live
# session). Rate limits come from ~/.claude/.rate_limits_cache.json, which the
# Claude Code statusline (statusline.sh) refreshes from its stdin while a session
# is active; Fable spend comes from the fable_usage.py summary cache. Both are
# best-effort — the widget exits 0 on every failure and simply omits a segment
# whose source is missing or stale.

summary="${HOME}/.claude/fable_usage_summary.json"
rlcache="${HOME}/.claude/.rate_limits_cache.json"
RL_STALE_S=900   # hide rate limits if the statusline hasn't refreshed in 15 min

command -v jq >/dev/null 2>&1 || exit 0

parts=""

# --- Rate limits (remaining %) — only if fresh ---
if [ -f "$rlcache" ]; then
    now=$(date +%s 2>/dev/null || echo 0)
    upd=$(jq -r '.updated_at // 0' "$rlcache" 2>/dev/null)
    [[ "$upd" =~ ^[0-9]+$ ]] || upd=0
    if [ "$upd" -gt 0 ] && [ $(( now - upd )) -le "$RL_STALE_S" ]; then
        h5=$(jq -r '.five_hour_remaining // empty' "$rlcache" 2>/dev/null)
        d7=$(jq -r '.seven_day_remaining // empty' "$rlcache" 2>/dev/null)
        [ -n "$h5" ] && parts="${parts}5h:${h5}% "
        [ -n "$d7" ] && parts="${parts}7d:${d7}% "
    fi
fi

# --- Fable spend (today + live session) ---
if [ -f "$summary" ]; then
    enabled=$(jq -r 'select(.display_enabled == true) | .display_enabled // empty' "$summary" 2>/dev/null)
    if [ "$enabled" = "true" ]; then
        today=$(jq -r '.today.cost_usd // .last_task.cost_usd // empty' "$summary" 2>/dev/null)
        sess=$(jq -r '.session.cost_usd // empty' "$summary" 2>/dev/null)
        [ -n "$parts" ] && parts="${parts}· "
        [ -n "$today" ] && parts="${parts}F d\$${today}"
        # Show the live session only when it has non-zero spend (avoids a noisy
        # "s$0.00" on every session that never called Fable).
        if [ -n "$sess" ] && [ "$sess" != "0.0000" ] && [ "$sess" != "0.00" ]; then
            parts="${parts} s\$${sess}"
        fi
    fi
fi

# Trim trailing space and print (nothing if we have no data).
parts="${parts% }"
[ -n "$parts" ] && printf '%s' "$parts"
exit 0
