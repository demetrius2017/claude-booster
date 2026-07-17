---
name: "autopilot"
description: "Enable, inspect, or disable Fable autopilot and its North Star for delegated engineering decisions."
---

# Booster Autopilot

Read the sibling skill `../booster-command/SKILL.md`, then run command
`autopilot` through that runner. Preserve the same hard boundary in Codex even
though Claude hooks are not active: UI action/visual acceptance always goes to
Dmitry, as do secrets, real/user/production data or persistent project files at
risk, irreversible actions, external messages, publication, payments/orders,
and expansion beyond existing authority. Classify security topics by blast
radius and reversibility: validated task-specific temporary fixtures and
sandbox-only reversible changes may be delegated.

Treat remaining user text as `on <North Star>`, `status`, or `off`. Fable calls
must use `~/.claude/scripts/fable_consult.sh`; never fabricate a user answer.

Codex must use the same trusted lifecycle as Claude:
`fable_autopilot.py consult-decision --prompt-file` or trusted
`checkpoint plan_complete|first_slice|final_diff --prompt-file`. The runner
itself reserves, invokes `fable_consult.sh`, hashes exact output, validates an
`ON_COURSE|REFOCUS|REPLAN|ASK_USER` verdict, typed directive, output SHA-256,
and `/go fable` watchlist reconciliation. State is project-local and its stored `scope` must equal the
resolved git/workspace root; never use ambient HOME state for another project.
