---
name: "Consilium 2026-04-18 — Impact ranking of 4 next-step candidates (post 15h plan)"
description: >
  3 Claude agents (Product strategist, Reliability engineer, Research/ML)
  + GPT-5.4 thinkdeep rank 4 candidates after the 15h Claude memory infra
  plan shipped. Key RECON finding: horizon broker-parity (the original
  target) is resolved in prod today in parallel, blocked on Dmitry's v3
  sign-off. Synthesis: C (sign-off + T0) + D (lite A/B) run in parallel
  as complementary paths. A and B defer until D reports.
type: consilium
scope: global
preserve: true
---

# Consilium 2026-04-18 — Impact ranking of 4 candidates

## Task context

After completing the 15h consilium_2026-04-18_memory_rearchitecture plan (Q1 schema v5 + Q3 --json + telemetry + Q2 verify_gate hook + R1+R2 startup-budget quick wins) in a single session, Dmitry asked to rank 4 next candidates by impact. The candidates:

A. **R5** — split `institutional.md` (5,495 tok, 54 rules, 11 categories) by error-taxonomy slug + build `paths:`-matching conditional-load pattern. Effort 3-4h. Saves 2-3k tokens/start for narrow-scope projects.

B. **v2 verify_gate** — evidence-to-tool_use correlation. Closes GPT audit HIGH #3 from today's Q2 review. Effort ~3h. Needs ≥10 hook fires as baseline.

C. **horizon v3 architectural sign-off + tranche T0 stabilize**. Engineering cost 0.5h. Blocker = Dmitry's decision on 5 v3 questions (deposit sourcing, unexplained_delta bucket, eToro capability matrix, legacy schema cleanup, +1).

D. **R4 lite profile A/B** — create `~/.claude/rules/_lite/` with just core.md + tool-strategy.md (~2,100 tok vs current ~14,000 always-loaded). Env var `CLAUDE_RULES_PROFILE=lite` swaps. Run 5 horizon sessions with lite; measure evidence count / hypothesis updates / repeated-question rate vs baseline. Empirical test of Framing 2 (attention saturation) hypothesis.

## Verified Facts Brief (RECON — code, not memory)

Key findings from Explore agent scan of current state:

- **`institutional.md`**: 99 lines, 17,585 bytes, 5,495 tok, 54 rules across 11 H2 categories. Only **1 of 9** rule files currently uses `paths:` frontmatter (`frontend-debug.md`). R5 must build the category-matching infra before it can deliver value.
- **verify_gate hook**: <5 decisions logged since ship, already had a **demonstrated false-positive today** (newest-block-wins failed when latest assistant text hadn't flushed to transcript JSONL at Bash-fire time; 4× tail-retry with 0/50/150/300ms backoff insufficient).
- **horizon (CRITICAL)**: 3 parity gaps (429 retry, sync_positions_from_broker atomicity, cash semantics) **RESOLVED IN PROD today in parallel** to Claude_Booster work. 17/17 production invariant PASS. KGC 497.3471 sh = broker 497.3471 sh exact match. NAV agrees across 4 UI surfaces within ±0.17%. Blocker is not engineering — Dmitry's sign-off on 5 v3 architectural questions per `horizon/reports/handover_2026-04-18_105730.md`.
- **core.md + tool-strategy.md**: 2,115 tok combined (vs claimed 1,700 — slight underestimate, still well below full 14k).
- **Telemetry baseline**: 30 session_summaries in 30d (cadence=thrashing), 9/10 handovers with evidence, 0 overdue tags, 0 superseded rows.

## Agent positions

| Agent | Ranking | Key insight | Primary metric |
|---|---|---|---|
| **Product strategist** | C > D > A > B | "Infra wasn't on critical path — horizon's 3 gaps resolved without it. The real blocker was a human decision that sat untouched." | Horizon apply-path manual interventions/week ≤ 1 |
| **Reliability engineer** | C > B > D > A | "17/17 invariants PASS is a snapshot, not a proof. Most likely incident: drift re-opening via 429 retry × sync atomicity edge case." | Hook fires ≥30 with FP rate <15% by 2026-05-02 |
| **Research/ML** | D > A > B > C | "Only D **discriminates** between Framing 1 and Framing 2. Without data, we're optimizing the wrong axis. N=10 paired design gives Cohen's d ≈ 0.8 detectable at p<0.05." | Rule violations/session delta (lite vs full) |
| **GPT-5.4 thinkdeep** | C+D bundled > B scoped > A | "C is the priority; D is the guardrail that keeps C honest. Pair them; don't treat as separate." | Decision-quality validation within 2 weeks |

## Points of agreement (3+ of 4 agents)

- **C in top 2**: all 4 agents (direct product leverage).
- **A last**: 3 of 4 (Product, Reliability, GPT). Research puts A at #2 only because it still partially attacks Framing 2, but its effect size is smaller than D's A/B.
- **B premature without data**: 3 of 4 (Reliability, Research, GPT). <5 fires is overfitting-to-noise risk.
- **Today's 15h infra was NOT on horizon's critical path**: 2 of 4 (Product explicitly, GPT implicitly). Reliability disagrees — argues the infra catches *future* drift that 17/17 PASS cannot prove isn't there.

## Points of disagreement

1. **Is C's sign-off blocker a reason to prioritize or deprioritize it?** Product: "C is #1 because it's the only path to real value and the engineering cost is 0.5h." Research: "C has zero epistemic content and is human-gated — put it at #4." GPT: "C is #1 but pair with minimal D."
2. **Is D worth 2h + 2 weeks elapsed?** Research: "Yes, Bayesian prior for skipping is weak — we commit to incremental R5/R6 without knowing which axis matters." Product: "Only if narrowly scoped; otherwise disguised procrastination." Reliability: "Cheap and bounded, but #3 because it doesn't directly reduce prod-incident probability."
3. **Retrospective on the 15h infra sequencing**: Product is harsh ("displacement activity while the real blocker sat untouched"). GPT is more charitable ("will earn its keep eventually"). Reliability declines to judge.

## Synthesis (Lead's decision)

### Ranking: **C > D > B-safeguard > A**, with C and D running in parallel

The panel converges on C as #1 but splits on #2. GPT's reframe dissolves the split: **C and D are complementary, not competing**. C has engineering cost 0.5h and is human-gated; D is the natural engineering use of the blocked slot.

### Concrete sequence (by effort, not elapsed time)

| Step | Time | Output | Why now |
|---|---|---|---|
| 1 | 30 min | Write a v3 sign-off doc listing the 5 open architectural questions with recommendations + trade-offs for each. Surface to Dmitry as forcing function. | Unblocks C. Also the only contribution engineering can make to a human-gated decision. |
| 2 | 1 h | Patch verify_gate newest-block-wins false-positive (aggressive retry OR require block within N lines of Bash fire). Don't bundle with v2 — this is a v1.5 bug fix on top of today's ship. | Unblocks the Q2 hook from polluting the telemetry signal Dmitry will rely on for 30-day review. |
| 3 | 2 h | Ship R4 lite profile infra (create `rules/_lite/`, env var switching, session-violation-count tracker). Pre-seed 10 paired horizon tasks (3 audit + 4 impl + 3 debug) on a shared spec doc. | Runs while Dmitry decides on v3. Produces the evidence that gates R5 later. |
| 4 | wait | Dmitry signs off on v3 questions → run horizon T0 tranche (0.5h). | C unblocks. |
| 5 | 2 weeks elapsed | Complete R4 A/B across 10 sessions (5 lite, 5 full), measure primary metric (violations/session), secondary (repeated-question rate, curl-before-claim rate, handover evidence density). | Answers Framing 1 vs Framing 2 question. |
| 6 | decide | Based on R4 verdict: if Framing 2 confirmed → ship R5 (3-4h). If rejected → ship rule-refresh-at-turn-40 mechanism instead. | Evidence-gated commit to the next layer. |
| **Later (deferred)** | — | v2 verify_gate — once ≥30 fires baseline accumulated. | Needs observational data not yet available. |

### Rejected alternatives

- **Do A (R5) immediately**: wastes 3-4h on an engineering bet that could be falsified by R4 for 2h.
- **Do B (v2) immediately**: tunes on <5 fires including a known false positive → overfitting guaranteed.
- **Do C alone and defer D**: loses the evidence-gate on whether 15h of prose-reduction infra matters. Bakes optimism into an unproven framing.
- **Do everything**: 10h+ of parallel work against a tired-lead schedule; the Reliability engineer's "worst decision" warning applies.

### 30-day KPIs (aggregated across agent picks)

1. **Horizon apply-path manual interventions ≤ 1/week** (Product strategist). Direct outcome measure independent of attribution.
2. **verify_gate fires ≥ 30, false-positive rate < 15%** by 2026-05-02 (Reliability). Validates Q2 hook actually bites.
3. **R4 primary metric: violations/session delta** between lite and full profile, with 95% CI and effect size (Research). Answers Framing question.
4. **Agent-health telemetry regression-free**: the 5 signals maintained at ✓ or explained when ⚠ (telemetry already ships — passive monitoring).

## Risks of the accepted plan

1. **Step 1 (sign-off doc) procrastinated**. Mitigation: calendar block for Dmitry; 30 min not 3h.
2. **D experiment gets contaminated**: horizon work resumes non-uniformly; treatment and control sessions drift in task shape. Mitigation: pair by task class, randomize order, 24h gap between paired sessions.
3. **v1.5 verify_gate patch creates a new bug**. Mitigation: write adversarial test BEFORE patching (TDD); keep scope to the newest-block timing fix, don't refactor.
4. **R4 shows Framing 2 is dominant → R5 becomes mandatory**. Expected; not a risk.
5. **R4 shows neither Framing explains the decay → new consilium required**. Acceptable — Research agent's fallback hypothesis (rule *recency* via mid-session re-injection) is a clear next test.

## Open decisions (need Dmitry's input)

1. **Do the sign-off doc tonight or tomorrow?** 30 min effort; the question is timing against Dmitry's bandwidth.
2. **Approve the exact 10-session R4 design** (N=5 lite + 5 full, paired by task class, primary metric = institutional rule violations per session, detection threshold ≥1.5 violation delta)?
3. **Verify_gate v1.5 fix scope**: retry-more OR require-block-position-≤-N-lines-from-Bash? Recommend: both, cheap to combine.
4. **Freeze window on Claude_Booster infra until R4 reports?** Argues against ad-hoc R5/R6/B starts for the next 2 weeks.

## Implementation priority (next 48h)

```
TODAY (1h total):
  - 30min: v3 sign-off doc (Step 1) — forcing function for C
  - 1h: verify_gate v1.5 patch (Step 2) — unblock the telemetry signal

NEXT SESSION (2h):
  - Ship R4 lite profile infra (Step 3)
  - Write 10-session spec (treatment/control assignment + metric scoring rubric)

2 WEEKS ELAPSED:
  - Collect 10 sessions
  - Write R4 verdict report
  - Decide A (R5) vs next-hypothesis based on verdict
```

## Panel continuation IDs

- Product strategist (Claude): agent `a037a1671821685d5`, 13.8s, 28k tokens
- Reliability engineer (Claude): agent `a7753061ad35ea4ac`, 16.3s, 28k tokens
- Research/ML engineer (Claude): agent `a535a8d1b343e92e5`, 23.4s, 28k tokens
- GPT-5.4 external (PAL thinkdeep): continuation `825a66aa-1cfa-43f8-a095-3d97ef74a951`

## Key insight

The 15h infra plan we shipped today was **not retrospectively on the critical path** for horizon — horizon's 3 gaps were resolved in parallel by the other track. The infra's value is now speculative and gated by R4's empirical test. **The honest read is that it will earn its keep only if R4 confirms Framing 2 matters**; otherwise it's a high-quality backlog item waiting for the next time a long-running project needs supersession enforcement. Do not compound the bet by shipping R5/R6 on the same unproven framing.

The consilium does NOT recommend reverting the infra — it shipped cleanly, passes audit, and is low-maintenance. But the next 30 days should be about **validating** it, not **extending** it.
