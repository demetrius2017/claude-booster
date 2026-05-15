# Consilium: Codex & PAL MCP — Root Cause Analysis

**Date:** 2026-05-15
**Trigger:** Codex CLI configured but unused (0% utilization); PAL MCP crashes on startup
**Agents:** Tooling Architect, Budget Engineer, Product Strategist, Infrastructure Engineer (blocked by enforcer)
**External:** GPT unavailable (PAL down — chicken-and-egg)

## Task Context

Claude Booster's model_balancer routes 7/9 categories to Codex CLI (flat-fee via ChatGPT Pro), but the model_tag_enforcer hook was blocking all Agent calls that lacked a `[model]` tag — which was every call, because CC bug #16598 prevents the auto-inject workaround. Meanwhile, PAL MCP (GPT second opinion for /audit and /consilium) was crashing at startup because `openai_models.json` contained a `provider_type` field that PAL's strict schema validation rejects.

## Root Causes (verified)

### 1. PAL MCP: `provider_type` field crashes model registry

- **File:** `~/.claude/openai_models.json`
- Two models (`gpt-5.3-codex`, `gpt-5.3-codex-spark`) had custom `provider_type: "codex-cli"` field added during model_balancer development
- PAL MCP (`BeehiveInnovations/pal-mcp-server`) validates schema strictly → rejects unknown fields
- OpenAI model provider fails to load → 0 models available → PAL crashes
- **Evidence:** PAL log `WARNING - Unsupported fields in model configuration: provider_type` → `ERROR - Auto mode is enabled but no models are available`

### 2. Enforcer Phase 2: tag check creates deadlock

- **File:** `templates/scripts/model_tag_enforcer.py` lines 421-428
- Phase 1 (codex routing advisory, exit 0) — worked as designed
- Phase 2 (model tag check) — hard block exit 2 if `[model]` tag missing
- CC bug #16598 prevents `updatedInput` auto-inject → tag always missing → always blocked
- **Architectural impossibility (Tooling Architect):** PreToolUse hook fires AFTER Lead chose the tool. Blocking forces a retry loop; Lead has no mechanism to switch from Agent→Bash+codex_worker.sh mid-call. Hook can validate parameters but cannot redirect tool selection.

### 3. Enforcer Phase 1: tier mismatch blocks stronger overrides

- **File:** `templates/scripts/model_tag_enforcer.py` lines 402-419
- high_blast_radius routes to `sonnet`, but consilium agents legitimately request `opus`
- Phase 1 blocked opus→sonnet "downgrade" without checking that opus > sonnet
- **Evidence:** Infrastructure Engineer consilium agent blocked — `model_balancer routes 'high_blast_radius' to sonnet, but model param is 'opus'`

## Agent Positions

| Agent | Position | Key Insight |
|-------|----------|-------------|
| Tooling Architect | Enforcer architecturally can't redirect tools; demote to advisory | "Never use a blocking gate to compensate for a missing tool-routing primitive" |
| Budget Engineer | $190/mo wasted (96.8% Codex unused); fix routing, ROI clear | Codex flat-fee vs Anthropic per-token — every Agent call that should be Codex is pure waste |
| Product Strategist | Kill entire routing layer, focus on working features | Routing is speculative infra fighting core product mission |
| Infrastructure Engineer | (blocked by enforcer — ironic proof of the bug) | N/A |

## User Strategic Feedback (post-consilium)

Dmitry redirected the approach with three key points:

1. **Why PAL when Codex is free?** PAL uses API tokens ($), Codex uses flat-fee ChatGPT Pro. Codex should replace PAL for all second-opinion/review use cases. PAL demoted to optional fallback.
2. **Sonnet causes rework.** Stop defaulting to Sonnet for coding. When something fails, escalate model tier — never retry at the same level. (Haiku→Sonnet→Opus, codex-5.3→codex-5.5→Opus)
3. **Why no Codex for consilium/audit/hackathon?** These multi-agent operations should use `codex_worker.sh gpt-5.5` (flat-fee, intelligence_score=20, same tier as Opus) instead of Agent tool (Anthropic quota).

## Decision

**Decompose, not kill-or-fix.** Three independent fixes:

1. **PAL MCP** — remove `provider_type` from openai_models.json. PAL works again as optional fallback.
2. **Enforcer Phase 2** — demote to advisory (exit 0). Tag is DX feature broken by CC #16598, shouldn't be a gate. Phase 1 tier mismatch — allow stronger model overrides (opus > sonnet = pass).
3. **Bio-agent tier** — new routing category: consilium/audit/hackathon agents → `codex_worker.sh gpt-5.5` via Bash (not Agent tool). Flat-fee, no API token burn. Added to tool-strategy.md, pipeline.md, consilium.md, audit.md.

## Rejected Alternatives

| Alternative | Why Rejected |
|-------------|-------------|
| Kill routing layer entirely | Routing is correct in principle; the bug was in enforcement, not design |
| Convert enforcer back to hard-block | Architecturally impossible — hooks can't redirect tool selection (CC #16598) |
| Keep PAL as primary for audit/consilium | Costs API tokens when Codex gpt-5.5 is flat-fee at same intelligence tier |
| Retry failed agents at same model tier | Causes rework loops — escalation is cheaper than repetition |

## Changes Applied

| File | Change |
|------|--------|
| `~/.claude/openai_models.json` | Removed `provider_type` from 2 Codex model entries |
| `templates/scripts/model_tag_enforcer.py` | Phase 2 → advisory (exit 0); Phase 1 allows stronger model override |
| `templates/rules/tool-strategy.md` | Bio-agent tier, escalation rule, PAL optional, Codex preferred |
| `templates/rules/pipeline.md` | AUDIT uses Codex gpt-5.5, PAL optional fallback |
| `~/.claude/commands/consilium.md` | Bio-agents via codex_worker.sh, PAL demoted |
| `~/.claude/commands/audit.md` | External review via codex_worker.sh, PAL fallback |

## Risks

1. **Codex quality unknown for audit/consilium** — gpt-5.5 intelligence_score=20 matches Opus, but real-world quality for code review and architectural debate is unproven. Monitor first 5 sessions.
2. **PAL as fallback may rot** — if never exercised, config may break again silently. Periodic smoke test recommended.
3. **Advisory-only enforcer** — Lead can now ignore all routing suggestions. Budget discipline depends on Lead's rules, not enforcement. Acceptable trade-off given CC #16598.
