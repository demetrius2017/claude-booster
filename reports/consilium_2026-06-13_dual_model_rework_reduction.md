# Consilium — Reducing returns-to-code (rework) after the Fable 5 block

**Date:** 2026-06-13
**Topic:** How to minimize returns-to-code (make first-pass code near-flawless) now that Fable 5 is blocked and the implementation pipeline went mono-provider gpt-5.5.
**Trigger:** User proposal — run gpt-5.5 + Opus 4.8 in parallel ("continuous consilium"), expand the тройка into a "6-ка" of role-specialized authors (security / fault-tolerance / correctness / …), then **merge their solutions by code**. Keep effort medium-or-lower ("only the essence").
**Format:** 4 bio-agents (3× Opus 4.8 via Agent, 1× gpt-5.5 via Codex CLI) + GPT-5.5 external (PAL thinkdeep, adversarial steelman).

---

## Verified Facts Brief (live code RECON — not memory)

1. **The тройка is already mono-provider gpt-5.5 — that is the regression.** `/go` routes Flow Designer via `get hard` and Worker+Verifier via `get coding`; both `coding` and `hard` are pinned to `codex-cli:gpt-5.5`. Opus 4.8 currently touches **nothing** in the pipeline — it is only the Lead/orchestrator. (`templates/commands/go.md:57,142`; `~/.claude/model_balancer.json`; `model_balancer.py:_PINNED_CATEGORIES`.)
2. **Two-thirds of the proposal already exists.** `/hackathon` = N parallel isolated Workers (git worktree) + deterministic Judge by exit-code score, **winner-take-all, no merge**. `/consilium` = role-bios + GPT, **opinion-only, no code merge, not continuous**.
3. **The missing third is the hard part:** dual-*provider* on the same coding task, and **cross-solution code-merge** — neither exists.
4. **Load-bearing axiom** (`paired-verification.md:226`): *Lead never issues PASS by reading code; the only input to PASS/FAIL is the test's exit code + stdout.* Verifier never sees Worker's code/prompt. A "merge by code" of divergent solutions structurally needs semantic judgment → collides with this axiom.
5. **Cost structure:** Opus 4.8 = per-token against Claude Max (~86% weekly used). gpt-5.5 via Codex = flat-fee, same intelligence tier (score 20). So the second model is cheap to add *if* placed where Opus runs once, not per-volume.

---

## Agent positions

| Agent (model) | Position | Key insight | KPI impact |
|---|---|---|---|
| **Orchestration architect** (Opus 4.8) | AGAINST 6-ка+merge | Independent authors produce *structural* divergence (different decomposition/state ownership) → conflict-on-every-hunk → 100% manual reconcile. That's **more** rework, not less. Merge has no syntactic arbiter. | Ship dual-provider at PFD + read-only review-lenses over **one** Worker's diff; Verifier exit-code stays sole PASS. |
| **Rework-KPI quality engineer** (Opus 4.8) | AGAINST | Root-cause split of rework: contract ambiguity ~40% + missed failure mode ~25% + integration mismatch ~20% + weak verification ~10% + **model capability ~5% (last)**. 6-ка attacks the 5% and *worsens* integration (#3) by adding a merge code-path. | Make PFD a **dual-reasoner adversarial pass** (gpt-5.5 drafts, Opus challenges); instrument `verifier_fail_count` by `defect_category`. |
| **Adversarial skeptic** (Opus 4.8) | AGAINST (theater) | "Merge by code" of role-authors is a fantasy (6 incompatible whole-cloth designs); "continuous" does no real work; "lower effort + flawless code" is self-contradictory. **One real kernel:** mono-provider gpt-5.5 = correlated W/V blind spot. | KILL 6-ка/merge/continuous/low-effort. KEEP only the diagnosis → split provider across Worker≠Verifier. |
| **Cost-latency economist** (gpt-5.5 / Codex) | AGAINST as default | 6-ка+merge ≈ **2.5–4× model-work** per task; merge/arbitration is serial (Opus = expensive). Break-even needs avg rework >1.4–3.5 cycles/task — only true for pathological classes (migrations/auth/concurrency/financial). | Gated **Opus pre-code design review** after Flow Designer, only for medium/high-risk; keep Worker+Verifier flat-fee gpt-5.5. |
| **GPT-5.5 external** (PAL, adversarial) | PARTIAL DISSENT | Consensus over-pruned. "No merge" too broad → reframe as **candidate tournament + safe cherry-pick of *tests* from losers**. "Design-only dual-provider" too strict → add **post-implementation cross-provider review** (design review can't catch impl-emergent defects) + **risk-gated escalation ladder (Tier 0–4)**, 2–3 candidates not 6. | Single canonical patch; candidate-selection over merge; test cherry-pick; dual-provider authoring only behind explicit risk triggers. |

**Convergence:** 5/5 reject "6 parallel role-authors → textual code-merge → continuous-on-every-task → lower-effort." 5/5 affirm the user's *diagnosis* (mono-provider regression; idle Opus is wasted leverage). GPT refined "never merge" → "never *textually* merge; candidate-tournament + test-only cherry-pick is safe."

---

## Decision

**Reject the 6-ка + code-merge + continuous-everywhere + lower-effort proposal as designed.** Adopt the underlying *intent* — put model diversity and the idle strong model where they actually cut rework — via four moves, ordered by ROI:

### SHIP-1 (highest ROI) — Dual-reasoner design gate
gpt-5.5 drafts the PFD (as today); **Opus 4.8 runs one adversarial challenge pass** against it before the Worker spawns — HAZOP-style: "which contract field is ambiguous? which failure mode is missing? which existing helper does this duplicate?" Disagreements resolve into the Artifact Contract *before code exists*. **Full effort here — never lower.** Attacks the ~65% root cause (contract + failure-mode) at the cheapest point. Opus runs **once** per task → flat-ish cost.

### SHIP-2 — Cross-provider verification
Make Worker and Verifier **different providers** (Worker gpt-5.5, Verifier Opus, or vice-versa). Breaks the correlated blind spot of a single model checking itself. **Strengthens** the axiom (PASS still = exit code), costs ~1× on the verify leg only.

### SHIP-3 — Post-implementation cross-provider diff review (GPT's addition)
After Verifier PASS, the *other* provider reviews the final diff read-only for minimality / integration / security. Findings feed Worker retry as directives — **never a merge**. Catches defects that only surface during implementation, which design review structurally cannot.

### SHIP-4 — Risk-gated escalation, reusing /hackathon (not a new 6-ка)
Most tasks stay тройка. Hard / high-blast tasks (migrations, auth, concurrency, financial, multi-service) escalate to **2–3 independent candidates via existing `/hackathon`** (winner-take-all, deterministic Judge) + the **one safe "merge": cherry-pick *tests* from losing candidates** into the winner's suite, then re-run. Code stays single-author; only tests union.

### Instrumentation (proves it's not theater)
Log per task: `worker_spawn_count`, `verifier_fail_count`, and per-fail `defect_category ∈ {contract_ambiguity, missed_failure_mode, integration_mismatch, weak_verification, capability}` (the W/V/A/E retry protocol already classifies). **Target:** mean `verifier_fail_count` ↓ and first-pass-clean rate ↑; specifically `contract_ambiguity + missed_failure_mode` fails drop while `capability` fails stay flat. If only `capability` moves, SHIP-1 is the wrong lever and we revisit.

---

## Rejected alternatives (with reasons)

- **6 role-specialized authors → merge by code.** Independent authors diverge structurally; merge has no deterministic arbiter → needs LLM judgment → violates the PASS axiom and manufactures new integration bugs (the #3 rework class). Winner-take-all (`/hackathon`) already solves "multiple attempts" correctly.
- **Continuous consilium on every task.** Pays a 2.5–4× tax on tasks that would pass first time; "continuous" collapses to "run consilium when there's genuine design disagreement" — which is just consilium used well. No new machinery warranted.
- **Effort medium-or-lower on design.** Self-defeating against the rework KPI: design is exactly where rework is prevented. Save ~30s at design → pay ~2 Worker retries (~3–4 min) downstream. Low effort belongs on mechanical grep/edit agents only.
- **Textual auto-merge of patches.** Replaced by candidate-tournament + test-only cherry-pick (SHIP-4).

## Risks

- **SHIP-2/3 add Opus per-token load** on the 86%-used weekly budget. Mitigate: cross-provider verify/review gated to coding tasks above a size/risk threshold, not every trivial edit.
- **Dual-reasoner design gate latency** (+1 Opus pass, ~30–60s) on every coding task. Mitigate: skip for mechanical/low-risk per existing Flow Designer skip criteria.
- **Instrumentation honesty:** if `defect_category` tagging is sloppy, the proof-metric lies. The W/V/A/E classification must be recorded at retry time, not reconstructed.

## Implementation recommendation (next session, not this one)

1. Add the **Opus adversarial challenge pass** to `templates/commands/go.md` Phase 1 (Flow Designer → challenge → reconcile-into-contract). Pin the challenge to `anthropic:opus` explicitly (it's the high-leverage Opus slot).
2. Add a `verifier_provider` override to the Phase 2 spawn so Verifier ≠ Worker provider.
3. Wire `defect_category` logging into the retry classifier; add a `/start` telemetry line for mean `verifier_fail_count`.
4. SHIP-4 is mostly config: document when Lead escalates to `/hackathon` + the test-cherry-pick step. No new command.

This hits the rework KPI by moving the strong model to **design and verification** (where defects are born and caught), not to redundant authorship (where it only creates merge conflicts) — while keeping the deterministic exit-code axiom intact.
