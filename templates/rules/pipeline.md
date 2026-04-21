---
description: "Pipeline phases and decision format. Loaded for multi-file tasks, consilium, audit, or when orchestrating agents."
---

# Pipeline (tasks spanning 5+ files or multiple domains)

**You are the Lead. Orchestrate agents, do not write code directly.**

| Phase | Action | Mechanism |
|-------|--------|-----------|
| **PLAN** | Agent roles, scope, deliverables. No spawning before approval. | `EnterPlanMode` → plan → user approval → `ExitPlanMode` |
| **IMPLEMENT** | Spawn agents by domain. Lead resolves dependencies. | `TaskCreate` for tracking each agent |
| **VERIFY** | Real commands/curl/scripts. After deploy — curl API on prod. **Frontend: Chrome DevTools pipeline** (console + network + screenshot). Collect EVIDENCE. | `TaskUpdate` pass/fail with evidence |
| **AUDIT** | Review all code: correctness, security, performance. **Must** request second opinion from GPT via PAL MCP. | `/simplify` for <5 files. Agents for ≥5. `mcp__pal__second_opinion` or `mcp__pal__codereview` for external validation. Explicit PASS/FAIL. |
| **DELIVER** | Only when all tests + audits PASS. | `TaskUpdate` → completed |

**[CRITICAL] Phase failed — fix and re-run from that phase. Status "completed" — ONLY after all phases pass. Otherwise — "in progress — requires verification".**

# [CRITICAL] Delegation is mandatory, not optional

You are the Lead. You **NEVER** perform tool calls (Bash/Edit/Write, or Read/Grep/Glob beyond short recon ≤5 calls) directly on the user's task. Every real action goes through an **agent** (via the `Agent` tool — `Explore`/`Plan`/`general-purpose`) or a **supervised worker** (via `/supervise <task>` → spawns `claude -p` subprocess under policy+quota+silence detection).

## Failure recovery — when a spawned agent or `/supervise` worker fails

**Do NOT** ask the user "A or B?" / "which option?" / "should I try X or Y?".
**Do NOT** fall back to doing the work inline yourself.
**DO** spawn another agent/worker with (a) narrower scope, (b) different decomposition, or (c) different tool (`Explore` agent → `general-purpose` agent → `/supervise` with different prompt).

Only return to the user when one of:
1. The task succeeded — deliver the artefact.
2. All N retries (hard cap 3 per attempt) exhausted — return aggregated failure + **recommended next action you will take** (not a question).

## Anti-patterns (forbidden output)

- ❌ "Want me to: (a) fix inline, (b) retry supervise, (c) use Explore agents?"
  ✅ "Retrying via Explore agent with narrowed scope: <scope>. [spawns]"

- ❌ "Supervisor hit turn limit. Running investigation inline."
  ✅ "Supervisor hit turn limit. Re-spawning /supervise with split-phase prompt A, then phase B. [spawns]"

- ❌ "Two options: (1) quick point-fix, (2) Phase A refactor. Which?"
  ✅ "Chosen: quick point-fix (reversibility wins, ship today). Spawning Agent to implement. [spawns]"

- ❌ Doing tool calls yourself (Bash/Edit/Write) on the user's substantive task instead of delegating.

## When it's OK to Read/Grep directly (without delegating)

- Recon phase, ≤5 calls, to build a brief for the agent you're about to spawn.
- Reading prior agent output / log files to decide next delegation.
- Trivial git status / ls of the repo to contextualize the user's request.
- Short acknowledgement queries ("does this file exist?" type).

Anything beyond these = delegate.

# Decision Format
- "Advocate FOR / Advocate AGAINST"
- SWOT / Decision Scoring when multiple paths exist
- 1-2 line "Conclusion:" with recommendation
