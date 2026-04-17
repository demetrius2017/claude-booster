---
name: "Audit 2026-04-18 — Startup token budget and the fatigue/laziness hypothesis"
description: "Measures the token cost of everything Claude loads before the user asks the first question. Tests the hypothesis that context bloat at /start is a contributing cause of the 'agent works at half force' pattern diagnosed in audit_2026-04-17."
type: audit
date: 2026-04-18
scope: global
preserve: true
category: Claude_Booster
---

# Audit 2026-04-18 — Startup token budget & the fatigue/laziness hypothesis

## Context

Yesterday's `audit_2026-04-17_agent_context_dysfunction.md` diagnosed three-part continuity failure (authority-without-supersession, rules-as-prose-not-mechanics, runtime reintroduces divergence). That diagnosis focused on **structural** causes. Dmitry's question at EOD today is the **quantitative** companion: how many tokens does the startup stack consume before any actual work begins, and is context bloat itself a contributing root cause of the "works at half force" pattern — agents skipping verification, repeating questions across sessions, treating broken architecture as canon?

This audit measures each load layer, totals them, compares to the Opus 4.7 1M context, and tests the fatigue hypothesis against three observable failure modes from yesterday's audit.

## Methodology

- **Measurement**: `wc -c` on every file the session loads at start, plus estimation of the Claude Code harness contribution (system prompt, tool schemas, auto-memory instructions) from the system-reminder blocks visible in this very session.
- **Token conversion**: `tokens ≈ chars / 3.2` for mixed ru/en markdown. English prose is closer to `/4`; Cyrillic and code-dense markdown are closer to `/3`. Ranges given where uncertainty matters.
- **Limitation**: tiktoken was unavailable in the environment; ratios are heuristic, accurate to ~10%. For orders-of-magnitude reasoning this is sufficient; do not cite these numbers to three significant figures.
- **Frame**: count is "before the first user turn's tool call". Tool results, agent outputs, and subsequent Read calls inflate the context further — that compounding is orthogonal to this audit.

## Layer-by-layer measurement

| Layer | Source | Chars | Tokens (est.) | Status | Avoidable? |
|---|---|---|---|---|---|
| A | `~/.claude/rules/*.md` (9 files) | 40,583 | ~12,700 | Always loaded | Partial |
| B | Project `.claude/CLAUDE.md` | 1,102 | ~345 | Always (in-repo) | No |
| C | `~/.claude/projects/*/memory/*.md` (3 files: MEMORY.md + 2 linked) | 6,338 | ~1,980 | Always loaded via auto-memory | Mostly yes — only the index is needed eagerly |
| D | `rolling_memory.py start-context` output | 1,980 | ~620 | On /start | No |
| E | `memory_session_start.py` SessionStart hook block | 1,991 | ~620 | Always (hook) | Partial — overlaps with D |
| F | On-demand /start reads (latest handover) | ~19,000 | ~5,940 | On /start | Partial — summarize instead of full-read |
| G | Deferred tool names list (no schemas) | ~1,425 | ~475 | Always | No |
| H | MCP server instructions (claude-in-chrome, pal, context7) | ~1,200 | ~400 | Always | No |
| I | Skills list (11 skills with descriptions) | ~2,400 | ~800 | Always | No |
| J | Built-in tool schemas loaded at start (Bash, Read, Edit, Grep, Glob, Write, Agent, Skill, Task, Schedule, etc., ~16 tools) | ~12,800 | ~4,250 | Always | No (core tools) |
| K | Claude Code harness system prompt + environment + auto-memory instructions | ~18,000 | ~6,000 | Always | No (harness) |

### Subtotals

- **Always-on baseline** (A+B+C+E+G+H+I+J+K): **~27,570 tokens** before /start's first action.
- **/start-specific load** (D+F): **~6,560 tokens** added by /start itself.
- **Post-/start context ceiling** (before first user task): **~34,130 tokens**.

For context: **Opus 4.7 1M budget → startup uses 3.4%**. That is small in absolute terms. The fatigue hypothesis is not about exhausting the budget — it is about **signal-to-noise** and **attention diffusion**, addressed below.

## The fatigue/laziness hypothesis

### Two framings, one diagnostic question

**Framing 1 — "It's not about tokens, it's about rules."** Yesterday's audit says the agent fails because rules are prose, not mechanics. The fix is to make enforcement executable (hooks, not prompts). On this framing, startup token count is almost irrelevant: even a 5k-token rule file won't be followed if the enforcement is cosmetic.

**Framing 2 — "It's about attention saturation at startup."** The agent receives 27k tokens of rules, memory, and scaffolding before a single user message. By turn 40, half of those rules are >30k tokens back in the context and compete for attention with fresh tool results, code snippets, and follow-up user messages. The agent rationally defaults to the path-of-least-retrieval: act on the visible (recent tool output), skip the invisible (rule buried at token 8,000 about running curl). The result looks like "laziness" but is actually **rational triage under context load**.

**Diagnostic.** The hypothesis is decidable: if laziness is driven by rule *quality* (prose vs mechanics), shrinking the rules won't help — a shorter prose rule is still prose. If it's driven by context *volume*, a smaller-but-identical ruleset should produce better adherence.

### Three specific failure modes from yesterday's audit, mapped against both framings

| Failure mode (horizon 39-day loop) | Framing 1 explanation | Framing 2 explanation | Testable prediction |
|---|---|---|---|
| 0 curl commands across 10 handovers | Rule says "verify with curl" — prose, not enforced, so agent ignores it. | "Verify with curl" is 1 line buried in ~12,700 tokens of rules; by the time agent completes a task, that line is deep in context and doesn't win attention vs the fresh tool result it just produced. | A session with only the core.md anti-loop rules (3k tokens) loaded would curl 3× more often than one with full rules/ (12k tokens). Cheap to test. |
| Same question asked in sessions 6/7/8 with different answers | Memory layer doesn't persist the answer as a first-class fact. | Memory *does* persist it — but with 6,600 tokens of memory already loaded, a specific answer to "source of truth" is a needle in a haystack; agent re-derives from scratch because retrieval is cheaper than search. | Consolidated MEMORY.md index (~80 tok) with on-demand linked-file reads should outperform always-loading all linked files. |
| Broken architecture treated as canon | No supersession mechanism on stale rules. | Plus: when `[UNDER REVIEW]` tags land in a 5,700-token institutional.md block, attention on them is diluted. Tag hygiene is a necessary but insufficient fix. | C2 (check_review_ages.py) today helps by surfacing stale tags at /start — a deliberate attention-grabbing mechanism that doesn't rely on the agent proactively scanning the rule file. |

**Both framings are probably true.** Framing 1 is necessary (a rule has to be enforceable). Framing 2 is also necessary (an enforced rule still has to be *salient* enough to trigger in the right moment). The audit_2026-04-17 bundle addresses Framing 1; it does not address Framing 2.

## Concrete findings

1. **A single file dominates the rules budget**: `institutional.md` = 17,077 chars = ~5,700 tokens = **42% of all rules**. It grows monotonically (35 rules now, was 20 two months ago). No rule ever retires today.
2. **Memory linked files auto-load eagerly.** `MEMORY.md` is 326 chars but its two linked files add 6,012 chars (~1,870 tok). The user asks a question about directive scope → the directive feedback file is useful; the rolling_memory backup file is dead weight for that turn. Today both load unconditionally.
3. **Layer E (SessionStart hook) and Layer D (start-context CLI) overlap**. Both surface recent sessions + directives + decisions. Combined = ~1,240 tokens, about half of which is duplicated information in different formats.
4. **On-demand /start reads are the biggest single shot.** The latest handover (~6,000 tokens) is fully read every /start. Summarizing to its `## Summary` + `## First step tomorrow` sections would cut this to ~800 tokens with no loss of actionable signal.
5. **The scenario + audit reports (~19,600 tokens combined)** are loaded in full today because the session needed them. If /start only read the latest handover (as commands.md prescribes), that load is opt-in — but once Claude reads them, they stay in context for the session, and today's session put them there. **By turn 10 today, context was already ~60k tokens.**
6. **Deferred tool schemas are a silent bloat vector.** Loaded 3 tool schemas this session (EnterPlanMode, ExitPlanMode, TaskCreate, plus TaskUpdate, AskUserQuestion) at ~800-2000 tokens each. Easy to forget a schema is resident once loaded. After ~10 ToolSearch invocations across a session, cumulative tool-schema footprint can exceed rules/.

## Recommendations (prioritized by impact/effort)

### Immediate (low effort, high signal)

- **R1: Print the startup token budget in /start output.** Add one line to `rolling_memory.py start-context` or a new script: `Startup context: ~27k tok always-on + ~6k tok /start. Budget headroom: 97%.` Makes bloat visible. Effort: 15 min. No risk. Addresses attention-to-own-stack.
- **R2: Summarize, don't re-read, the latest handover in /start.** Change `commands.md §start` step 1 to: "Read `## Summary` and `## First step tomorrow` sections only (offset/limit via Read tool). Read rest only if those sections cite a file needed for today's task." Saves ~5,000 tokens per /start. Effort: 10 min (commands.md edit).
- **R3: Lazy-load MEMORY.md linked files.** Today `MEMORY.md` auto-pulls linked memory files into context. Change auto-memory loader to read only `MEMORY.md` index; require an explicit Read to pull a specific memory file. Saves ~1,900 tokens always. Effort: Depends on whether this is harness-controlled or script-controlled — need to check `memory_session_start.py`. Medium risk (breaks "feedback applied automatically" guarantee).

### Near-term (medium effort, direct test of Framing 2)

- **R4: A/B experiment — rules_lite profile.** Create `~/.claude/rules/_lite/` with just `core.md` + `tool-strategy.md` (~1,700 tokens). Provide a `CLAUDE_RULES_PROFILE=lite` env var to swap. Run horizon's next 5 sessions with `lite`; measure curl count, hypothesis updates, repeated-question rate. If Framing 2 is right, lite should outperform full rules for investigation-heavy projects. Effort: 2h. Also validates yesterday's cross-project contamination concern.
- **R5: Split institutional.md by category.** Currently 35 rules in one file. Move to `institutional/<category>.md` (11 files matching error-taxonomy slugs). Load only categories relevant to current repo (via `paths:` frontmatter matching `backend/*.py` → trading + db-asyncpg; `*.tsx` → api-data + frontend). Saves ~3,000-4,000 tokens for projects that don't touch all domains. Effort: 3-4h. Risk: requires frontmatter discipline going forward.

### Structural (high effort, ships with memory re-architecture consilium)

- **R6: Summary-first retrieval for memory layer.** Rolling_memory returns `(summary, body_path)` tuples; summaries always load at start, full bodies load only when a tool uses their `body_path`. This is the "precompiled index" pattern. Pairs naturally with Q1 of today's pending consilium (confidence scoring + decay) — decayed memories load summary-only; high-confidence memories load full body. Effort: part of memory re-architecture, 8-12h if pursued. Most aligned with scenario #5 and framing 2.

## Verdict

**The startup budget (~27k always + 6k /start) is not dangerous in absolute terms.** It is 3.4% of Opus 4.7's 1M context, and current-Claude-generation handles 10x this fine in retrieval-heavy tasks. **But it is the wrong place for discipline scaffolding.** Instructions for how to behave at turn 40 should not sit at token 5,000 of turn 1 — by turn 40 they are buried under a tool-result avalanche.

**The fatigue/laziness pattern is overdetermined.** Framing 1 (rules as prose) and Framing 2 (attention saturation) both contribute. Yesterday's audit fixes Framing 1. This audit's R4 (lite profile A/B) is the cheapest way to test whether Framing 2 matters enough to re-architect.

**Pragmatic path forward:** ship R1+R2 this session (15 min + 10 min = 25 min, savings ~5,000 tokens per /start). Queue R4 as experimental and R5+R6 to bundle with the in-flight consilium's verdict on memory re-architecture (Q1). Do not rewrite rules/ based on this audit alone; wait for the consilium and the A/B.

## Self-audit clause

This audit's measurements are valid for 2026-04-18 file sizes; they drift as rules/memory grow. **Re-measure quarterly** (`scripts/check_review_ages.py` can be extended or a companion `check_token_budget.py` added). If total always-on exceeds **40,000 tokens**, escalate — that's the point at which Framing 2 becomes dominant over Framing 1 on current-generation models. Token-conversion ratios should be re-validated with tiktoken once available (`pip install tiktoken` in whatever Python env /start uses).

## Open questions for Dmitry

1. Ship R1 + R2 in this session, before the handover?
2. Schedule R4 (lite profile A/B test) for horizon next week? Needs deploy-free period.
3. Treat R5 + R6 as inputs to the pending consilium (`consilium_2026-04-18_memory_rearchitecture.md`), or as separate follow-up?
