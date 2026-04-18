---
description: "Rules auto-load canary. If Claude sees the token RULES_CANARY_2026_04_18_d7af3e in a system-reminder or loaded instructions, the ~/.claude/rules/ auto-load mechanism is working. Always loaded."
---

# Rules Auto-Load Canary

**Canary token:** `RULES_CANARY_2026_04_18_d7af3e`

Purpose: if this file is loaded into Claude's context at session start (via the `~/.claude/rules/*.md` auto-load mechanism), the canary token above will appear in one of Claude's system-reminders or loaded-instructions blocks. A subsequent check by either Claude or a human against `check_rules_loaded.py` can confirm the subsystem is alive.

## Failure modes this canary detects

- `~/.claude/rules/` glob stops matching due to a Claude Code upgrade changing path semantics.
- A hook format regression silently removes rule loading.
- Permission / ACL change on `~/.claude/` breaks read access for the harness.

## What to do if the canary is missing

1. Run `python ~/.claude/scripts/check_rules_loaded.py` — reports file state + expected token.
2. If file exists but token is not visible in Claude's instructions block: rules auto-load is broken. Check `claude --version`, `/memory` command, compare with a fresh install.
3. If file is missing entirely: restore from the most recent `~/claude_backup_*.tar.gz`.

## Do not edit

The token is rotated intentionally on audit cycles (next rotation: 2026-07-17 per `reports/audit_2026-04-17_agent_context_dysfunction.md` §Self-audit clause). Ad-hoc rotation defeats the cross-session comparison.
