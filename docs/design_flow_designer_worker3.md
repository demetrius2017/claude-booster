# Flow Designer: Structured Checklist + Adversarial Challenge

**Worker 3 — Hackathon Submission**
**Date:** 2026-05-16

---

## Executive Summary

Claude's temporal blindness is not a knowledge gap — it is a **reasoning mode gap**. Claude knows that inventory depletes over time; it simply does not activate that knowledge unless forced. The fix is not more information; it is a **mandatory reasoning protocol** that blocks the Worker from writing code until it has demonstrated processual thinking — then an independent Challenger that stress-tests the Worker's temporal model before a single line of implementation begins.

This approach is cheaper, simpler, and more composable than a separate pre-planning agent because it reuses the existing Worker+Verifier pair and adds only one lightweight role (Challenger) with a hard iteration cap.

---

## 1. Process Thinking Checklist (PTC)

The Worker MUST answer these questions in writing — in a structured block within the Artifact Contract's response — before producing any code. The questions are domain-agnostic; they force temporal/branching/derived-state reasoning regardless of whether the task is inventory, broker, rebalancing, or anything else.

### The Seven Questions

```markdown
## Process Thinking Checklist

### Q1. Timeline
What is the temporal span of this operation? What happens between trigger and completion?
Draw the timeline: [Event A] → [Interval: what changes during wait] → [Event B] → ...

### Q2. State Evolution
Which state variables change DURING the operation (not just at start and end)?
For each: what drives the change? What is the rate/curve? Is it monotonic or can it reverse?

### Q3. Branching Scenarios
What are ALL possible outcomes at each decision point?
For each branch: what is the probability (rough)? What downstream state does it produce?
Format: Decision Point → {Branch A (likely): consequence, Branch B (unlikely but possible): consequence, ...}

### Q4. Derived State Cascade
When the primary state changes, what DERIVED values must be recalculated?
Map the propagation: X changes → Y depends on X → Z depends on Y → ...
Which of these recalculations are immediate? Which are deferred/async?

### Q5. Failure Modes & Partial States
What happens if the operation fails MIDWAY? What partial state exists?
Is the partial state observable by other components? Can they handle it?
What is the recovery path from each partial state?

### Q6. Concurrency & Races
Can another operation on the same state start before this one completes?
If yes: what is the interaction? Does ordering matter? Is there a lock/fence?

### Q7. Projection vs. Snapshot
Am I using any CURRENT value where I should be projecting a FUTURE value?
For each input: will it still be accurate at the time the result is consumed?
Specifically: if there is a delay between calculation and effect, does the delay
change the validity of any assumption?
```

### Why These Seven

| Question | Failure class it prevents |
|----------|--------------------------|
| Q1 Timeline | "Flat snapshot" — treating a process as instantaneous |
| Q2 State Evolution | Ignoring depletion/accumulation curves |
| Q3 Branching | Happy-path-only code that has no error/partial handling |
| Q4 Derived Cascade | Fixing one value while leaving derived values stale |
| Q5 Failure Modes | Unhandled partial-commit states |
| Q6 Concurrency | Race conditions, double-execution |
| Q7 Projection vs. Snapshot | The core bug: using `now` values for `future` decisions |

---

## 2. Checklist Enforcement — Preventing Shallow Answers

The Challenger agent exists precisely for this purpose, but even before the Challenger sees the answers, the **Lead** applies a mechanical quality gate:

### Minimum Depth Criteria (Lead checks, no LLM judgment needed)

Each answer MUST contain:
- **Q1:** At least 2 time-points with explicit intervals (not just "start → end")
- **Q2:** At least 1 named state variable with its change driver
- **Q3:** At least 2 branches per decision point (if only one → "no branching" must be explicitly justified)
- **Q4:** At least 1 propagation chain of depth >= 2 (X → Y → Z)
- **Q5:** At least 1 named partial state with recovery path
- **Q6:** Explicit "no concurrency" with justification, OR race description
- **Q7:** At least 1 specific input tested against the projection criterion

### Enforcement Mechanism

The PTC answers are injected into the Worker's response as a **required preamble**. The Worker's prompt includes:

```
Before writing ANY code, you MUST complete the Process Thinking Checklist below.
Your answers will be reviewed by an independent Challenger agent before you proceed.
Do NOT produce code until the Challenge loop completes.

[PTC questions here]

Write your answers in a ```process-thinking``` fenced block at the TOP of your response.
If you cannot answer a question, write "NOT APPLICABLE: <one-sentence reason>".
One-word or one-line answers are insufficient — each answer requires evidence of reasoning.
```

If the Worker's response does not contain the `process-thinking` block, or contains placeholder/shallow answers, Lead **rejects without classification** — this is not a W/V/A/E failure, it is a protocol violation. Worker is re-spawned with an explicit warning.

---

## 3. Challenger Agent Prompt

The Challenger is a NEW role — lightweight, adversarial, time-bounded. It receives the Worker's PTC answers (NOT the Worker's code, because no code exists yet) and attacks them.

### Challenger System Prompt

```markdown
You are an Adversarial Process Challenger. Your job is to find temporal/process 
blind spots in the Worker's reasoning — NOT to review code (none exists yet).

You receive:
1. The Artifact Contract (objective, constraints, scope)
2. The Worker's Process Thinking Checklist answers

Your mandate:
- For each of the 7 answers, identify what the Worker MISSED or UNDERESTIMATED
- Generate "what about..." scenarios that stress-test the Worker's temporal model
- Focus on: hidden delays, feedback loops, partial states the Worker didn't consider,
  rate changes, external events during the operation, derived values going stale
- Each challenge must be SPECIFIC and ACTIONABLE (not "think harder about X")

Output format:
```challenge-report
## Challenges

### C1. [Which PTC question this challenges: Q1-Q7]
**Attack:** <specific scenario the Worker didn't account for>
**Why it matters:** <what breaks if this scenario occurs>
**Required response:** <what the Worker must address — a specific question to answer>

### C2. ...
(minimum 3, maximum 7 challenges)

## Verdict
- PASS: Worker's process model is adequate (all gaps are non-critical)
- REVISE: Worker must address challenges C1, C3, C5 before coding
  (list which specific challenges require revision)
```

Rules:
- You are NOT the Verifier. You do not write tests. You attack reasoning.
- You do NOT see the Worker's implementation strategy — only their process model.
- "PASS" means: the Worker demonstrated they understand the temporal dynamics,
  even if their code might still have bugs (that's what the Verifier catches).
- "REVISE" means: there is a blind spot in the Worker's UNDERSTANDING that will
  certainly produce a defective implementation if not addressed first.
- Be adversarial but FAIR — don't invent implausible edge cases. Attack the 80%
  scenarios the Worker missed, not the 0.1% theoretical ones.
- If the task is genuinely simple (no temporal dynamics), say PASS with a note.
```

### Challenger Model Tier

**Sonnet 4.6** — same tier as the Worker. Rationale:
- The Challenger does not need to SOLVE the problem, only to ATTACK the model
- Attacking is computationally cheaper than solving (asymmetric difficulty)
- Using the same tier ensures the challenges are calibrated to what the Worker should have caught
- If Challenger were Opus, it would generate challenges the Worker literally cannot address at its tier

Exception: if the task is classified as `high_blast_radius` (auth, broker, payments, migrations), escalate Challenger to **Opus** — the cost of missing a temporal blind spot is asymmetrically high.

---

## 4. Challenge-Fix Loop

```
Worker answers PTC → Lead validates depth → Challenger attacks → Worker revises → [loop or proceed]
```

### Loop Rules

1. **Max iterations: 2** (Worker answers → Challenger → Worker revises → Challenger re-evaluates → done)
2. After iteration 2, if Challenger still says REVISE, **escalate to Lead** — Lead reads both the Worker's answers and the Challenger's attacks, then:
   - If Lead agrees the blind spot is real: re-frame the Artifact Contract to make the temporal requirement explicit, re-spawn Worker (fresh context) with the revised Contract
   - If Lead judges the Challenger is being over-zealous: override with explicit note in handover, proceed to code
3. Worker receives ONLY the specific challenges marked "Required response" — not the full Challenger report (prevents information overload)
4. Revision is ADDITIVE — Worker appends to their PTC answers, does not rewrite from scratch

### Sequence Diagram

```
Lead                    Worker                  Challenger
 |                        |                        |
 |--- Spawn Worker ------>|                        |
 |    (Artifact Contract  |                        |
 |     + PTC questions)   |                        |
 |                        |                        |
 |<-- PTC Answers --------|                        |
 |                        |                        |
 |--- Validate depth ---->|                        |
 |    (mechanical gate)   |                        |
 |                        |                        |
 |--- Spawn Challenger ---|----------------------->|
 |    (Contract + PTC     |                        |
 |     answers only)      |                        |
 |                        |                        |
 |<----- Challenge Report ---|---------------------|
 |                        |                        |
 |--- IF REVISE: forward  |                        |
 |    challenges to Worker |                        |
 |                        |                        |
 |--- Spawn Worker ------>|                        |
 |    (revision prompt    |                        |
 |     + specific Cx)     |                        |
 |                        |                        |
 |<-- Revised answers ----|                        |
 |                        |                        |
 |--- Re-Challenger? ---->|  (only if iter < 2)    |
 |                        |                        |
 |--- PASS or escalate    |                        |
 |                        |                        |
 |--- NOW: "Write code"-->|                        |
 |    (PTC answers become |                        |
 |     part of context)   |                        |
```

### Key Design Decisions

- **Worker sees its own PTC answers when coding** — they become working memory, not discarded scaffolding
- **Challenger never sees code** — it operates only on the process model
- **The loop is PRE-implementation** — no code is wasted on revisions
- **Worker is re-spawned for code** (not continued from PTC) to avoid token bloat from the challenge loop

---

## 5. Verifier Integration

The Verifier already exists in paired-verification.md. The PTC answers feed into it via an **enriched Artifact Contract**.

### How PTC Answers Become Test Properties

After the challenge loop completes (PASS), Lead extracts **testable temporal assertions** from the PTC answers and adds them to the Artifact Contract's `Acceptance emphasis:` field:

```markdown
Acceptance emphasis:
- [Standard acceptance criteria from task]
- [TEMPORAL] Worker identified state evolution: stock depletes at rate R during 
  lead time T. Verify that calculation uses projected_stock(T), not current_stock.
- [BRANCHING] Worker identified partial-fill branch. Verify that partial-fill 
  state is handled (not just full-fill and reject).
- [CASCADE] Worker identified NAV depends on positions. Verify that after 
  position change, NAV is recalculated before being exposed to consumers.
```

### Verifier's Enhanced Mandate

The standard Verifier mandate (from paired-verification.md) is unchanged. But the enriched `Acceptance emphasis:` naturally guides the Verifier to produce tests that cover temporal properties.

The Verifier does NOT see the full PTC answers — only the extracted `[TEMPORAL]`, `[BRANCHING]`, `[CASCADE]` assertions that Lead promotes to the Contract. This maintains the Verifier's independence (it tests observable behavior, not the Worker's internal model).

### Example: Verifier Test for Broker Execution

```bash
#!/bin/bash
set -e

# Standard: function exists and is callable
python3 -c "from broker.execution import handle_fill; assert callable(handle_fill)"

# [TEMPORAL] Verify timeout handling exists (Q1 timeline: order can timeout)
python3 -c "
from broker.execution import handle_fill
import types
# Must accept timeout scenario
sig = inspect.signature(handle_fill)
# or: check that timeout branch produces valid state
result = handle_fill(mock_timeout_event())
assert result.status in ('timeout', 'cancelled'), f'Timeout not handled: {result.status}'
"

# [BRANCHING] Verify partial-fill produces valid partial state
python3 -c "
from broker.execution import handle_fill
result = handle_fill(mock_partial_fill(filled=50, total=100))
assert result.filled_qty == 50
assert result.remaining_qty == 50
assert result.status == 'partial'
# Derived state: NAV must reflect partial position
assert result.nav_impact != 0, 'Partial fill must affect NAV'
"

# [CASCADE] After position change, derived values recalculated
python3 -c "
from broker.portfolio import Portfolio
p = Portfolio(test_positions())
old_nav = p.nav
p.apply_fill(mock_partial_fill(filled=50, total=100))
new_nav = p.nav
assert old_nav != new_nav, 'NAV must update after position change'
# Verify reconcile state also updated
assert p.reconcile_state.is_dirty == True, 'Reconcile must be flagged after fill'
"

echo "ALL TEMPORAL ASSERTIONS PASSED"
```

---

## 6. Process Flow Document vs. Checklist-as-Artifact

**Decision: The PTC answers ARE the artifact. No separate PFD.**

Rationale:
- A separate "Process Flow Document" creates maintenance burden — it drifts from code
- The PTC answers live in the Worker's response, which is ephemeral (session-scoped)
- If persistence is needed (complex multi-session feature), the PTC answers can be saved as `state/process_models/{task_id}_ptc.md` — but this is optional, not default
- The testable temporal assertions ARE persisted — in the Artifact Contract's `Acceptance emphasis:` field, which the Verifier tests against

### When to persist the PTC

- Feature spans multiple sessions (handover scenario) → save PTC to `state/process_models/`
- Architecture-level change (≥3 feeds in dep_manifest) → save alongside Architecture Auditor input
- Incident response where the process model itself is the finding → promote to institutional knowledge

---

## 7. Examples — Broker Execution Case

### BAD PTC Answers (would trigger REVISE)

```process-thinking
## Q1. Timeline
Order is placed, fill comes back.

## Q2. State Evolution
Position changes when fill arrives.

## Q3. Branching Scenarios
Fill or reject.

## Q4. Derived State Cascade
NAV changes.

## Q5. Failure Modes & Partial States
Order might fail.

## Q6. Concurrency & Races
N/A

## Q7. Projection vs. Snapshot
N/A
```

**Why this fails the depth gate:**
- Q1: Only 2 points, no intervals, no "what happens DURING the wait"
- Q2: No change driver, no rate
- Q3: Only 2 branches, missing partial-fill, timeout, amendment
- Q4: Single-hop cascade (NAV), no depth-2 propagation
- Q5: No specific partial state named, no recovery path
- Q6: No justification for N/A — what about multiple orders?
- Q7: No specific input tested

### GOOD PTC Answers (would likely pass Challenger)

```process-thinking
## Q1. Timeline
[Order placed t=0] → [In-flight: 0-30s for market orders, 0-∞ for limits] → 
[Fill event OR timeout at t=broker_timeout OR cancel by user OR exchange EOD]

During in-flight interval:
- Market price moves (can make limit order fillable/unfillable)
- Other orders on same symbol may fill (changing buying power)
- User may request cancel (race with fill)
- Broker may send partial fill (splitting the order)
- Exchange may halt trading (freezing order indefinitely)

## Q2. State Evolution
| Variable | During in-flight | Driver | Monotonic? |
|----------|-----------------|--------|------------|
| position.pending_qty | Increases at place, decreases at fill/cancel | Fill events | No (cancel reverses) |
| buying_power | Decreases at place (reserved), restored at cancel | Order lifecycle | No |
| position.realized_qty | Increases on each fill event | Partial fills | Yes (monotonic up) |
| position.avg_cost | Recalculated on each fill (weighted average) | Fill price × qty | No |
| NAV | Recalculated after any position change | position + market price | No |

## Q3. Branching Scenarios
Decision Point: Fill Event Type
- Full fill (common, ~70%): position += full qty, pending = 0, order closed
- Partial fill (uncommon, ~15%): position += partial qty, pending decremented, order stays open
  → Creates intermediate state: some filled, some pending
  → May get MORE partial fills before completion
- Reject (rare, ~5%): buying power restored, pending cleared, error logged
  → Must distinguish "reject at exchange" vs "reject at broker" (different error codes)
- Timeout (rare, ~5%): order auto-cancelled after broker_timeout, buying power restored
  → Race condition: fill may arrive AFTER timeout message (out-of-order messages)
- Amendment (very rare, ~3%): quantity/price changed mid-flight
  → Old pending replaced by new pending; if fill arrives for old qty, reconciliation needed
- Exchange halt (~2%): order frozen, neither fills nor cancels
  → Must NOT treat as timeout; must NOT free buying power

## Q4. Derived State Cascade
Fill event arrives (partial, qty=50 @ $10.50):
1. position.realized_qty: 0 → 50 (immediate)
2. position.avg_cost: recalculated as weighted avg (immediate)
3. position.pending_qty: 100 → 50 (immediate)
4. buying_power: partially restored for unfilled portion? NO — reserved until order fully resolves
5. NAV: recalculated = sum(position.qty × market_price) for all positions (immediate but ASYNC — must not read stale)
6. reconcile_ledger: new entry for this fill (deferred, batch)
7. P&L: recalculated from avg_cost vs current price (depends on NAV completing first)
8. risk_exposure: recalculated from new position size (depends on P&L? No — parallel to P&L)

Propagation depth: fill → position → NAV → P&L (depth 3)
Propagation depth: fill → position → risk_exposure (depth 2, parallel to above)
Ordering constraint: NAV must complete before P&L reads it

## Q5. Failure Modes & Partial States
| Partial State | When | Observable by | Recovery |
|---------------|------|---------------|----------|
| Fill received, position updated, NAV not yet recalculated | Between steps 1-5 in Q4 | Any reader of NAV | Must use versioned/timestamped NAV; stale NAV flagged |
| Order timed out locally, but fill arrives from broker | Race between timeout handler and fill handler | Reconciliation engine | Fill handler must check if order still exists; if cancelled, create a "late fill" reversal |
| Multiple partial fills arrive simultaneously | High-volume market | Position calculator | Must serialize fills or use atomic increment |
| Amendment sent, old fill arrives | Between amendment and ack | Order state machine | Match fill to order version; reject fills for old version |

## Q6. Concurrency & Races
YES — multiple concurrent scenarios:
1. Two fills for same order arrive in parallel (split by exchange): must serialize position updates
2. Cancel request and fill arrive simultaneously: fill wins (exchange already executed), cancel is late
3. Two orders on same symbol: buying power must be checked atomically at place time
4. NAV recalculation while new fill arrives: must not read half-updated position
   Lock strategy: per-order lock for fill processing; per-portfolio lock for NAV calc; 
   or: event-sourcing with ordered log (preferred — no locks, eventual consistency)

## Q7. Projection vs. Snapshot
| Input | Current or Projected? | Risk if wrong |
|-------|----------------------|---------------|
| buying_power at order placement | Current — correct for placement | Low |
| position.qty when calculating margin | Current — but stale if fill in-flight | HIGH — may over-allocate |
| market_price for NAV | Current — but latency means it's already past | MEDIUM — accepted, mark timestamp |
| pending_qty for risk calc | Current snapshot — but multiple fills may reduce it before calc completes | MEDIUM — use versioned read |

KEY INSIGHT: When checking "can user place new order?", buying_power must account 
for ALL in-flight orders (reserved), not just current settled position. This is the 
"projection vs snapshot" trap: buying_power NOW already accounts for reservations, 
but if we read position.qty instead of available_buying_power, we double-count.
```

### Challenger's Response to Good Answers

```challenge-report
## Challenges

### C1. [Q4 — Derived State Cascade]
**Attack:** You describe NAV recalculation as "immediate but ASYNC" — but what
serializes NAV completion before P&L reads it? If both are triggered by the same
fill event, and there's no explicit ordering mechanism, P&L may read stale NAV.
**Why it matters:** Stale P&L cascades to risk-exposure, which cascades to margin
calls. Wrong margin call = real financial consequence.
**Required response:** Specify the ordering mechanism: event-sourced log with
causal ordering? Explicit await? Versioned reads with retry?

### C2. [Q5 — Failure Modes]
**Attack:** "Late fill" reversal — you mention creating one, but what does that
mean for NAV and P&L that already incorporated the timeout cancellation? Do you
unwind? Or is the reversal forward-only (credit back)?
**Why it matters:** If we unwind, we need to re-run the cascade. If forward-only,
NAV was temporarily wrong and any decisions made during that window (other orders,
margin checks) used incorrect data.
**Required response:** Choose the reversal strategy and describe its blast radius.

### C3. [Q3 — Branching + Q6 — Concurrency]
**Attack:** You mention "fill wins over cancel" but don't address: what if the
cancel response arrives FIRST (out-of-order messages on the wire)? The system sees
"cancelled" then receives a fill for a "cancelled" order.
**Why it matters:** If order state machine transitions to CANCELLED and then gets
a fill, most state machines will reject the fill as invalid. But the exchange
already executed — you own the shares. Reconciliation will diverge.
**Required response:** Define the state machine transition rules: can CANCELLED
receive a fill? Or must you hold the CANCELLING state until broker confirms?

## Verdict
REVISE: Worker must address C1, C2, C3 before coding.
```

---

## 8. Skip Criteria — When PTC is NOT Needed

The PTC adds overhead. It should be SKIPPED when the task has **no temporal dimension**:

### Skip When ALL Are True

1. **No time passes** between trigger and completion (pure transformation: input → output, synchronous, no waiting)
2. **No branching** beyond trivial error/success (no partial states)
3. **No derived state** affected beyond the immediate output
4. **No concurrency** possible (single-threaded, no external actors)
5. **Single snapshot is sufficient** — all inputs are valid at consumption time

### Concrete Examples of SKIP

- Formatting a string
- Adding a column to a display table
- Refactoring an internal function (same behavior, different structure)
- CSS/styling changes
- Adding a new endpoint that reads data (stateless query)
- Writing a pure utility function (sort, filter, transform)

### Concrete Examples of DO NOT SKIP

- Anything with `async`/`await`/promises/callbacks
- Anything involving external API calls (network latency = time passes)
- Anything touching state that other components read
- Anything with retry/timeout logic
- Anything involving queues, events, or pub/sub
- Anything financial (money has implicit temporal dynamics: interest, settlement, clearing)
- Anything with caching (cache = stale snapshot by definition)

### Heuristic for the Lead

> "If the Worker could write this function as a pure function with no side effects
> and no external dependencies, skip PTC. If it MUST interact with time, state, or
> external actors, require PTC."

---

## 9. Cost Analysis

### Token Cost Breakdown (per task)

| Component | Tokens (estimated) | Cost at Sonnet pricing |
|-----------|-------------------|----------------------|
| PTC in Worker prompt | +400 tokens (questions embedded) | Negligible |
| Worker PTC answers | +800-2000 tokens (good answers) | ~$0.006-0.015 |
| Challenger spawn | ~3000 tokens (contract + answers + system prompt) | ~$0.022 |
| Challenger response | ~1000 tokens (challenge report) | ~$0.008 |
| Worker revision (if needed) | ~1000 tokens (additive answers) | ~$0.008 |
| Total overhead (no revision) | ~4200-5400 tokens | ~$0.036-0.045 |
| Total overhead (with 1 revision) | ~6200-7400 tokens | ~$0.052-0.061 |

### Comparison: Separate Pre-Planning Agent

| Approach | Tokens | Latency | Complexity |
|----------|--------|---------|-----------|
| PTC + Challenger (this design) | 4-7k extra | +15-30s (1-2 Challenger calls) | 1 new role, no new infra |
| Separate Flow Designer agent | 10-20k | +45-90s (full agent lifecycle) | New agent type, new protocol, new handoff format |
| Full consilium per task | 30-50k | +2-5min | Massive overkill for non-architectural tasks |

### ROI Calculation

- Average bug fix from temporal blindness: 2-4 iteration cycles = 20-40k tokens wasted on retries
- PTC catches the blind spot pre-implementation: 5-7k tokens invested
- **Net savings per caught bug: 15-35k tokens (3-7x ROI)**
- If PTC catches even 1 in 4 temporal bugs, it pays for itself

---

## 10. Weaknesses — Where This Approach Fails

### Fundamental Limitations

1. **Worker model ceiling.** Sonnet 4.6 may struggle with Q4 (deep cascade reasoning) and Q6 (concurrency reasoning) for genuinely complex systems. Mitigation: escalate to Opus for tasks touching ≥3 interacting components.

2. **Challenger calibration.** If the Challenger is too aggressive, every task triggers REVISE and the loop becomes ceremony. If too lenient, it misses real blind spots. Mitigation: explicit "common scenario" focus in the Challenger prompt; review Challenger quality in audits.

3. **Novel temporal patterns.** The 7 questions cover known failure modes. A truly novel temporal bug (one that doesn't fit any of Q1-Q7) will slip through. Mitigation: when a temporal bug escapes PTC, add a Q8 that would have caught it (evolutionary improvement).

4. **Worker gaming.** A Worker can learn to write "good-looking" PTC answers that satisfy the depth gate without genuinely thinking. The checklist answers become cargo-cult rather than reasoning scaffold. Mitigation: the Challenger exists precisely to detect this — generic/templated answers are easy to attack.

5. **Over-engineering simple tasks.** Despite skip criteria, borderline cases will get PTC'd unnecessarily, adding latency to tasks that don't need it. Mitigation: strict skip criteria + Lead discretion + track false-positive rate in handovers.

6. **Doesn't help with unknown unknowns.** PTC forces reasoning about temporal dynamics the Worker CAN identify. It cannot force reasoning about temporal dynamics the Worker doesn't know exist (e.g., an undocumented race condition in a third-party library). Mitigation: this is what institutional knowledge + Challenger are for — Challenger can draw on broader training knowledge.

### When to Escalate Beyond PTC

If the Worker fails PTC twice (cannot produce adequate answers after 2 iterations):
- The task requires deeper architectural understanding than the Worker's tier provides
- Escalate to Opus Worker with PTC embedded
- OR escalate to full consilium if the problem is genuinely novel

---

## Integration with Claude Booster Pipeline

### Where PTC Fits in the Existing Flow

```
RECON → PLAN → [Lead writes Artifact Contract]
                         ↓
         [Lead adds PTC questions to Worker prompt]
                         ↓
         [Worker produces PTC answers + code? NO — only PTC first]
                         ↓
         [Lead validates depth mechanically]
                         ↓
         [Lead spawns Challenger with answers]
                         ↓
         [Challenger PASS/REVISE]
                         ↓
         [If REVISE: forward to Worker, iterate (max 2)]
                         ↓
         [Lead extracts temporal assertions → enriches Artifact Contract]
                         ↓
         [NOW: standard paired-verification — Worker codes + Verifier tests]
                         ↓
         [Verifier's test includes [TEMPORAL]/[BRANCHING]/[CASCADE] assertions]
                         ↓
VERIFY → AUDIT → DELIVER
```

### Changes Required

| Existing Component | Change |
|-------------------|--------|
| Worker prompt template | Add PTC questions block + "answer before coding" instruction |
| Lead's spawn logic | Add depth validation + Challenger spawn + iteration management |
| Artifact Contract | Add `Temporal assertions:` field (populated post-challenge) |
| Verifier prompt | No change — naturally picks up enriched `Acceptance emphasis:` |
| Pipeline phases | No change — PTC happens within IMPLEMENT phase |
| Infrastructure | None — uses existing Agent spawning |

### New Files (minimal)

- `~/.claude/rules/process-thinking-checklist.md` — the rule file (this design, trimmed)
- `~/.claude/prompts/challenger.md` — the Challenger system prompt

No new scripts. No new hooks. No new database tables. No schema changes.

---

## Summary of Key Design Choices

| Choice | Rationale |
|--------|-----------|
| Checklist embedded in Worker, not separate agent | Cheaper, simpler, Worker has full context for answers |
| Challenger as separate spawn, not Worker self-review | Self-review has the same bias as self-evaluation (paired-verification principle) |
| Challenger at same model tier as Worker | Calibrated difficulty — challenges are at Worker's comprehension level |
| Max 2 iterations | Diminishing returns; if 2 rounds don't fix it, the problem is tier mismatch |
| Temporal assertions extracted by Lead | Lead has full context from both sides; extraction is a judgment call |
| Verifier sees assertions, not full PTC | Maintains Verifier independence (tests behavior, not process) |
| Skip criteria are explicit and strict | Prevents ceremony creep while catching all temporal-risk tasks |
| No separate PFD document | Avoids docs-drift; PTC is ephemeral reasoning, assertions are persistent in tests |
