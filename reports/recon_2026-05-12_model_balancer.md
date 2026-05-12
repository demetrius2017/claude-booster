---
name: recon_2026-05-12_model_balancer
description: "Verified Facts Brief for model_balancer consilium — what exists, what's missing, what the architectural questions are"
type: recon
date: 2026-05-12
---

# RECON — model_balancer feature

## Trigger

User request 2026-05-12: design a `model_balancer` that distributes work between Claude (Opus/Sonnet/Haiku) and Codex (gpt-5.5 + simple tiers like gpt-5-mini, gpt-5-nano) based on:

1. **Real-time model speed** (Claude API infra reported 3x slower than baseline **right now**)
2. **Tokens consumed in the session / day** (Claude Max weekly quota mechanics)

Decision is taken **once per day** on the first `/start` in any project, persisted to a JSON file. Subsequent `/start` calls in the same day reuse the decision. Cross-project — one JSON, not per-project.

## Verified facts (from two parallel Explore RECON agents — Haiku)

### Existing infrastructure (green)

| Component | Location | What it does | Relevance |
|---|---|---|---|
| **Codex CLI** | `/opt/homebrew/bin/codex` v0.125.0 | `codex exec`, `codex mcp-server` (stdio), ChatGPT subscription auth. Smoke 2026-04-29: `codex exec "pong"` → 22s / 28k tokens | Pattern B integration target |
| **PAL MCP** | Built-in `mcp__pal__*` tools | Chat / thinkdeep / consensus / codereview / debug with gpt-5.5, gpt-5-mini, gpt-5-nano, o3, o3-mini, etc. | Already a working "delegate to GPT" channel |
| **rolling_memory.db** | `~/.claude/rolling_memory.db` | SQLite + FTS5, shared between Claude and Codex via `rolling_memory_mcp.py` (registered in `~/.codex/config.toml`) | Storage for daily decision + history |
| **supervisor/quota.py** | `~/.claude/scripts/supervisor/quota.py` | `QuotaTracker` — per-session token budget, circuit-breaker (CLOSED/HALF_OPEN/OPEN at 0%/50%/85%). DB table `supervisor_quota` | Token-side primitive already exists |
| **stream_json_adapter.py** | `~/.claude/scripts/supervisor/stream_json_adapter.py:165` | Receives `duration_ms` from Claude CLI in every response. Currently logged to stderr only — not persisted | Latency-side primitive exists but unused |
| **openai_models.json** | `~/.claude/openai_models.json` | 18 OpenAI models with `intelligence_score` (11–20), context windows, capabilities. PAL reads at startup | Model registry pattern (OpenAI side). Needs Claude-side equivalent or merge |
| **tool-strategy.md** | `~/.claude/rules/tool-strategy.md:11-20` | Qualitative routing: Haiku=trivial, Sonnet=coding/medium, Opus=hard. Lead always Opus 4.7 | Existing decision surface — needs adapter to consume runtime data |
| **SessionStart hooks** | `~/.claude/settings.json:440-523` | `memory_session_start.py`, `check_booster_update.py`, `check_fast_mode.py` | Injection point for daily decision load |
| **PostToolUse hooks** | `~/.claude/settings.json` | `compact_advisor.py`, `memory_post_tool.py` | Injection point for per-call latency capture |

### Critical gaps (red)

| Gap | Impact |
|---|---|
| **No latency/performance history** | Cannot answer "is Sonnet currently 3x slower than baseline?" — there is no baseline |
| **No `model_metrics` table** | No place to store `(date, model, task_category, latency_ms, tokens_in, tokens_out, success)` |
| **No daily-decision JSON** | Decision must live somewhere; not yet defined where (~/.claude/model_balancer.json? rolling_memory row?) |
| **No measurement bootstrap** | First `/start` ever has zero data — needs active probe (ping each model with cheap prompt) OR conservative default + accumulate passively |
| **Codex not registered as MCP in Claude Code** | Cannot call Codex as a tool from Claude — only one-way (Codex reads rolling_memory). Yesterday's LOW debt: Pattern B |
| **No cross-provider quota view** | Claude Max weekly limit + Codex ChatGPT subscription + per-session supervisor quota — three separate counters, no unified view |
| **No feedback loop on failures** | API 429 / generation error on Sonnet doesn't influence future model choice |

### Existing related work / decisions

- Yesterday's handover (`reports/handover_2026-05-11_184540.md`) explicitly marks **Codex CLI Pattern B (MCP server) integration** as the LOW debt to address as a separate session. Today's request expands scope: not just register Codex, but build a routing layer on top.
- `/consilium` feedback memory `feedback_consilium_lead_keep_opus.md` (2026-05-10): `/consilium` and `/lead` deliberately stay on Opus. The model_balancer must **respect explicit `model:` directives** in command files and not override them silently.
- `tool-strategy.md` already has a tie-breaker policy ("≥20 LOC non-boilerplate → Sonnet"; "why-question → Opus"; "what/where → Haiku"). The balancer should **augment**, not replace, this rule.
- `~/.claude/rolling_memory.db` schema has `agent_memory` (FTS5), `supervisor_decisions`, `supervisor_quota` — no `model_metrics` yet.

## Architectural questions for the consilium

These are the decisions where multi-perspective debate is genuinely needed (vs. trivially decidable):

### Q1. Measurement bootstrap — active probe vs passive observation

- **Active probe at first /start**: send a cheap canonical prompt (e.g., "echo: ok") to each candidate model, record wall-clock and tokens. Costs ~5-10K tokens once per day. Decision is data-driven from minute 1.
- **Passive observation**: don't probe; let the first N=10 real tool calls accumulate metrics, fall back to `tool-strategy.md` defaults until data exists. Zero probe cost; first-day decision is template-based.
- **Hybrid**: probe only the 2 models that are critical-path right now (Sonnet for code, Haiku for recon); passive for the rest.

**Trade-off:** active probe gives instant signal but burns tokens on a quota-constrained user; passive is free but blind on day 1.

### Q2. Topology — where does the balancer live

- **Standalone script** `~/.claude/scripts/model_balancer.py` called from SessionStart hook → writes JSON.
- **Hook** itself — embed logic inside `memory_session_start.py`.
- **MCP server** — a new `model_balancer_mcp.py` that exposes `get_routing_decision(task_category)` as a tool. Both Claude Code and Codex can query.
- **Modify `tool-strategy.md`** dynamically — inject `model:` overrides at SessionStart via `additionalContext`.

**Trade-off:** MCP is cleanest abstraction but heaviest engineering; standalone script + JSON is simplest. Dynamic rule injection feels clever but is harder to debug.

### Q3. Decision shape — proportion vs per-category vs threshold rules

Three plausible JSON schemas:

```json
// (a) Simple proportion across all delegations today
{"decision_date": "2026-05-12", "allocation": {"opus": 0.1, "sonnet": 0.4, "haiku": 0.2, "gpt-5.5": 0.2, "gpt-5-mini": 0.1}}
```

```json
// (b) Per-task-category routing
{"decision_date": "2026-05-12", "routing": {
  "trivial": "haiku",
  "coding": "gpt-5.5",         // because Sonnet 3x slower today
  "medium": "gpt-5-mini",
  "hard": "opus",
  "consilium_bio": "opus"       // explicit override stays
}}
```

```json
// (c) Threshold rules with adaptive fallback
{"decision_date": "2026-05-12", "rules": [
  {"if": "task=='coding' && claude_p95_latency_ms > 5000", "use": "gpt-5.5"},
  {"if": "task=='trivial'", "use": "haiku"},
  {"default": "from tool-strategy.md"}
]}
```

**Trade-off:** (a) is too coarse — can't say "Haiku for recon, gpt-5.5 for coding"; (b) is concrete but rigid; (c) is most powerful but needs a tiny evaluator.

### Q4. Daily-only vs adaptive-within-day

- **Daily-only**: compute at first /start of UTC day, frozen for 24h. Simple, predictable, but doesn't react if Claude infra recovers mid-day or Codex hits its own degradation.
- **Daily + adaptive override**: daily baseline + per-call latency check — if last 3 calls of a model exceeded p95 by 2x, fall back to next-best model for next N calls.
- **Decision invalidation triggers**: token-budget-crossed event, observed-failure rate >X%, explicit user override (`/balancer recompute`).

**Trade-off:** daily-only is the user's literal ask; adaptive is more robust but adds state machinery.

### Q5. Codex integration depth — Pattern B (MCP) vs Pattern A (subprocess) vs PAL-only

- **Pattern A — subprocess**: when balancer says "use Codex", Lead spawns `codex exec` as a Bash command. Output captured, no MCP needed. Simplest.
- **Pattern B — MCP server**: register `codex mcp-server` in settings.json. Claude Code sees Codex tools natively; balancer routes by selecting the tool. Cleanest.
- **PAL-only**: don't integrate Codex CLI directly; route to `mcp__pal__chat` with model=gpt-5.5 / gpt-5-mini. PAL is already wired. Lowest-risk first step.

**Trade-off:** PAL-only is fastest to ship — already works, no new MCP plumbing. Pattern B is the eventual clean architecture but a bigger lift.

### Q6. Cost vs latency objective — which dominates today

User said: "Claude 3x slower". This makes latency the immediate driver. But Claude Max is 86% weekly on day 5 (yesterday's signal), so token budget is also tight.

- **Latency-first**: prefer fastest model that's "good enough" for the task tier.
- **Cost-first**: prefer cheapest model that meets the quality bar (Claude Max tokens count toward weekly; Codex tokens are separate subscription).
- **Pareto**: define a score = `α × normalized_latency + β × normalized_cost + γ × success_rate`; pick model that maximizes.

**Trade-off:** pure-latency may exhaust Max early; pure-cost ignores the 3x slowdown user complained about. Pareto needs weights — who picks α/β/γ?

### Q7. Failure semantics — what happens when balancer is broken

- **Hard fail**: if balancer can't read JSON or sees malformed data — block /start with error.
- **Silent fallback**: log warning, fall back to `tool-strategy.md` defaults, continue.
- **Stale-OK policy**: if today's JSON missing but yesterday's exists, use yesterday's with a banner.

User feedback memory pattern (`feedback_active_pid_verification.md`): "fail-loud on irreversible, fail-soft on advisory". Balancer is advisory → silent fallback.

## What the consilium should produce

1. **Concrete topology choice** (Q2) — pick one, justify.
2. **Decision schema** (Q3) — pick (a), (b), (c) or hybrid; show a real example for today's "Claude 3x slow" scenario.
3. **Bootstrap policy** (Q1) — active / passive / hybrid; specify probe budget if active.
4. **Codex integration depth** (Q5) — phase plan if multi-stage.
5. **Adaptive trigger thresholds** (Q4) — concrete numbers, not "TBD".
6. **Objective function** (Q6) — answer the cost-vs-latency question with concrete weights or escalation rule.
7. **Failure mode** (Q7) — one sentence.
8. **Implementation phases** — what's day-1 ship vs. day-N polish. The user wants something working today.

## Constraints (non-negotiable)

- **Respect explicit `model:` directives in command files.** `/consilium` and `/lead` stay on Opus per user veto. The balancer is a default — explicit always wins.
- **No silent quality downgrade for coding tasks.** Sonnet → gpt-5.5 is acceptable; Sonnet → gpt-5-nano is not.
- **JSON file lives in `~/.claude/`**, not per-project — one decision for all projects on the same day.
- **Stuck-loop detector friendly** — daily decisions don't churn the rolling memory.
- **Code-over-docs** (per `~/.claude/rules/code-over-docs.md`): the balancer reads runtime state (current latency observations) over stored beliefs.
