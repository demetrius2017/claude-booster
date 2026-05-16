# Flow Architect Agent — Design Document

**Author:** Worker 1 (Hackathon)  
**Date:** 2026-05-16  
**Status:** Design proposal  

---

## 1. The Problem — Flat-State Cognition

Claude models state as a snapshot: "what is true NOW." It does not naturally project state forward through time, enumerate branching futures, or identify that a variable's value at decision-time will be different from its value at execution-time.

Three failure archetypes:

| Archetype | Example | Root cognitive error |
|-----------|---------|---------------------|
| **Temporal gap** | Inventory reorder using current stock, ignoring 2-month depletion curve until delivery arrives | State at t₀ ≠ state at t₁; calculation uses t₀ as if time stood still |
| **Branch blindness** | Broker execution fix that handles happy-path only | Outcome is a tree, not a line; partial fill / reject / timeout each produce different derived state |
| **Forward projection** | Rebalance logic using today's velocity for a decision that takes effect 6 weeks from now | Parameters are functions of time, not constants |

These are not reasoning failures — Claude *can* do temporal projection when explicitly asked. The failure is **structural**: nothing in the standard pipeline forces temporal/branching analysis before implementation begins. The Worker receives a flat Artifact Contract and naturally produces flat code.

---

## 2. Solution — Flow Architect Agent

A single agent that runs **after RECON, before PLAN**, producing a **Process Flow Document (PFD)** that becomes a mandatory section of the Artifact Contract fed to Worker and Verifier.

```
RECON → [Flow Architect] → PLAN → Worker+Verifier (with PFD in contract)
```

### Why one agent, not many

Per Anthropic/Cognition's finding: single-agent ≥ multi-agent at equal compute. A single sophisticated agent with the right prompt and output schema will outperform 3 agents trying to coordinate temporal views. The PFD is a single coherent artifact; splitting its production across agents introduces information loss at handoff boundaries.

### When it fires (activation criteria)

The Flow Architect is NOT needed for every task. It fires when the task involves **any** of:

1. **Time-separated actions** — something happens now, something else happens later, and state changes between.
2. **External system responses** — broker, API, user action — where the response is non-deterministic (may succeed, fail, partial, timeout).
3. **Derived state** — a value computed from other values, where those source values can change independently.
4. **Concurrent mutations** — multiple writers to the same state (race conditions, reconciliation).
5. **State machines** — explicit or implicit FSM where transitions have preconditions.

**Skip criteria** (Flow Architect adds no value):
- Pure refactoring (same behavior, different structure)
- UI-only changes with no backend state
- Documentation/config changes
- Mechanical search-replace
- Trivial bug fixes where the temporal dimension is obvious (wrong operator, typo in key name)

The **Lead decides** whether to invoke Flow Architect during PLAN phase setup, based on RECON findings. This is a judgment call — not a gate.

---

## 3. Agent Prompt

```markdown
# Flow Architect

You are the Flow Architect. Your job: given RECON findings and a task description,
produce a Process Flow Document (PFD) that maps HOW STATE CHANGES THROUGH TIME
for this task.

## Your mandate

You think in PROCESSES, not SNAPSHOTS. Every variable is a function of time.
Every external call is a branch point. Every derived value has a freshness window.

You are NOT implementing anything. You are NOT designing architecture. You are
mapping the TEMPORAL TOPOLOGY of the problem — what changes, when, what depends
on what, and what can go wrong at each transition.

## What you receive

1. RECON summary (files read, current code state, dependencies)
2. Task description (what needs to happen)
3. dep_manifest.json entries for touched components (if available)
4. Current code of the function(s) being modified (if modification, not greenfield)

## What you produce

A single Process Flow Document in the schema below. Every field is mandatory
unless marked [OPTIONAL]. Empty fields get "N/A — <reason>".

## Your thinking process

For each operation in the task:
1. What is the STATE BEFORE this operation?
2. What are ALL POSSIBLE OUTCOMES (not just success)?
3. For each outcome: what state does it produce? What downstream is affected?
4. How much TIME passes between steps? What changes during that time?
5. What INVARIANTS must hold across the entire flow? Where could they break?

## Quality bar

Your PFD is GOOD when:
- A developer reading it can enumerate every branch without thinking
- Every "what if X fails?" has an explicit answer
- Time-dependent values are marked with their freshness window
- The Worker cannot accidentally write flat-snapshot code because the PFD
  makes branching/temporal structure impossible to ignore

Your PFD is BAD when:
- It restates the task description in flowchart form (no new information)
- It only covers the happy path
- It uses vague language ("handle errors appropriately")
- Time gaps between steps are not quantified
- Derived values don't show their dependency chain

## Output format

Produce ONLY the PFD in the schema specified. No preamble, no commentary outside
the document structure. Use concrete types, concrete values, concrete time units.
```

---

## 4. Process Flow Document (PFD) Schema

```yaml
# Process Flow Document v1.0
# Machine-consumable temporal/branching analysis

meta:
  task: "<one-line task description>"
  temporal_class: "sequential | branching | concurrent | state_machine"
  time_horizon: "<shortest meaningful duration, e.g. '200ms' or '2 months'>"
  critical_state_vars: ["<var1>", "<var2>"]  # state that changes across the flow

timeline:
  # Ordered list of temporal phases. Each phase has a duration and state changes.
  - phase: "<name>"
    duration: "<estimate or 'instantaneous'>"
    state_at_entry:
      <var>: "<value or expression>"
    operations:
      - op: "<what happens>"
        outcomes:
          - label: "success"
            probability: "<high/medium/low or percentage>"
            state_delta:
              <var>: "<new value or transformation>"
            next_phase: "<phase name>"
          - label: "<failure mode>"
            probability: "<estimate>"
            state_delta:
              <var>: "<new value or transformation>"
            next_phase: "<phase name or 'terminal_error'>"
            recovery: "<what must happen to recover, or 'none — propagate'>"

temporal_gaps:
  # Explicit enumeration of time periods where state drifts unobserved
  - between: ["<phase_a>", "<phase_b>"]
    duration: "<time estimate>"
    drifting_state:
      - var: "<variable name>"
        drift_mechanism: "<what causes it to change>"
        drift_rate: "<how fast, or 'unpredictable'>"
        stale_after: "<when the value from phase_a becomes unreliable>"
    mitigation: "<how to handle — refresh, bound, hedge>"

invariants:
  # Properties that MUST hold across the entire flow
  - name: "<invariant name>"
    expression: "<formal or semi-formal expression>"
    violation_consequence: "<what breaks if this fails>"
    enforcement_point: "<where in the flow to check>"
    
branch_tree:
  # Mermaid diagram showing all paths through the flow
  # This is for human readability; machine uses timeline.outcomes
  mermaid: |
    graph TD
      A[Start] --> B{Operation 1}
      B -->|success| C[Phase 2]
      B -->|timeout| D[Retry with backoff]
      B -->|reject| E[Terminal: log + alert]

derived_values:
  # Values computed from other values — dependency chain + freshness
  - name: "<derived value>"
    depends_on: ["<source_var_1>", "<source_var_2>"]
    formula: "<how it's computed>"
    freshness_window: "<how long the computed value remains valid>"
    recompute_trigger: "<event that invalidates it>"

failure_modes:
  # Systematic enumeration of what can go wrong
  - id: "F1"
    trigger: "<what causes this failure>"
    affected_state: ["<var1>", "<var2>"]
    detection: "<how to know it happened>"
    impact: "<what breaks downstream>"
    mitigation: "<code-level response>"
    category: "temporal_gap | branch_unhandled | invariant_violation | race_condition | stale_data"

worker_directives:
  # Concrete instructions for the Worker, derived from the analysis above
  - directive: "<imperative instruction>"
    rationale: "<which failure mode or temporal gap this prevents>"
    enforcement: "input_guard | body_invariant | output_guard | retry_logic | state_refresh"

verifier_assertions:
  # Concrete testable properties for the Verifier, derived from invariants + failure modes
  - assertion: "<what to test>"
    type: "temporal | branching | invariant | freshness"
    how: "<suggested test approach — mock time, inject failure, check state>"
```

---

## 5. Integration with Artifact Contract

The PFD feeds into the existing Artifact Contract as follows:

| PFD Section | Artifact Contract Field | How |
|-------------|------------------------|-----|
| `worker_directives` | **Appended to** `Acceptance emphasis:` | Worker sees temporal/branching requirements alongside functional ones |
| `invariants` | **New field:** `Flow invariants:` | Both Worker and Verifier see what must hold |
| `verifier_assertions` | **Appended to** `Expected observable behavior:` | Verifier knows which temporal/branching properties to test |
| `failure_modes` | **New field:** `Enumerated failure modes:` | Worker must handle each; Verifier must test at least N |
| `derived_values` | **Appended to** `Acceptance emphasis:` | "Value X must be recomputed when Y changes" |
| `temporal_gaps` | **New field:** `Temporal gaps:` | Worker sees where stale-data bugs hide |
| `branch_tree.mermaid` | **New field:** `Flow diagram:` | Visual overview for both |

### Modified Artifact Contract template (additions in bold):

```
Objective: <one sentence>
Verified Facts Brief: <evidence-backed current state>
Artifact path: <output location>
Invocation: <how to run/import>
Inputs: <types and forms>
Expected observable behavior: <what external observer sees>
  + <verifier_assertions from PFD>
**Flow invariants: <from PFD invariants section>**
**Enumerated failure modes: <from PFD failure_modes — Worker must handle, Verifier must test>**
**Temporal gaps: <from PFD temporal_gaps — where stale data hides>**
**Flow diagram: <mermaid from PFD branch_tree>**
Out of scope: <do not touch>
Environment constraints: <deps, versions>
Acceptance emphasis: <what to check>
  + <worker_directives from PFD>
  + <derived_values freshness requirements>
Affected downstream: <from dep_manifest>
Architecture map consulted: yes/no
Architecture constraints: <from dep_manifest feeds>
Downstream consumers: <from dep_manifest called_by>
Session context: <optional>
```

---

## 6. Temporal Modeling Approach

The Flow Architect uses three cognitive primitives to identify time-dependent state:

### Primitive 1: State-Over-Time Projection

For each variable mentioned in the task:
1. What is its value NOW (from RECON)?
2. What CHANGES it? (writers — functions, external events, time-based decay)
3. What is its value at each DECISION POINT in the flow?
4. Is the code using the value from step 1 when it should use the projected value from step 3?

**Detection heuristic:** Any variable that is READ in one function and WRITTEN in another function that runs on a different schedule (cron, event, user action) is a temporal-gap candidate.

### Primitive 2: Outcome Tree Expansion

For each external call or non-deterministic operation:
1. Enumerate ALL return categories: success, partial success, failure, timeout, unexpected.
2. For each category: what state does the system land in?
3. Does the current code handle ALL categories, or only success?
4. For unhandled categories: what state corruption occurs?

**Detection heuristic:** Any function that calls an external system (DB, API, broker, filesystem) and does not have explicit handling for at least {success, failure, timeout} is branch-blind.

### Primitive 3: Freshness Window Analysis

For each derived value (computed from other values):
1. When was the source data last refreshed?
2. How fast do sources change?
3. Is the derived value recomputed before each use, or cached?
4. If cached: what is the maximum staleness, and what's the blast radius of using stale data?

**Detection heuristic:** Any value that is computed once (in a constructor, startup, or earlier function) and used later without re-validation is a freshness candidate. The longer the time between computation and use, the higher the risk.

---

## 7. Failure Mode Enumeration — Systematic Method

The Flow Architect applies a **HAZOP-inspired** (Hazard and Operability Study) approach adapted for software:

### Guide words applied to each operation in the timeline:

| Guide Word | Software Meaning | Question |
|------------|-----------------|----------|
| **NO** | Operation doesn't execute at all | What if this step is skipped (crash, exception before, conditional bypass)? |
| **MORE** | Operation executes multiple times | What if retry logic fires? What if event delivered twice? Idempotent? |
| **LESS** | Partial execution | What if only part of the data processes (partial fill, truncated response, half-written file)? |
| **REVERSE** | Opposite effect | What if the operation undoes previous work (overwrite, negative quantity, backwards delta)? |
| **LATE** | Happens after expected window | What if latency exceeds expectation (timeout, stale cache, clock skew)? |
| **EARLY** | Happens before preconditions met | What if event arrives before state is ready (race condition, startup order)? |
| **OTHER** | Wrong operation entirely | What if a different event triggers this path (event misrouting, enum mismatch)? |

The agent applies each guide word to each operation in the timeline and records non-trivial results in `failure_modes`.

---

## 8. Quality Criteria — How to Know the PFD is Good

### Necessary conditions (all must hold):

1. **Branch coverage** — every `outcomes` list has ≥2 entries (at minimum: success + one failure). A single-outcome operation means the analysis is incomplete.

2. **Temporal quantification** — every `temporal_gaps` entry has a non-vague `duration` (not "some time" but "2-60 seconds" or "1-8 weeks").

3. **Invariant testability** — every invariant has an `expression` that could be turned into a boolean assertion in code. "System should be consistent" is not an invariant; `sum(positions.qty * positions.price) == portfolio.total_value ± 0.01` is.

4. **Worker directive actionability** — every `worker_directives` entry is an imperative that translates to a specific code pattern (guard clause, retry loop, refresh call, lock acquisition). "Be careful with timing" is not actionable.

5. **Failure mode specificity** — every `failure_modes` entry names a concrete trigger, not a category. "Network error" is too vague; "broker API returns HTTP 503 during market open due to load" is specific.

6. **No happy-path-only flow** — the branch_tree mermaid MUST show at least one path that terminates in error/recovery, not just success.

### Sufficiency test (Lead evaluates before passing to Worker):

> "If I gave this PFD to a junior developer who has never seen this codebase, could they enumerate all the edge cases without asking questions?"

If yes — the PFD is sufficient. If no — the Flow Architect missed something.

### Automated quality check (Lead runs before accepting PFD):

```python
def validate_pfd(pfd: dict) -> list[str]:
    """Returns list of quality violations. Empty = pass."""
    violations = []
    
    # Every operation must have ≥2 outcomes
    for phase in pfd["timeline"]:
        for op in phase["operations"]:
            if len(op["outcomes"]) < 2:
                violations.append(f"Single-outcome operation: {op['op']} in {phase['phase']}")
    
    # Every temporal_gap must have quantified duration
    for gap in pfd.get("temporal_gaps", []):
        if any(v in gap["duration"].lower() for v in ["some", "variable", "depends"]):
            violations.append(f"Vague duration in gap: {gap['between']}")
    
    # Every invariant must have expression
    for inv in pfd.get("invariants", []):
        if len(inv["expression"]) < 10:  # too short to be real
            violations.append(f"Trivial invariant expression: {inv['name']}")
    
    # Must have at least one failure_mode
    if not pfd.get("failure_modes"):
        violations.append("No failure modes enumerated")
    
    # branch_tree must exist and contain at least one failure path
    mermaid = pfd.get("branch_tree", {}).get("mermaid", "")
    if "error" not in mermaid.lower() and "fail" not in mermaid.lower() and "reject" not in mermaid.lower():
        violations.append("Branch tree shows no failure paths")
    
    return violations
```

---

## 9. Cost/Latency Budget

### Model choice: **Opus** (via `model_balancer.py get hard`)

Rationale: The Flow Architect performs **architecture-level reasoning** — cross-system temporal analysis, failure enumeration, invariant derivation. This is firmly in the "Hard" tier (architecture design, cross-system reasoning). Sonnet would produce shallow PFDs that miss subtle temporal interactions.

When balancer returns `codex-cli` for `hard` category: use `codex_worker.sh gpt-5.5` — same intelligence tier as Opus, flat-fee.

### Expected token usage:

| Component | Input tokens | Output tokens |
|-----------|-------------|---------------|
| Agent prompt | ~800 | — |
| RECON summary | ~2,000–5,000 | — |
| Task description + current code | ~1,000–10,000 | — |
| dep_manifest entries | ~500–2,000 | — |
| **PFD output** | — | ~2,000–6,000 |
| **Total per invocation** | ~4,000–18,000 in | ~2,000–6,000 out |

### Latency:

- Opus: 30–90 seconds (depending on input size)
- Codex gpt-5.5: 15–45 seconds (faster inference)

### Total pipeline impact:

Adding Flow Architect between RECON and PLAN adds **30–90 seconds** to the pipeline. This is acceptable because:
1. It runs once per task (not per Worker attempt)
2. Worker+Verifier quality improves → fewer W-failure retries (each retry costs 60–120s)
3. Net pipeline time often decreases for complex tasks (1 PFD + 1 Worker pass vs. 0 PFD + 3 Worker retries)

### When the cost is NOT justified (skip):

Tasks where `temporal_class` would be "instantaneous" with zero temporal gaps and a single operation with binary outcome (success/simple failure). Lead skips Flow Architect — see §2 activation criteria.

---

## 10. Concrete Example — Inventory Reorder Point

### Input to Flow Architect:

```
Task: Fix calculate_reorder_point() to account for stock depletion during lead time.

RECON: 
- Function at inventory/calculations.py:45
- Currently: reorder_point = safety_stock + (avg_daily_demand * lead_time_days)
- Uses current_stock from latest snapshot (refreshed daily at 00:00 UTC)
- lead_time_days = 60 (supplier delivery time)
- avg_daily_demand computed from last 30 days
- Downstream: purchase_order_generator reads reorder_point to trigger POs
```

### Flow Architect Output (abbreviated):

```yaml
meta:
  task: "Fix reorder point to project stock depletion during lead time"
  temporal_class: "sequential"
  time_horizon: "60 days"
  critical_state_vars: ["current_stock", "daily_demand", "reorder_point", "stock_at_delivery"]

timeline:
  - phase: "calculation_trigger"
    duration: "instantaneous"
    state_at_entry:
      current_stock: "snapshot from 00:00 UTC today"
      daily_demand: "avg of last 30 days"
    operations:
      - op: "calculate reorder_point"
        outcomes:
          - label: "success"
            probability: "high"
            state_delta:
              reorder_point: "safety_stock + (avg_daily_demand * lead_time_days)"
            next_phase: "comparison"
          - label: "stale_snapshot"
            probability: "low"
            state_delta:
              reorder_point: "calculated from yesterday's data (snapshot failed)"
            next_phase: "comparison"
            recovery: "flag stale, use pessimistic estimate (add 1 day demand)"

  - phase: "comparison"
    duration: "instantaneous"
    state_at_entry:
      current_stock: "same snapshot (not re-fetched)"
      reorder_point: "from previous phase"
    operations:
      - op: "compare current_stock vs reorder_point → trigger PO?"
        outcomes:
          - label: "stock_above_reorder → no action"
            probability: "high"
            state_delta: {}
            next_phase: "end_no_order"
          - label: "stock_below_reorder → generate PO"
            probability: "medium"
            state_delta:
              po_generated: true
            next_phase: "lead_time_wait"

  - phase: "lead_time_wait"
    duration: "60 days"
    state_at_entry:
      current_stock: "value at PO generation time"
      daily_demand: "avg from last 30 days (will change)"
    operations:
      - op: "stock depletes while waiting for delivery"
        outcomes:
          - label: "demand_as_expected"
            probability: "medium"
            state_delta:
              stock_at_delivery: "current_stock - (avg_daily_demand * 60)"
            next_phase: "delivery"
          - label: "demand_spike (seasonal/promotion)"
            probability: "medium"
            state_delta:
              stock_at_delivery: "current_stock - (actual_demand * 60) → may be NEGATIVE"
            next_phase: "stockout_before_delivery"
          - label: "demand_drop"
            probability: "low"
            state_delta:
              stock_at_delivery: "higher than projected → overstock"
            next_phase: "delivery"

temporal_gaps:
  - between: ["calculation_trigger", "lead_time_wait"]
    duration: "0–60 days"
    drifting_state:
      - var: "current_stock"
        drift_mechanism: "daily sales deplete stock"
        drift_rate: "avg_daily_demand units/day (but varies ±30%)"
        stale_after: "1 day (next sales happen)"
      - var: "avg_daily_demand"
        drift_mechanism: "seasonality, promotions, market changes"
        drift_rate: "unpredictable; historical variance ±30% month-over-month"
        stale_after: "14 days (half the averaging window)"
    mitigation: "Project stock forward: stock_at_delivery = current_stock - projected_demand_over_lead_time. Use demand forecast, not static average."

invariants:
  - name: "no_stockout_before_delivery"
    expression: "projected_stock_at_delivery >= 0"
    violation_consequence: "Stockout — lost sales, customer churn"
    enforcement_point: "reorder_point calculation (must be high enough to prevent)"

  - name: "reorder_triggers_before_critical"
    expression: "reorder_point >= (avg_daily_demand * lead_time_days) + safety_stock"
    violation_consequence: "PO triggers too late, stock hits zero before delivery"
    enforcement_point: "output of calculate_reorder_point()"

derived_values:
  - name: "projected_stock_at_delivery"
    depends_on: ["current_stock", "daily_demand_forecast", "lead_time_days"]
    formula: "current_stock - sum(daily_demand_forecast[t] for t in range(lead_time_days))"
    freshness_window: "1 day (new sales data arrives daily)"
    recompute_trigger: "new snapshot OR demand forecast update"

failure_modes:
  - id: "F1"
    trigger: "Demand spikes 50%+ above historical average during lead time (promotion, viral event)"
    affected_state: ["stock_at_delivery", "reorder_point adequacy"]
    detection: "daily monitoring: actual_demand > 1.5 * avg_daily_demand for 3+ consecutive days"
    impact: "Stockout before delivery despite 'correct' reorder point"
    mitigation: "Use demand forecast with confidence interval, not flat average. Safety stock sized to cover upper CI bound."
    category: "temporal_gap"

  - id: "F2"
    trigger: "Lead time extends (supplier delay, logistics disruption)"
    affected_state: ["stock_at_delivery"]
    detection: "supplier notification OR delivery_date > expected_date + buffer"
    impact: "More depletion than projected → stockout"
    mitigation: "Reorder point formula includes lead_time_variance buffer. Re-trigger calculation when lead_time_estimate changes."
    category: "temporal_gap"

  - id: "F3"
    trigger: "Snapshot data is stale (daily job failed, uses yesterday's stock)"
    affected_state: ["current_stock accuracy"]
    detection: "snapshot.timestamp < today - 1 day"
    impact: "Reorder decision based on wrong stock level"
    mitigation: "Input guard: reject snapshot older than 26h. Fallback: use pessimistic (subtract 1 extra day of demand)."
    category: "stale_data"

worker_directives:
  - directive: "Replace static avg_daily_demand * lead_time_days with a PROJECTED depletion curve: sum of daily demand forecasts over the lead time window"
    rationale: "Prevents F1 — flat average ignores known seasonality and trends"
    enforcement: "body_invariant"

  - directive: "Add input guard: reject stock snapshot older than 26 hours. If stale, use current_stock - avg_daily_demand as pessimistic fallback"
    rationale: "Prevents F3 — stale snapshot leads to wrong reorder decision"
    enforcement: "input_guard"

  - directive: "Add output guard: assert projected_stock_at_delivery >= 0 before returning reorder_point. If violated, raise alert + return emergency_reorder_point (immediate PO)"
    rationale: "Prevents stockout — Three Nos Layer 2: do not pass on a reorder_point that guarantees stockout"
    enforcement: "output_guard"

  - directive: "Include lead_time_variance in safety_stock calculation: safety_stock = z_score * sqrt(lead_time_days * demand_variance + avg_demand^2 * lead_time_variance)"
    rationale: "Prevents F2 — fixed safety_stock ignores supplier reliability variance"
    enforcement: "body_invariant"

verifier_assertions:
  - assertion: "Given current_stock=1000, daily_demand=10, lead_time=60: reorder_point must be > 600 (accounts for depletion, not just current state)"
    type: "temporal"
    how: "Unit test with mock data. Old code would return ~600+safety; new code returns higher because it projects the depletion curve."

  - assertion: "Given a demand_forecast that increases 50% mid-lead-time: reorder_point is higher than with flat average"
    type: "temporal"
    how: "Unit test comparing output with flat vs. ramping demand forecast"

  - assertion: "Given a snapshot with timestamp 48 hours ago: function raises or returns pessimistic fallback, not normal calculation"
    type: "freshness"
    how: "Unit test with stale timestamp → expect ValueError or fallback flag"

  - assertion: "Invariant: for any valid inputs, projected_stock_at_delivery >= 0"
    type: "invariant"
    how: "Property-based test (hypothesis) with random valid inputs"

branch_tree:
  mermaid: |
    graph TD
      A[Daily trigger: calculate reorder_point] --> B{Snapshot fresh?}
      B -->|Yes| C[Project demand curve over lead_time]
      B -->|No: stale >26h| D[Use pessimistic estimate]
      C --> E[Compute projected_stock_at_delivery]
      D --> E
      E --> F{projected_stock >= 0?}
      F -->|Yes| G[Return reorder_point]
      F -->|No: stockout projected| H[Emergency: return max_reorder + alert]
      G --> I{current_stock < reorder_point?}
      I -->|Yes| J[Generate PO]
      I -->|No| K[No action]
      J --> L{60-day lead time}
      L -->|Demand as expected| M[Delivery: stock OK]
      L -->|Demand spike| N[Stockout risk: re-trigger calculation]
      L -->|Supplier delay| O[Extended depletion: re-trigger calculation]
```

---

## 11. Integration with Pipeline Phases

### Phase transitions:

```
RECON
  ├── Lead reads code, dep_manifest, identifies task scope
  ├── Lead evaluates activation criteria (§2)
  │
  ▼ (if temporal/branching complexity detected)
FLOW_ARCHITECT (sub-phase of PLAN)
  ├── Lead spawns Flow Architect agent (Opus / codex gpt-5.5)
  ├── Agent receives: RECON summary + task + current code + dep_manifest
  ├── Agent produces: PFD (YAML)
  ├── Lead validates PFD quality (automated check + sufficiency test)
  ├── Lead extracts: worker_directives, verifier_assertions, invariants, failure_modes
  │
  ▼
PLAN
  ├── Artifact Contract enriched with PFD sections
  ├── Worker brief includes: flow invariants, temporal gaps, directives
  ├── Verifier brief includes: assertions, invariants, failure modes to test
  │
  ▼
IMPLEMENT (Worker + Verifier pair, per paired-verification.md)
```

### Hook integration:

The Flow Architect does NOT need a new hook. It's invoked by the Lead as an Agent call during the PLAN phase. The existing `phase_gate.py` ensures no code edits happen before IMPLEMENT phase. The PFD is a planning artifact, not a code artifact.

### Storage:

PFD is stored as `state/pfd/{task_id}_flow.yaml` (or inline in the Artifact Contract for simple flows). This allows:
- Post-mortem analysis (did the PFD predict the actual failures?)
- Reuse (similar tasks can reference prior PFDs)
- Audit trail (did Worker follow the directives?)

---

## 12. Weaknesses and Mitigations

### Weakness 1: Over-engineering simple tasks

**Risk:** Lead invokes Flow Architect for a task that doesn't need temporal analysis. The PFD is mostly "N/A" fields. Cost: 60s + tokens for no value.

**Mitigation:** Clear skip criteria (§2). Lead's judgment call. If >50% of PFD fields are "N/A", the activation criteria need tuning (feedback loop).

### Weakness 2: Single agent can still produce shallow analysis

**Risk:** Even Opus can be lazy — producing surface-level PFDs that enumerate the obvious cases but miss subtle interactions between temporal gaps and failure modes.

**Mitigation:**
- Automated quality check rejects single-outcome operations and vague durations.
- The HAZOP guide-word method forces systematic enumeration (not free-association).
- Lead can reject PFD and re-prompt with "you missed: [specific gap]" (same retry logic as Worker failures).

### Weakness 3: PFD may be wrong (predicts failures that can't happen, misses ones that can)

**Risk:** The Flow Architect operates on RECON data + code reading, not runtime observation. It may misunderstand how components interact in production.

**Mitigation:**
- PFD is a planning aid, not a specification. Worker uses it for guidance but can deviate if code reality differs.
- Post-incident analysis: compare actual failure against PFD predictions → feed back into prompt tuning.
- The PFD doesn't REPLACE Verifier testing — it INFORMS it. Verifier still writes independent tests.

### Weakness 4: Added latency on every complex task

**Risk:** 30–90 seconds per complex task. In a session with 5 complex tasks, that's 2.5–7.5 minutes of pure planning.

**Mitigation:**
- Pipeline normally spends 60–120s per Worker retry. One PFD that prevents 2 retries saves net time.
- For tasks where latency matters more than correctness (hotfix, incident): Lead can skip with documented justification.

### Weakness 5: Worker may ignore directives

**Risk:** Worker receives a rich PFD-enhanced Artifact Contract but produces flat code anyway (context overwhelm, or the directives are too abstract for the Worker's model tier).

**Mitigation:**
- Verifier's assertions are derived from PFD — even if Worker ignores directives, the test will catch the gap.
- Worker directives are phrased as imperative instructions, not suggestions ("Add input guard..." not "Consider adding...").
- If Worker consistently fails on PFD-informed tasks → escalate model tier (Sonnet → Opus for Worker).

### Weakness 6: Schema rigidity for novel problem shapes

**Risk:** The PFD schema assumes problems decompose into timeline phases, temporal gaps, and branch trees. Some problems (emergent behavior, complex feedback loops, multi-party protocols) may not fit cleanly.

**Mitigation:**
- `temporal_class: "state_machine"` covers FSMs and protocols.
- For truly novel shapes: Flow Architect can add free-form `notes:` field (not in schema yet — add if needed).
- The schema is v1.0; iterate based on real usage patterns.

---

## 13. Comparison with Alternatives

| Approach | Pros | Cons | Why not |
|----------|------|------|---------|
| **Multi-agent temporal council** (3 agents: time, branches, invariants) | Diverse perspectives | Information loss at merge; 3x cost; coordination overhead | Single-agent ≥ multi-agent at equal compute |
| **Prompt engineering only** (add "think about time" to Worker prompt) | Zero cost | Unreliable — Worker has competing priorities (implementation), temporal thinking gets deprioritized | Proven failure mode: Worker optimizes for "get code working" over "think about all branches" |
| **Post-implementation audit** (audit phase catches temporal bugs) | Catches bugs | Catches them LATE — after Worker already wrote flat code; fixing is more expensive than preventing | Shift-left principle: prevent > detect > fix |
| **Formal verification tools** (TLA+, Alloy) | Mathematically rigorous | Massive overhead; requires formal spec; overkill for most tasks | Good for protocols; overkill for business logic |
| **This design (single Flow Architect)** | Right balance of rigor and cost; prevents flat-code at source; informs both Worker and Verifier | Depends on Opus quality; adds latency; may over-engineer simple tasks | Selected |

---

## 14. Implementation Plan

### Phase 1 — Minimal viable (can ship immediately):

1. Add activation criteria check to Lead's PLAN phase logic (in `pipeline.md` or as a skill).
2. Flow Architect prompt as a skill command (`/flow-architect` or auto-triggered by Lead).
3. PFD output parsed and injected into Artifact Contract.
4. Automated quality validator (the Python function from §8).

### Phase 2 — Feedback loop:

5. Store PFDs in `state/pfd/`.
6. Post-incident: compare actual failures against PFD predictions.
7. Tune activation criteria based on false-positive (PFD was overkill) and false-negative (PFD was needed but skipped) rates.

### Phase 3 — Optimization:

8. Cache PFD patterns for recurring task types (e.g., "broker execution" always needs the same temporal structure).
9. Sonnet-tier "PFD-lite" for tasks that need basic branch enumeration but not full temporal analysis.
10. Integration with `dep_manifest.json` — auto-populate `temporal_gaps` from known write frequencies of upstream components.

---

## 15. Summary

The Flow Architect is a **single sophisticated agent** that runs between RECON and PLAN, producing a structured Process Flow Document that maps the temporal topology of a problem. It forces the pipeline to think in processes (state changes over time, branching futures, freshness windows) rather than snapshots (current state only).

Key design decisions:
- **One agent, not many** — information coherence over diversity
- **Opus tier** — this is architecture-level reasoning, not mechanical work
- **Structured output** — machine-consumable YAML, not prose
- **Feeds existing Artifact Contract** — minimal changes to paired-verification protocol
- **Optional activation** — Lead decides; not every task needs temporal analysis
- **Informs, doesn't replace** — Verifier still writes independent tests; PFD guides but doesn't dictate

The fundamental bet: **30–90 seconds of temporal planning prevents 2–3 Worker retries** on tasks where flat-snapshot thinking would produce code that misses branches, ignores time gaps, or computes derived values from stale inputs.
