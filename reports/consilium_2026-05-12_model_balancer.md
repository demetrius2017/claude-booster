---
name: consilium_2026-05-12_model_balancer
description: "Consilium — model_balancer feature design. 4 Claude Opus bio-agents (Architect, SRE, MLOps, Cost/Ops) + GPT-5.5 external via PAL. Trigger: user reports Claude infra 3x slower than baseline and Max weekly 86% on day 5."
type: consilium
date: 2026-05-12
preserve: true
---

# Consilium — `model_balancer` feature design

## Task context

User requested a balancer that routes work between Claude (Opus/Sonnet/Haiku) and Codex / PAL-routed GPT models (gpt-5.5 / gpt-5-mini / gpt-5-nano) based on:

1. **Real-time speed** of each provider — Claude API infra reported **3× slower than baseline right now**.
2. **Tokens consumed** in the session / day — Claude Max was at 86% weekly on day 5 (yesterday).

Decision is computed once per day on the first `/start` (any project), written to `~/.claude/model_balancer.json`. Subsequent `/start` calls in the same UTC day reuse it. **One JSON for all projects**, not per-project.

Verified Facts Brief (input to all bio-agents): `reports/recon_2026-05-12_model_balancer.md` — built from two parallel Explore Haiku RECON agents that read the actual code, not memory.

The 7 questions in the brief were assigned across 4 Claude Opus bio-agents + 1 GPT-5.5 external via PAL thinkdeep:

| Agent | Bio | Assigned questions |
|---|---|---|
| Architect | Systems Architect (15y distributed systems / hook pipelines) | Q2 topology, Q7 failure semantics |
| SRE | Performance / SRE Engineer (10y latency telemetry) | Q1 bootstrap, percentile design, "3× slow" detection, schema |
| MLOps | MLOps / Routing Engineer (8y multi-model serving) | Q3 schema, Q4 cadence, Q6 objective |
| Cost | Cost / Operations Engineer (10y multi-provider AI stacks) | Q5 Codex integration depth, quota math, day-1 vs day-N |
| GPT-5.5 (PAL) | External dissent | All 7 |

## Agent positions

| Q | Architect | SRE | MLOps | Cost | GPT-5.5 (PAL) |
|---|---|---|---|---|---|
| Q1 — Bootstrap | — | **(C) Hybrid** — probe sonnet+haiku only, 800 tok/day; passive elsewhere | — | — | (B) Passive |
| Q2 — Topology | **(A) Standalone script + JSON** | — | — | — | (A) |
| Q3 — Schema | — | — | **(b) Per-task-category** | — | (b) |
| Q4 — Cadence | — | — | **(iii) Daily + invalidation triggers** | — | (iii) |
| Q5 — Codex depth | — | — | — | **(C) PAL-only today**, A→B as later migrations | (C) |
| Q6 — Objective | — | — | **(iii) Pareto α=0.5/β=0.3/γ=0.2 with `budget_pressure` profile** | — | (i) Latency-first today |
| Q7 — Failure | **(B) Silent fallback** | — | — | — | (B) |

### Key insights — one per agent

**Architect.** SessionStart in `~/.claude/settings.json:440-523` already has 6 duplicate hook entries — `memory_session_start.py` fires 3–6× per session. Embedding balancer logic into it would multiply the failure surface. The compact_advisor pair is the established advisory-hook precedent (one-shot marker file + JSONL audit + env bypass) — the balancer should mirror it byte-for-byte. Decision JSON lives at `~/.claude/model_balancer.json` (hand-readable, atomic-replace pattern from `compact_advisor.py:104-113`); raw observations live in `rolling_memory.db` `model_metrics` table; failure → silent fallback to `tool-strategy.md` defaults + log to `~/.claude/logs/model_balancer.jsonl` + write an `error_lesson` row so next `/start` rolling-memory surfaces it.

**SRE — the most consequential finding.** `stream_json_adapter.py:311-327` confirms `duration_ms` is the wall-clock of the **entire `claude -p` subprocess** (N turns × M tools × thinking), not per-API-call. Using it raw as "model latency" is contaminated: a 10-turn worker that read 30 files reports 90s even if the model itself was fast. Mitigation: divide by `num_turns` (captured but unused at `:323`), or parse per-`assistant`-block timestamps as a 50-LOC enrichment. Probe cost is **800 tok/day** (2 models × 400 tok round-trip on the prompt `"Reply with exactly: ok"`), not the 5–10K estimated in the brief. p75 over `min(last 30 samples, last 4h)` chosen over p95 (with daily N=20–30 samples, p95 has 1–2 supporting points and flaps). Welford running stats for incremental aggregation. Cold-start handling: drop first K=1 per model per session. Degraded detection: `p75(last 20) > 2.5× rolling_7d_p75 grouped by (model, task_category)`; require 3 consecutive 5-sample windows to flip the flag.

**MLOps.** `openai_models.json` has `intelligence_score` (1–20) — use it directly, don't invent a new quality signal. Claude-side mapping: opus-4.7=20, sonnet-4.6=17, haiku-4.5=13. `tool-strategy.md` qualitative tiers (`trivial / medium / coding / hard`) become the JSON keys verbatim — the balancer is an extension, not a replacement. Daily-only (i) loses because Max-budget breach must trigger same-day; per-call adaptive (ii) oscillates with small N; **(iii) daily + named invalidation triggers** is SRE-correct. Triggers: UTC midnight, Max-weekly crosses 85% (matches `quota.py` OPEN threshold), >36h with Max>70% soft-shift, observed failure-rate >15% over 20 calls per tier, user command `/balancer recompute`. Hysteresis: 30-min minimum dwell per (tier, chosen_model). Race condition (3 sessions at 00:00:01 UTC): atomic write via tmp+rename, idempotent — same inputs ±2s give same decision. Objective:
```
score(model, task) = 0.5·quality_match − 0.3·norm_latency − 0.2·norm_budget_cost
```
`quality_match = min(1.0, intelligence_score(model) / required_score(task))` with `required_score`: trivial=12, medium=15, coding=17, hard=19, consilium_bio=19. The 0.5/0.3/0.2 weights swap to 0.5/0.1/0.4 under `budget_pressure` profile (auto-engaged when Max-weekly >85%) — which is exactly today's regime.

**Cost — the routing-economics insight that changes the game.** Claude Max counts every token the Lead generates AND every token Sonnet/Haiku consume in delegated `Agent` calls — same weekly bucket. But `mcp__pal__chat` and `codex exec` only debit Max for the **invocation payload** itself (few hundred tokens of args); the bulk of work (10K+ tokens of code generation) lands on a different bill — OpenAI API key (PAL) or flat ChatGPT subscription (Codex). On a user at 86% weekly with 2 days to reset, delegating coding to PAL gpt-5-mini is essentially free against Max. **Pattern B (Codex MCP) is not over-engineered, it's correctly-engineered — for day-N.** For today, PAL-only is 0-minute integration (already wired, tested in `/consilium` flow). Migration triggers: PAL→Codex CLI when PAL p95>8s sustained OR daily PAL calls >30 (Codex's flat ChatGPT-subscription cost amortizes better at volume); Codex CLI → Codex MCP when daily calls >50 OR Lead needs structured tool-use. Honest cost-model table — only `supervisor_session` is locally observable; Claude Max weekly, ChatGPT subscription, OpenAI paygo all marked `unknown / not-observable` for now. **Don't pretend the balancer can see Max-weekly directly** — it can only react to user's `/status` reports and to local latency signal.

**GPT-5.5 (PAL).** Hypothesis-level position (provider returned metadata oracle without full expert payload; continuation_id `11b01e3b-b3f1-4364-93d9-6e53d871332d` preserved for re-query). Aligns with Architect on Q2/Q7, with MLOps on Q3/Q4, with Cost on Q5. Two dissents: Q1 prefers pure-passive (the 800-tok probe SRE costed wasn't visible at hypothesis time) and Q6 prefers explicit latency-first today rather than Pareto-with-profile-switch (PAL judged Pareto as too much machinery for day-1). The SRE's costing addresses dissent #1; MLOps' `budget_pressure` profile addresses dissent #2 by making Pareto degenerate to latency-and-budget-dominated on day-1 with the same weights swap.

## Decision

Synthesized verdict — one answer per question:

| Q | Verdict | Vote pattern |
|---|---|---|
| **Q1 Bootstrap** | **Hybrid (C):** active probe `sonnet` + `haiku` once per day with prompt `"Reply with exactly: ok"` (~400 tok / model = 800 tok/day total). Opus never probed (Lead-only, never balancer-routed). Codex never probed (warm-up ~20s self-poisons signal). PAL/GPT passive via `listmodels` and real calls. | SRE leads; PAL dissent on token cost answered by SRE's 800-tok costing |
| **Q2 Topology** | **(A) Standalone script** `~/.claude/scripts/model_balancer.py` invoked from a new SessionStart hook entry. Writes `~/.claude/model_balancer.json` (atomic tmp+rename, mirroring `compact_advisor.py:104-113`). Reader = `memory_session_start.py` appends a `=== Model Routing ===` block (~5 lines, <500 chars) to `additionalContext`. No new MCP server. | Architect + PAL unanimous |
| **Q3 Schema** | **(b) Per-task-category routing** keyed by `tool-strategy.md` tiers (`trivial / medium / coding / hard / recon / consilium_bio / audit_external`). One JSON, hand-readable, no DSL. | MLOps + PAL unanimous |
| **Q4 Cadence** | **(iii) Daily + explicit invalidation triggers:** UTC-midnight recompute; Max-weekly>85% force-shift; failure-rate>15%/20-calls invalidates one tier; user `/balancer recompute`. 30-min hysteresis per (tier, model). Race-safe via atomic write + `model_balancer_decisions` SQLite UNIQUE on `decision_date`. | MLOps + PAL unanimous |
| **Q5 Codex depth** | **(C) PAL-only today.** Route coding-tier delegations through `mcp__pal__chat` with `model="gpt-5-mini"`. No new MCP server, no settings.json change. Pattern A (Codex subprocess) when PAL p95>8s OR daily PAL calls>30. Pattern B (Codex MCP) when calls>50 OR structured-tool-use needed. | Cost + PAL unanimous |
| **Q6 Objective** | **(iii) Pareto with profile switch.** `score = 0.5·quality_match − 0.3·norm_latency − 0.2·norm_budget_cost` under `normal` profile; `0.5 / 0.1 / 0.4` under `budget_pressure` (auto-engaged at Max-weekly>85%). Today's profile = `budget_pressure`, which behaves as PAL's "latency+budget dominated" while still respecting quality floor via `quality_match=min(1, intelligence_score/required_score)`. | MLOps leads; PAL's day-1 simplicity dissent addressed by `budget_pressure` profile auto-switching |
| **Q7 Failure** | **(B) Silent fallback** to `tool-strategy.md` defaults. Trigger: file missing / unparseable / stale (decision_date≠today) / contradicts non-negotiable (e.g., `/consilium` mapped to non-opus). Telemetry: row in `~/.claude/logs/model_balancer.jsonl` + `error_lesson` row in `agent_memory` for next-`/start` visibility. User-visible: 1 line in `additionalContext` only. No banner. No nag. Yesterday's JSON **not** stale-OK — model-latency volatility is the whole reason for the feature; using yesterday's data when today's is broken defeats the purpose. | Architect + PAL unanimous |

### Concrete JSON schema — what the balancer writes today

```json
{
  "schema_version": 1,
  "decision_date": "2026-05-12",
  "valid_until": "2026-05-13T00:00:00Z",
  "weight_profile": "budget_pressure",
  "inputs_snapshot": {
    "probe_2026-05-12T08:14:03Z": {
      "claude-sonnet-4-6": {"duration_ms": 2840, "tokens_in": 8, "tokens_out": 2},
      "claude-haiku-4-5":  {"duration_ms": 980,  "tokens_in": 8, "tokens_out": 2}
    },
    "claude_max_weekly_used_pct": 0.86,
    "openai_api_used_pct": "unknown",
    "chatgpt_subscription_used_pct": "unknown"
  },
  "rationale": "Claude infra ~3x baseline (sonnet probe 2.84s vs 7d baseline ~1.0s); Max 86% weekly; coding shifts to PAL gpt-5-mini, hard reasoning stays opus (irreplaceable), trivial stays haiku.",
  "routing": {
    "trivial":        {"provider": "anthropic", "model": "claude-haiku-4-5"},
    "recon":          {"provider": "anthropic", "model": "claude-haiku-4-5"},
    "medium":         {"provider": "openai-pal", "model": "gpt-5-mini"},
    "coding":         {"provider": "openai-pal", "model": "gpt-5-mini"},
    "hard":           {"provider": "anthropic", "model": "claude-opus-4-7"},
    "consilium_bio":  {"provider": "anthropic", "model": "claude-opus-4-7"},
    "audit_external": {"provider": "openai-pal", "model": "gpt-5.5"}
  },
  "fallback_ladder": {
    "gpt-5-mini": ["gpt-5-nano", "claude-haiku-4-5"],
    "gpt-5.5":    ["gpt-5-mini", "claude-sonnet-4-6"],
    "claude-sonnet-4-6": ["gpt-5-mini", "claude-haiku-4-5"],
    "claude-opus-4-7":   ["gpt-5.5"],
    "claude-haiku-4-5":  ["gpt-5-nano"]
  },
  "overrides_respected": [
    "command-frontmatter `model:` field",
    "/consilium → opus (feedback_consilium_lead_keep_opus.md)",
    "/lead → opus (feedback_consilium_lead_keep_opus.md)"
  ]
}
```

The key shift today: `medium` and `coding` flip from `claude-sonnet-4-6` to `gpt-5-mini` via PAL. Reason: Claude 3× slow + Max 86%. `hard` and `consilium_bio` stay on opus — quality floor non-negotiable. `audit_external` stays on gpt-5.5 (already the convention for PAL external review).

## Day-1 minimal ship plan (≤ 2 hours)

In strict order:

1. **Schema:** add `model_metrics` table to `rolling_memory.db` (5-line DDL per SRE's sketch). Keep the existing `agent_memory / supervisor_decisions / supervisor_quota` untouched.
2. **PostToolUse hook:** new `model_metric_capture.py` (~80 LOC, mirroring `compact_advisor.py` structure) — reads stdin tool-call event, extracts `duration_ms` and `num_turns` for `Agent` tool calls, writes a row to `model_metrics`. **Critical: persist both `duration_ms` AND `duration_ms / max(num_turns, 1)`** — the second is the actual model-time proxy per SRE's contamination finding. Env bypass: `CLAUDE_BOOSTER_SKIP_MODEL_BALANCER=1`.
3. **Balancer script:** `~/.claude/scripts/model_balancer.py` (~150 LOC):
   - Reads today's `~/.claude/model_balancer.json` (atomic-read pattern from `compact_advisor_inject.py`). If `decision_date == today`, exit 0.
   - Otherwise: query `model_metrics` for 7-day baselines, run active probe on sonnet + haiku, compute Pareto score per (tier, candidate model), select argmax, write JSON via tmp+rename.
   - Probe failure / DB unavailable → emit `event=bootstrap_failed`, write a JSON with `routing` copied from `tool-strategy.md` static map, `weight_profile="fallback"`.
4. **SessionStart wiring:** one new entry in `~/.claude/settings.json` `hooks.SessionStart` — `model_balancer.py` runs ONCE per session (not embedded into `memory_session_start.py` per Architect's SRP warning). Idempotent — re-running same day exits 0.
5. **Context injection:** extend `memory_session_start.py` with a **lazy-imported** `_load_routing_context()` (≤30 LOC, returns "" on any exception) that reads `~/.claude/model_balancer.json` and appends a 5-line block to `additionalContext`:
   ```
   === Model Routing — 2026-05-12 ===
   trivial/recon → haiku   |  coding/medium → PAL gpt-5-mini   |  hard/consilium → opus
   audit_external → PAL gpt-5.5
   Source: budget_pressure profile (Max 86%) + claude 3x slow probe.
   Overrides: explicit `model:` in command frontmatter wins.
   ```
6. **Rule extension:** add one paragraph to `~/.claude/rules/tool-strategy.md` (NOT a rewrite) — "When `~/.claude/model_balancer.json` exists and is fresh, treat it as the authoritative tier→model map. The qualitative pyramid below is the failure-mode default."
7. **Smoke test:** `tests/test_model_balancer.sh` — 6 assertions: schema migrate idempotent; bootstrap creates valid JSON; stale JSON triggers recompute; malformed JSON triggers silent fallback; invalidation trigger on Max>85% works; explicit `model:` override beats JSON.
8. **Manual seed for TODAY:** write the example JSON above to `~/.claude/model_balancer.json` by hand so today's session benefits immediately even before the script ships. The script will overwrite tomorrow.

That's day-1. No new MCP server. No Codex CLI integration. No quota dashboard.

## Day-N polish (NOT today)

- **Pattern A** (Codex subprocess) when PAL p95>8s sustained OR daily PAL calls>30.
- **Pattern B** (Codex MCP) when calls>50 OR Lead needs structured tool-use.
- **`telemetry_agent_health.py` extension** to surface "model_balancer fell back N times this week" alongside the other 5 signals.
- **Multi-provider quota dashboard** — fetch OpenAI usage API + parse `/status` output from Anthropic.
- **Adaptive within-day** — only if stuck-loop detector observes the daily-only mode causing missed-shift-window incidents 3+ weeks running.
- **Per-`assistant`-block timestamp deltas** (50 LOC parser) — only if `duration_ms / num_turns` proxy proves too coarse after 1 week of data.

## Rejected alternatives

| Rejected | Why |
|---|---|
| (a) Proportion-based JSON `{"opus":0.1,"sonnet":0.4,...}` | Uninterpretable at call site; Lead doesn't roll a die per Agent spawn |
| (c) Rule-DSL evaluator | Needs parser + tests; rules belong in *generator*, not consumer |
| Daily-only (Q4-i) | Ignores mid-day budget breach; user is at 86% with 2 days remaining |
| Per-call adaptive (Q4-ii) | Oscillates with N=3; flaps every 10 min |
| Active probe of Opus/Codex | Opus never balancer-routed; Codex 20s+ warm-up self-poisons signal |
| Pure latency-first today | Ignores Max budget; same outcome via Pareto `budget_pressure` profile (β=0.1, γ=0.4) but with quality floor still enforced |
| Pure cost-first | Burns coding quality (sonnet→nano); user's "no quality downgrade for coding" constraint blocks |
| Codex MCP Pattern B day-1 | 1–2h of work + restart + smoke tests on a 3×-slow infra day = wrong tradeoff |
| Embed balancer in `memory_session_start.py` | SessionStart already fires 3–6× per session (Architect's finding); doubles failure surface; violates SRP |
| Dynamic mutation of `tool-strategy.md` | Source-controlled file becomes mutable state; breaks git diff; conflicts with code-over-docs |
| Yesterday's JSON as stale-OK fallback | Model-latency volatility is the whole reason for the feature; using stale data when today's broken defeats the purpose |
| Active probe with prompt longer than 10 tokens | Prompt complexity contaminates latency signal; "Reply with exactly: ok" is the canonical micro-prompt |
| Persist daily decision in `rolling_memory.db` only (no JSON) | Loses hand-editable / `jq`-able / `cat`-able artifact; debuggability suffers |

## Risks

1. **`duration_ms` contamination** (SRE finding) — raw value is per-subprocess, not per-API-call. **Mitigation:** persist `duration_ms / max(num_turns, 1)` as the proxy and the raw value as audit; if the proxy still has too much variance after 1 week, add the 50-LOC per-block parser.
2. **Bootstrap probe could fail** on a network blip exactly at SessionStart → balancer falls back to static map → user gets degraded routing for the day. **Mitigation:** retry probe once with 2s backoff before flagging `bootstrap_failed`; silent-fallback contract preserves session usability.
3. **Race condition** if user runs `/start` simultaneously in 2 projects at UTC-midnight. **Mitigation:** atomic tmp+rename + SQLite UNIQUE on `decision_date` — same inputs within ±2s produce same decision; first writer wins, second is no-op.
4. **Explicit `model:` override silently disrespected** by lazy implementation. **Mitigation:** day-1 smoke test asserts `/consilium`-frontmatter model survives balancer routing. P0 bug if it doesn't.
5. **`mcp__pal__chat` provider returns metadata-only response** (observed today in this very consilium). **Mitigation:** `audit_external` tier falls back to `claude-opus-4-7` if PAL returns empty payload — quality floor preserved even when external channel degrades.
6. **Stuck-loop detector** may flag "model_balancer fell back" handovers as a recurring topic. **Mitigation:** balancer fallback events go to `agent_memory` with a stable category `claude-tooling/model_balancer` so the hash detector groups them under one signal instead of churning.
7. **Hidden Claude Max billing** — delegated `Agent(model="sonnet")` calls still count against Max. Only routing through PAL/Codex moves the bulk-of-work tokens to a different bill. If user expects "balancer fixes Max usage" but routing config still picks sonnet for coding, savings won't materialize. **Mitigation:** in `inputs_snapshot.rationale` always state explicitly which tiers were moved off-Claude this cycle.

## Implementation recommendations — ordered for today

1. Manual seed `~/.claude/model_balancer.json` (the JSON example above) — done in <5 min, gives immediate routing benefit while the rest of the plumbing is built.
2. Add `model_metrics` table DDL (5 lines SQL via `rolling_memory.py` schema bump).
3. Build `model_metric_capture.py` PostToolUse hook (paired Worker+Verifier, Sonnet).
4. Build `model_balancer.py` (paired Worker+Verifier, Sonnet) — most complex piece.
5. Wire SessionStart hook + extend `memory_session_start.py` (single Worker+Verifier, Sonnet).
6. Append routing paragraph to `tool-strategy.md` (Sonnet edit).
7. Write `tests/test_model_balancer.sh` (Verifier-style independent suite).
8. Audit pass: `/simplify` (3 lens agents Sonnet) + `/security-review` doesn't trigger (no auth/secrets touched).
9. Update README "What's new" + bump VERSION to 1.8.0.
10. Handover at session-end.

Est. effort: 4–6h paired work. Day-1 minimum (steps 1, 4, 5, 6, 8) is ~2h.

## Constraints satisfied

- ✅ Explicit `model:` directives in command files override balancer (overrides_respected block + Q7 constraint-violation event).
- ✅ `/consilium` and `/lead` stay on Opus per `feedback_consilium_lead_keep_opus.md`.
- ✅ JSON at `~/.claude/`-level, not per-project.
- ✅ Code-over-docs: balancer reads observed runtime latency from `model_metrics`, not from any cached belief.
- ✅ No silent quality downgrade for coding — `quality_match` cap is enforced even under `budget_pressure` profile.
- ✅ Stuck-loop detector friendly — fallback events grouped under one category.

## Open question (deferred to user)

The day-1 plan ships **without** active Codex CLI integration (Pattern A or B). Coding-tier work goes through `mcp__pal__chat` only. This depends on the user's **OpenAI API key being configured** in PAL's environment (verify before shipping). If only ChatGPT subscription auth exists and PAL has no OpenAI key, Pattern A (Codex subprocess) is the day-1 path instead, with one extra `Bash("codex exec ...")` helper. Cost engineer flagged this — flip recommendation to A if Dmitry confirms "no OpenAI API key, only ChatGPT subscription".

---

## ADDENDUM — 2026-05-12 user decision (overrides Q5)

**User verdict:** Pattern A (Codex subprocess via Bash) is the day-1 channel for **all parallel agentic work** — not PAL. Reason: Codex CLI auth is ChatGPT subscription (flat fee), so bulk-of-work tokens go to the subscription bucket — neither Claude Max nor OpenAI paygo are debited beyond the Lead's invocation payload. This is strictly cheaper than PAL (which charges OpenAI paygo per output token) for high-volume parallel delegation.

### Updated routing map (replaces the JSON example in §Decision)

```json
{
  "schema_version": 1,
  "decision_date": "2026-05-12",
  "valid_until": "2026-05-13T00:00:00Z",
  "weight_profile": "budget_pressure",
  "inputs_snapshot": {
    "probe_2026-05-12T?:?:?Z": {
      "claude-sonnet-4-6": {"duration_ms": null, "note": "to be populated by bootstrap"},
      "claude-haiku-4-5":  {"duration_ms": null, "note": "to be populated by bootstrap"}
    },
    "claude_max_weekly_used_pct": 0.86,
    "chatgpt_subscription_used_pct": "unknown / not-observable (flat fee)"
  },
  "rationale": "Claude infra ~3x baseline + Max 86%. ChatGPT subscription is flat-fee — parallel agentic work routed to Codex CLI subprocess via Bash. PAL reserved for /consilium external dissent only (where structured 7-step thinkdeep is the artifact).",
  "routing": {
    "trivial":        {"provider": "codex-cli", "model": "gpt-5-nano"},
    "recon":          {"provider": "codex-cli", "model": "gpt-5-nano"},
    "medium":         {"provider": "codex-cli", "model": "gpt-5-mini"},
    "coding":         {"provider": "codex-cli", "model": "gpt-5.1-codex"},
    "hard":           {"provider": "anthropic", "model": "claude-opus-4-7"},
    "consilium_bio":  {"provider": "anthropic", "model": "claude-opus-4-7"},
    "audit_external": {"provider": "pal",        "model": "gpt-5.5"}
  },
  "fallback_ladder": {
    "gpt-5-nano":     ["gpt-5-mini", "claude-haiku-4-5"],
    "gpt-5-mini":     ["gpt-5.1-codex", "claude-haiku-4-5"],
    "gpt-5.1-codex":  ["gpt-5-mini", "claude-sonnet-4-6"],
    "claude-opus-4-7":["gpt-5.5", "claude-sonnet-4-6"],
    "claude-haiku-4-5":["gpt-5-nano"]
  },
  "codex_invocation": {
    "binary": "/opt/homebrew/bin/codex",
    "command_template": "codex exec --skip-git-repo-check -m {model} -",
    "stdin_supported": true,
    "approval_policy": "on-failure",
    "sandbox_mode": "workspace-write",
    "config_file": "~/.codex/config.toml"
  },
  "overrides_respected": [
    "command-frontmatter `model:` field",
    "/consilium → opus (feedback_consilium_lead_keep_opus.md)",
    "/lead → opus (feedback_consilium_lead_keep_opus.md)"
  ]
}
```

### Why Codex CLI subprocess beats PAL today

| Path | Bulk-of-work tokens go to | Per-call latency | Day-1 effort |
|---|---|---|---|
| ~~PAL `mcp__pal__chat` gpt-5-mini~~ | OpenAI paygo (user's API key) | normal | 0 min |
| **Codex `codex exec -m gpt-5.1-codex`** | **ChatGPT subscription (flat)** | normal | 30 min (one helper script) |
| Codex MCP `codex mcp-server` | ChatGPT subscription (flat) | normal | 2h (settings.json + smoke + reload) |

ChatGPT subscription is the only billing bucket that doesn't scale per output token. For paired Worker+Verifier (2 parallel agents) running 8–10 times per session, the savings vs PAL paygo compound.

### Implementation deltas to the day-1 plan

The original §"Day-1 minimal ship plan" stays except:

- **Step 1 — manual seed JSON**: use the ADDENDUM JSON above (codex-cli routing), not the §Decision JSON.
- **NEW Step 3a — `~/.claude/scripts/codex_worker.sh`** (~30 LOC, Bash):
  ```bash
  #!/usr/bin/env bash
  # codex_worker.sh — pipe a prompt to codex exec, return stdout. Used by Lead
  # for parallel agentic delegation. Stdin is the prompt; argv: --model <MODEL>.
  set -euo pipefail
  MODEL="${1:-gpt-5-mini}"
  shift
  exec /opt/homebrew/bin/codex exec --skip-git-repo-check -m "$MODEL" -
  ```
  Lead invocation pattern (parallel Worker+Verifier):
  ```bash
  # In a single tool message — runs Worker and Verifier concurrently
  echo "<<<WORKER PROMPT>>>" | codex_worker.sh gpt-5.1-codex > /tmp/worker_out.txt
  echo "<<<VERIFIER PROMPT>>>" | codex_worker.sh gpt-5-mini  > /tmp/verifier_out.txt
  ```
  Two `Bash` tool calls in one message = native parallel execution by Claude Code.
- **Step 4 — `model_balancer.py`**: probe both Claude (sonnet, haiku) AND a quick Codex availability check (`codex login status` exit 0 in <2s). If Codex auth fails → fallback ladder routes coding back to Claude with warning event.
- **Step 5 — context injection**: routing block in `additionalContext` should explicitly state which provider runs each tier so Lead doesn't accidentally call `Agent(model="sonnet")` when the JSON says `codex-cli gpt-5.1-codex`.
- **Step 6 — `tool-strategy.md` paragraph**: include explicit invocation recipes for `codex_worker.sh` so the rule is self-documenting.
- **NEW Step 9a — paired-verification.md update**: §"Pattern A — параллельная пара" should add a Codex-flavoured variant (Lead spawns two parallel `Bash("codex_worker.sh ...")` calls). Independence is preserved by Codex sessions being stateless across invocations (no continuation_id, fresh sandbox per call).

### Risks specific to Pattern A

1. **No structured tool-use across the boundary.** Codex sees Lead's prompt as a string, not as Anthropic-style tool definitions. Worker MUST receive a self-contained prompt — Artifact Contract embedded inline, paths absolute, no "see attached" references. Risk: prompt-engineering quality determines output quality more sharply than with `Agent`.
2. **stdout parsing fragility.** Codex emits plain text on stdout (no `--output-format=json` flag per Cost engineer's RECON). Lead must read the whole text and synthesize — there's no structured "exit_code" / "tool_uses" / "files_edited" envelope. Mitigation: Verifier writes its acceptance test as a separate file (Lead reads the file, runs it via Bash), not as part of Codex stdout.
3. **Workspace-write sandbox surprises.** `~/.codex/config.toml` has `sandbox_mode="workspace-write"` + `approval_policy="on-failure"`. A Worker that tries to write outside the project root could trip approval mid-run, blocking the subprocess. Mitigation: each Worker prompt explicitly sets working directory via `-C <abs_path>` flag, and prompt explicitly forbids writes outside that path.
4. **No rolling_memory access by default for Codex.** `~/.codex/config.toml` shows rolling_memory MCP is registered — but Codex MCP boots fresh subprocess per call, paying ~500ms init. Mitigation: don't rely on rolling_memory in short Worker prompts; pass facts inline.
5. **Loss of `Agent`-tool ergonomics.** Lead loses the rich progress stream, intermediate `tool_uses` visibility, partial-result inspection. The Worker is a black box until subprocess exits. Mitigation: Workers must produce a single artifact at a known path; Verifier reads that path. Lead doesn't introspect the Worker's process.
6. **Codex agent loop opacity to claude-booster gates.** `phase_gate.py`, `delegate_gate.py`, `verify_gate.py` all run as PreToolUse hooks for Claude tool calls. They DO fire when Lead does `Bash("codex_worker.sh ...")` because that's still a Bash tool call. But they do NOT see what Codex does inside its subprocess. Risk: Codex Worker can edit code without claude-booster's `dep_guard.py` or `financial_dml_guard.py` checking. Mitigation: for high-blast-radius work (auth, migrations, financial DML), route to Claude Sonnet via `Agent`, not Codex. The routing JSON already has this — `coding` ≠ `migrations`; tier those separately if needed.

### What stays from §Decision

- Q1 hybrid probe (sonnet + haiku active, others passive) — unchanged.
- Q2 standalone-script topology — unchanged.
- Q3 per-task-category schema — unchanged (just different provider values).
- Q4 daily + invalidation triggers — unchanged.
- Q6 Pareto with `budget_pressure` profile — unchanged.
- Q7 silent fallback — unchanged.
- All "rejected alternatives" — still rejected for the same reasons.
- All risks #1–#7 from §Decision still apply on top of the Pattern A risks above.

### Runtime probe 2026-05-12 — TWO rounds, recovery

**Round 1 (wrong names, all failed).** Initial smoke used model names taken from `~/.claude/openai_models.json` and PAL catalog. All returned 400 "not supported when using Codex with a ChatGPT account":

| Probed (wrong names) | Result |
|---|---|
| `gpt-5-nano`, `gpt-5-mini`, `gpt-5-codex`, `gpt-5.1-codex` | **400** all four |
| `gpt-5.5` | **200**, 27,509 tokens |

**Round 2 (correct 2026 names via OpenAI docs check, all worked).** After consulting `developers.openai.com/codex/models` and the GitHub deprecation changelog (gpt-5.1-codex family deprecated April 2026):

| Probed (correct names) | Result | Tokens for "Reply: ok" | Notes |
|---|---|---|---|
| `gpt-5.5` | 200 | 27,509 | frontier |
| `gpt-5.4` | 200 | 26,150 | flagship |
| **`gpt-5.4-mini`** | 200 | 26,818 | **0.3× included-limits weight → 3.3× more calls per subscription window** |
| `gpt-5.3-codex` | 200 | 25,724 | coding-specialized |
| **`gpt-5.3-codex-spark`** | 200 | 25,359 | **ChatGPT Pro only, extra-fast** (its availability confirms user is on Pro, not Plus) |
| `gpt-5.2` | 200 | 27,373 | legacy (still routable) |
| `gpt-5.5 -c model_reasoning_effort="low"` | 200 | 28,517 | `reasoning_effort` does NOT reduce cost on trivial prompts — there's a ~25K reasoning floor regardless |

WSS `503 Service Unavailable` on `wss://chatgpt.com/backend-api/codex/responses` observed during 1 of 7 probes — Codex self-retries, but Lead must treat Codex calls as occasionally-failing-once-retry-succeeds.

**Implications:**
1. Tier hierarchy via Codex models **does exist** with correct names — Round 1's "tier collapse" was an artifact of testing deprecated identifiers. Schema v1 of the seed JSON (single-model fallback) was rewritten 2026-05-12T12:25Z to schema v2 with the proper 5-model tier mapping.
2. **`gpt-5.4-mini` is the routing workhorse** — 0.3× included-limits weight (per OpenAI docs) means medium/coding subagents get ~3.3× more capacity than gpt-5.5. This restores the parallel-capacity argument that the earlier "collapse to gpt-5.5 only" tradeoff had lost.
3. **`gpt-5.3-codex-spark` is the recon tier** — research-preview, Pro-only, near-instant. Replaces what would have been gpt-5-nano in the Round-1 plan.
4. **Reasoning floor ≈25K tokens per call regardless of model.** Smoke prompts all consume 25–28K tokens despite being one-word replies. `reasoning_effort` overrides don't bypass this — it's the agentic-loop overhead (system prompt + tool inventory + context init). On real Workers (10K+ token prompts with file edits), the marginal cost grows but the per-call baseline is fixed.
5. **Code-over-docs lesson** — the `openai_models.json` registry and PAL catalog described **capability** (what OpenAI has trained), not **entitlement** (what your auth tier can call). Always probe with the actual auth before designing routing tables. The fix here was: search OpenAI dev docs → discover correct 2026 names → re-probe → recover the tier hierarchy.

The seed JSON at `~/.claude/model_balancer.json` (schema v2) reflects Round 2. See `inputs_snapshot.probe_2026-05-12T12:23Z` for the per-model evidence and `parallel_budget.estimated_max_calls_5h_window` for capacity estimates (gpt-5.5 ≈6, gpt-5.4-mini ≈20, spark ≈30).

### PAL's residual role

PAL is **not eliminated** — it remains the channel for:
- `/consilium` GPT external dissent (the 7-step structured thinkdeep is the artifact; Codex CLI has no equivalent).
- `/audit` external code review (`mcp__pal__codereview` is paired with the `audit_external` tier in the routing JSON above).
- Ad-hoc second-opinion calls where the user explicitly wants the structured PAL workflow.

For raw parallel agentic execution (Worker, Verifier, Explore, paired-verification pairs) — Codex CLI via `codex_worker.sh`.
