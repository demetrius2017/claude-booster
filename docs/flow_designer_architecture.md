# Flow Designer — Architecture Design Document

**Author:** Worker 2 (Hackathon)  
**Date:** 2026-05-16  
**Status:** Design proposal  

---

## 1. Problem Statement

Claude processes state as flat snapshots — "what is true NOW" — and fails to model:
- **Temporal decay:** values that change between decision-time and effect-time (inventory depleting during lead time)
- **Branching futures:** execution paths that diverge on partial success, timeout, error (broker partial fills)
- **Cascade propagation:** derived metrics that shift when upstream state changes (NAV after rebalance)

The Flow Designer injects a **Process Flow Document (PFD)** between RECON and PLAN, forcing the agent to think in temporal process flows before writing code.

---

## 2. Architecture Overview

```
User Task
    │
    ▼
RECON (standard) ─── produces Verified Facts Brief
    │
    ▼
┌───────────────────────────────────────────────────────┐
│              FLOW DESIGNER (parallel)                  │
│                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │   Temporal    │  │   Failure    │  │   State    │ │
│  │   Modeler    │  │   Analyst    │  │  Dep Mapper│ │
│  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│         │                  │                │        │
│         └──────────┬───────┘────────────────┘        │
│                    ▼                                  │
│             ┌────────────┐                           │
│             │ Synthesizer │                           │
│             └──────┬─────┘                           │
│                    │                                  │
└────────────────────┼──────────────────────────────────┘
                     ▼
          Process Flow Document (PFD)
                     │
                     ▼
              PLAN (uses PFD as input)
                     │
                     ▼
           IMPLEMENT (Worker sees PFD in Artifact Contract)
```

---

## 3. Agent Prompts

### 3.1 Temporal Modeler

```markdown
# Role: Temporal Modeler

You analyze state that changes over time. Your job: identify every value in the
Verified Facts Brief that will be DIFFERENT at the moment the code's effect
materializes vs. the moment the code reads it.

## Input
- Verified Facts Brief (from RECON)
- User task description
- Relevant code snippets (file paths provided, read them yourself)

## Instructions

1. List every state variable the task touches (inventory_level, account_balance,
   position_size, queue_depth, token_expiry, etc.)

2. For each variable, classify:
   - STATIC: does not change between read-time and effect-time (config values, IDs)
   - DECAYING: decreases over time (inventory, budget, TTL, quota remaining)
   - ACCUMULATING: increases over time (queue depth, debt, log size)
   - VOLATILE: changes unpredictably (market price, API latency, concurrent users)
   - PERIODIC: changes on a cycle (cron state, seasonal demand, billing cycle)

3. For non-STATIC variables, project:
   - Current value (from code/data)
   - Time horizon: how long between code's read and code's effect?
   - Projected value at effect-time (formula or qualitative direction)
   - Confidence: HIGH (deterministic formula) / MEDIUM (statistical) / LOW (external factors)

4. Flag TEMPORAL TRAPS: places where the code uses a current value but the
   effect happens later, creating a gap that could cause failure.

## Output Format (YAML)

```yaml
temporal_analysis:
  time_horizon: "<duration from decision to effect>"
  variables:
    - name: "<variable_name>"
      location: "<file:line or API endpoint>"
      classification: "STATIC|DECAYING|ACCUMULATING|VOLATILE|PERIODIC"
      current_value: "<value or formula>"
      projected_at_effect: "<value or direction>"
      confidence: "HIGH|MEDIUM|LOW"
      temporal_trap: true|false
      trap_description: "<why this is dangerous if treated as static>"
  critical_windows:
    - window: "<time range where state transitions are dangerous>"
      affected_variables: ["<var1>", "<var2>"]
      risk: "<what goes wrong>"
```

## Anti-patterns (do NOT do)
- Do NOT mark everything as VOLATILE — be precise about mechanism
- Do NOT invent variables not present in the code
- Do NOT provide fix recommendations — that's the Synthesizer's job
```

### 3.2 Failure Analyst

```markdown
# Role: Failure Analyst

You enumerate execution branches. Your job: starting from the happy path,
identify every point where execution can diverge (partial success, timeout,
error, race condition) and trace the downstream effect of each branch.

## Input
- Verified Facts Brief (from RECON)
- User task description
- Relevant code snippets (file paths provided, read them yourself)

## Instructions

1. Identify the PRIMARY FLOW (happy path) as a sequence of steps.

2. At each step, identify BRANCH POINTS:
   - PARTIAL: operation partially succeeds (partial fill, partial write, some items processed)
   - TIMEOUT: operation exceeds time limit (network, lock acquisition, queue wait)
   - ERROR: operation fails entirely (exception, 4xx/5xx, constraint violation)
   - RACE: concurrent operations create unexpected interleaving
   - EXTERNAL: dependency behavior changes (API deprecation, rate limit, schema change)

3. For each branch, trace DOWNSTREAM EFFECTS:
   - What state is left inconsistent?
   - What downstream operations will see corrupted/stale input?
   - Is the branch self-healing (retry, compensating transaction) or terminal?
   - What's the blast radius? (single record, user session, all users, data corruption)

4. Build the BRANCH TREE: a directed graph from entry point to all terminal states.

## Output Format (YAML)

```yaml
failure_analysis:
  entry_point: "<function or endpoint that starts the flow>"
  happy_path:
    - step: "<step_name>"
      operation: "<what happens>"
      state_after: "<system state if succeeds>"

  branch_points:
    - step: "<step_name from happy_path>"
      branches:
        - type: "PARTIAL|TIMEOUT|ERROR|RACE|EXTERNAL"
          trigger: "<specific condition>"
          probability: "LIKELY|POSSIBLE|UNLIKELY|EDGE"
          state_after: "<system state in this branch>"
          downstream_effects:
            - affected: "<component or state>"
              effect: "<what breaks or becomes inconsistent>"
              blast_radius: "RECORD|SESSION|USER|SYSTEM"
          recovery:
            mechanism: "RETRY|COMPENSATE|MANUAL|NONE"
            exists_in_code: true|false
            gap: "<what's missing if recovery is incomplete>"

  terminal_states:
    - name: "<descriptive name>"
      reached_via: ["<branch path>"]
      consistency: "CONSISTENT|PARTIALLY_INCONSISTENT|CORRUPTED"
      requires_intervention: true|false
```

## Anti-patterns (do NOT do)
- Do NOT list only obvious errors (null pointer, 500) — think about PARTIAL success
- Do NOT assume retry solves everything — trace what happens DURING the retry window
- Do NOT ignore concurrent execution — if this code can run in parallel, model it
- Do NOT provide fix recommendations — that's the Synthesizer's job
```

### 3.3 State Dependency Mapper

```markdown
# Role: State Dependency Mapper

You map derived state. Your job: given the state variables this task touches,
identify everything that DERIVES from them — and what cascades when they change.

## Input
- Verified Facts Brief (from RECON)
- User task description
- Relevant code snippets (file paths provided, read them yourself)

## Instructions

1. Identify PRIMARY STATE: the variables this task directly modifies or reads.

2. For each primary variable, trace DERIVATIONS:
   - What other values are computed FROM this variable?
   - Where are those computations? (file:line, SQL view, cached value, UI display)
   - How stale can the derivation be? (real-time, eventual, batch-computed)

3. Build the CASCADE GRAPH:
   - When variable X changes, what chain of recalculations must fire?
   - Are there MISSING cascades (X changes but derived Y is never recomputed)?
   - Are there CIRCULAR dependencies (A derives from B derives from A)?

4. Identify CONSISTENCY BOUNDARIES:
   - Which derived values must be updated atomically with the primary?
   - Which can be eventually consistent?
   - What's the maximum acceptable staleness for each?

5. Mark HIDDEN CONSUMERS: code that reads the primary variable but isn't
   obviously connected (other microservices, cron jobs, reports, caches).

## Output Format (YAML)

```yaml
state_dependency_map:
  primary_state:
    - name: "<variable_name>"
      location: "<file:line>"
      modified_by_task: true|false
      read_by_task: true|false

  derivations:
    - source: "<primary_variable>"
      derived: "<derived_variable>"
      computation_location: "<file:line or service>"
      staleness_tolerance: "REALTIME|SECONDS|MINUTES|HOURS|BATCH"
      update_mechanism: "TRIGGER|EVENT|POLL|MANUAL|NONE"
      missing_cascade: true|false
      missing_cascade_risk: "<what goes wrong if not updated>"

  cascade_chains:
    - trigger: "<initial change>"
      chain: ["<step1>", "<step2>", "<step3>"]
      total_propagation_time: "<duration>"
      atomicity_required: true|false

  consistency_boundaries:
    - boundary: "<name>"
      variables: ["<var1>", "<var2>"]
      constraint: "ATOMIC|EVENTUAL|BATCH"
      current_implementation: "TRANSACTION|SAGA|CRON|NONE"
      gap: "<what's missing>"

  hidden_consumers:
    - variable: "<primary_variable>"
      consumer: "<who reads it>"
      location: "<file:line or service>"
      update_awareness: "SUBSCRIBES|POLLS|UNAWARE"
      risk_if_stale: "<consequence>"
```

## Anti-patterns (do NOT do)
- Do NOT list UI display as a "critical" cascade unless staleness causes user action errors
- Do NOT assume all caches are invalidated — check the code for actual invalidation logic
- Do NOT invent dependencies not visible in code — cite file:line for each derivation
- Do NOT provide fix recommendations — that's the Synthesizer's job
```

---

## 4. Synthesizer Logic

The Synthesizer is NOT a separate agent spawn — it runs as **Lead logic** (Opus) after all three agents return. This saves a spawn and keeps synthesis at the highest intelligence tier where cross-domain reasoning happens.

### 4.1 Merge Algorithm

```python
# Pseudocode — actual implementation in Lead's reasoning

def synthesize(temporal: dict, failure: dict, state_dep: dict) -> PFD:
    """
    Three-pass merge:
    1. ALIGN — map variables across all three outputs to canonical names
    2. ENRICH — combine temporal classification + failure branches + cascade effects
       for each variable into a unified view
    3. CONFLICT — detect and resolve disagreements
    """

    # Pass 1: Variable alignment
    # Each agent may name the same variable differently
    # (e.g., "stock_level" vs "inventory_count" vs "available_qty")
    # Lead resolves by checking file:line references — same location = same variable
    canonical_vars = align_by_location(temporal.variables, state_dep.primary_state)

    # Pass 2: Enrichment
    for var in canonical_vars:
        var.temporal = temporal.get_classification(var)
        var.branches = failure.get_branches_affecting(var)
        var.cascades = state_dep.get_derivations_from(var)
        var.composite_risk = compute_risk(var.temporal, var.branches, var.cascades)

    # Pass 3: Conflict resolution (see section 7)
    conflicts = detect_conflicts(temporal, failure, state_dep)
    resolutions = resolve_conflicts(conflicts)  # rules in section 7

    return PFD(
        variables=canonical_vars,
        flow=failure.happy_path,  # happy path as backbone
        branches=failure.branch_points,  # enriched with temporal + cascade
        temporal_windows=temporal.critical_windows,
        cascade_chains=state_dep.cascade_chains,
        conflicts_resolved=resolutions,
        risk_summary=rank_by_composite_risk(canonical_vars)
    )
```

### 4.2 Enrichment Rules

| Temporal class | + Failure branch | + Cascade depth | = Composite risk |
|---|---|---|---|
| STATIC | no branches | 0 derivations | NEGLIGIBLE — skip in PFD |
| DECAYING | TIMEOUT branch exists | ≥1 cascade | HIGH — value depletes during timeout window |
| VOLATILE | PARTIAL branch exists | ≥2 cascades | CRITICAL — partial op on volatile + cascades = inconsistency storm |
| ACCUMULATING | no recovery mechanism | hidden consumers | HIGH — growth unnoticed by unaware consumers |
| Any non-STATIC | Any branch | missing_cascade=true | CRITICAL — state changes but derivations don't update |

### 4.3 What Gets Dropped

- Variables classified as STATIC by Temporal Modeler AND with 0 derivations from State Mapper AND not involved in any failure branch → **excluded from PFD** (noise reduction)
- Failure branches with probability=UNLIKELY AND blast_radius=RECORD AND recovery exists → **mentioned in appendix only** (not main flow)

---

## 5. Process Flow Document Schema

```yaml
# Process Flow Document (PFD) v1.0
# Machine-consumable output of Flow Designer

pfd_version: "1.0"
task: "<original user task description>"
generated_at: "<ISO timestamp>"
time_horizon: "<from Temporal Modeler — overall decision-to-effect window>"

# Section 1: State inventory with temporal + cascade metadata
state_inventory:
  - name: "<canonical variable name>"
    location: "<file:line>"
    temporal_class: "STATIC|DECAYING|ACCUMULATING|VOLATILE|PERIODIC"
    current_value: "<value or reference>"
    projected_at_effect: "<value or direction, null if STATIC>"
    cascade_depth: <int>  # how many derivations depend on this
    branch_exposure: <int>  # how many failure branches affect this
    composite_risk: "NEGLIGIBLE|LOW|MEDIUM|HIGH|CRITICAL"
    trap_description: "<null or why this is dangerous>"

# Section 2: Execution flow with branch points
execution_flow:
  happy_path:
    - step: "<step_name>"
      operation: "<what happens>"
      state_mutations: ["<var1>", "<var2>"]
      temporal_assumption: "<what must be true about time for this step to work>"

  branch_points:
    - at_step: "<step_name>"
      type: "PARTIAL|TIMEOUT|ERROR|RACE|EXTERNAL"
      trigger: "<condition>"
      probability: "LIKELY|POSSIBLE|UNLIKELY|EDGE"
      temporal_interaction: "<how time affects this branch — null if none>"
      cascade_effects:
        - variable: "<what shifts>"
          derivations_affected: ["<derived1>", "<derived2>"]
          staleness_risk: "<what happens if derivations don't update>"
      recovery:
        mechanism: "<type>"
        exists: true|false
        temporal_gap: "<how long system is inconsistent during recovery>"

# Section 3: Critical temporal windows
temporal_windows:
  - window: "<time range>"
    danger: "<what goes wrong in this window>"
    affected_variables: ["<var1>", "<var2>"]
    mitigation_required: "<what the implementation must handle>"

# Section 4: Cascade chains requiring attention
cascade_chains:
  - trigger: "<initial event>"
    chain: ["<step1> → <step2> → <step3>"]
    atomicity: "REQUIRED|PREFERRED|EVENTUAL_OK"
    current_gap: "<what's missing in current code — null if handled>"

# Section 5: Risk-ranked action items for implementation
action_items:
  - priority: 1
    risk: "CRITICAL"
    description: "<what the implementation MUST handle>"
    source_agents: ["temporal", "failure", "state_dep"]  # which agents flagged this
    evidence: "<file:line or reasoning>"
  - priority: 2
    # ...

# Section 6: Conflicts resolved during synthesis (audit trail)
conflicts_resolved:
  - variable: "<name>"
    temporal_says: "<classification>"
    failure_says: "<different classification>"
    state_dep_says: "<yet another view>"
    resolution: "<which view won and why>"

# Appendix: Low-risk branches (for completeness, not action)
appendix_low_risk:
  - branch: "<description>"
    reason_deprioritized: "<why this is in appendix>"
```

---

## 6. Integration with Artifact Contract

The PFD feeds directly into the existing paired Worker+Verifier contract:

| PFD Section | Artifact Contract Field | How it feeds |
|---|---|---|
| `state_inventory` (non-STATIC) | **Inputs** | "These inputs are NOT static — implementation must handle temporal drift" |
| `execution_flow.branch_points` | **Expected observable behavior** | Each branch becomes an explicit behavioral requirement |
| `temporal_windows` | **Acceptance emphasis** | Verifier must test behavior WITHIN critical windows |
| `cascade_chains` with gaps | **Out of scope** vs **In scope** | Gaps that are in-scope = must fix; gaps out-of-scope = document only |
| `action_items` priority 1-2 | **Objective** (enriched) | "Implement X **accounting for** temporal trap Y and branch Z" |
| `conflicts_resolved` | **Verified Facts Brief** | Verifier uses this to understand what was contentious |

### Example: Inventory Reorder Task

**Without PFD (old behavior):**
```
Objective: Calculate reorder point when stock < threshold
Inputs: current_stock, lead_time_days, daily_demand
```

**With PFD (new behavior):**
```
Objective: Calculate reorder point accounting for stock depletion during lead time
  and demand volatility across seasonal boundary

Inputs:
  - current_stock (DECAYING: depletes at ~daily_demand/day during lead_time)
  - lead_time_days (VOLATILE: supplier delays possible, see branch_point BP-2)
  - daily_demand (PERIODIC: seasonal shift occurs within lead_time window)

Acceptance emphasis:
  - Reorder triggers when projected_stock_at_delivery < safety_stock (not current_stock)
  - If lead_time exceeds supplier_timeout (BP-2), escalation fires
  - After restock event, demand_forecast recalculates using post-delivery seasonality
```

---

## 7. Handling Disagreement

Conflicts arise when agents classify the same variable differently. Resolution rules (applied by Synthesizer/Lead):

### 7.1 Conflict Types

| Conflict | Example | Resolution Rule |
|---|---|---|
| **Temporal says STATIC, State Mapper says "cascades from event Y"** | `config_threshold` — Temporal sees it as config (static), State Mapper sees it's recomputed nightly | **State Mapper wins.** If there's a mechanism that changes the value (even infrequently), it's not STATIC for PFD purposes. Temporal Modeler's "static" means "doesn't change on its own in the time horizon" — but external events (cron, admin action) can still mutate it. Reclassify as PERIODIC. |
| **Failure Analyst says "recovery exists", State Mapper says "missing cascade"** | Retry logic exists for the primary op, but derived caches aren't invalidated on retry | **Both are right — different scope.** The recovery handles the primary failure but not the cascade. PFD includes both: recovery=EXISTS for the branch, missing_cascade=TRUE for derivations. Action item: extend recovery to cascade. |
| **Temporal says HIGH confidence projection, Failure Analyst identifies race** | Stock depletion is deterministic (HIGH confidence), but concurrent orders create race | **Failure Analyst elevates uncertainty.** Confidence downgrades from HIGH to MEDIUM. The projection formula is correct in isolation but the race condition adds variance. PFD notes both the formula and the race hazard. |
| **All three agree on "no risk"** | Variable is STATIC + no branches affect it + zero derivations | **Exclude from PFD.** Unanimous "safe" = noise. Don't waste implementation attention on it. |

### 7.2 Tie-Breaking Principle

When no rule above applies: **the agent with the most specific file:line evidence wins.** A claim backed by `services/inventory.py:142` outweighs a claim backed by "generally, this type of variable tends to..." Specificity = trust.

---

## 8. Parallelism and Cost

### 8.1 Execution Model

All three specialized agents run **in parallel** (same message, concurrent spawns — identical pattern to `/audit` lenses). The Synthesizer runs sequentially after all return.

```
Time ────────────────────────────────────────────────►

RECON         ████████ (~30s, Lead reads code)

Temporal      ─────────████████████ (~45-90s)
Failure       ─────────████████████ (~45-90s)    ← PARALLEL
State Dep     ─────────████████████ (~45-90s)

Synthesizer   ─────────────────────████ (~15-30s, Lead logic, no spawn)

Total added latency: ~90-120s (limited by slowest agent)
```

### 8.2 Model Routing

| Agent | Model Tier | Rationale |
|---|---|---|
| Temporal Modeler | `coding` tier → `gpt-5.3-codex` (via codex_worker.sh) | Reads code, applies classification rules — mechanical with light reasoning |
| Failure Analyst | `hard` tier → `gpt-5.5` (via codex_worker.sh) | Branch enumeration requires imagination + trace reasoning — needs strong model |
| State Dependency Mapper | `coding` tier → `gpt-5.3-codex` (via codex_worker.sh) | Primarily grep + trace derivations — mechanical with moderate reasoning |
| Synthesizer | Lead (Opus 4.7) | Cross-domain reasoning, conflict resolution, risk ranking — hardest cognitive task |

### 8.3 Cost Per Invocation

| Component | Tokens (est.) | Cost model |
|---|---|---|
| Temporal Modeler | ~25k input + ~3k output | Codex flat-fee (ChatGPT Pro subscription) |
| Failure Analyst | ~25k input + ~5k output | Codex flat-fee |
| State Dep Mapper | ~25k input + ~3k output | Codex flat-fee |
| Synthesizer | ~12k additional context in Lead | Already running (Opus, no extra spawn) |

**Total incremental cost per non-trivial task:** Zero API tokens (all three agents route through Codex flat-fee subscription). Only cost is latency (~90s) and Lead context growth (~12k tokens for synthesis).

---

## 9. Quality Criteria

### 9.1 PFD Quality Checklist (machine-evaluable)

```yaml
quality_gates:
  completeness:
    - all_variables_classified: "Every variable in state_inventory has a temporal_class"
    - all_branches_traced: "Every branch_point has ≥1 downstream_effect"
    - all_cascades_sourced: "Every derivation has a computation_location (file:line)"

  specificity:
    - no_vague_risks: "Every trap_description references a concrete scenario, not 'might fail'"
    - file_line_coverage: "≥80% of claims cite file:line evidence"
    - projection_formulas: "DECAYING/ACCUMULATING vars have explicit rate or direction"

  actionability:
    - action_items_ranked: "action_items sorted by priority, each has evidence"
    - acceptance_testable: "Each action_item can be verified by an observable assertion"
    - no_obvious_items: "No action_item that says 'handle errors' without specifying WHICH error"

  parsimony:
    - static_excluded: "STATIC vars with 0 cascades + 0 branch exposure = excluded"
    - appendix_used: "Low-risk branches in appendix, not main flow"
    - total_action_items: "≤10 (if more, some are probably noise — re-rank)"
```

### 9.2 Shallow PFD Indicators (reject and re-run)

A PFD is **shallow** (and should be regenerated with more aggressive prompting) if:
- All variables are classified STATIC or VOLATILE with no projection
- Zero temporal traps identified (in a task that spans time — unlikely to be true)
- Branch points only list "500 error" and "timeout" — no PARTIAL or RACE
- Cascade depth is 0 for all variables (means State Mapper didn't trace derivations)
- Action items are generic ("add error handling", "validate inputs") without specifics

### 9.3 Lead Quality Check (before passing PFD to PLAN)

Lead performs a 30-second sanity check:
1. Does the time_horizon match the task? (If task is "fix reorder point" and horizon is "milliseconds" — something's wrong)
2. Are there at least 2 non-STATIC variables? (If everything is STATIC, Flow Designer was unnecessary)
3. Is there at least 1 CRITICAL or HIGH risk action item? (If not, was this task complex enough to need PFD?)
4. Do action_items reference specific files? (If all are abstract — too shallow)

If check fails → rerun the weakest agent with a more specific prompt (include Lead's critique).

---

## 10. Skip Criteria

Flow Designer is **overkill** and should be skipped when:

```python
def should_skip_flow_designer(task: str, recon_facts: dict) -> bool:
    """Return True if task doesn't benefit from temporal/branching analysis."""

    SKIP_PATTERNS = [
        # Pure text/config changes
        lambda t, r: r.get('files_changed', 0) <= 2 and r.get('lines_changed', 0) <= 20,
        # Rename/move operations
        lambda t, r: any(k in t.lower() for k in ['rename', 'move', 'typo', 'formatting']),
        # Documentation only
        lambda t, r: all(f.endswith('.md') for f in r.get('affected_files', [])),
        # Pure UI cosmetic (no state logic)
        lambda t, r: r.get('domain') == 'ui' and 'state' not in t.lower(),
        # Delete/remove (no temporal concern — thing goes away)
        lambda t, r: t.lower().startswith(('delete ', 'remove ', 'drop ')),
        # Single-file refactor that doesn't change behavior
        lambda t, r: 'refactor' in t.lower() and r.get('files_changed', 0) == 1,
    ]

    # MUST NOT skip when:
    FORCE_PATTERNS = [
        # Financial/trading logic
        lambda t, r: any(k in t.lower() for k in ['reorder', 'rebalance', 'reconcile',
                         'broker', 'fill', 'position', 'nav', 'settlement']),
        # Anything with explicit time dependency
        lambda t, r: any(k in t.lower() for k in ['schedule', 'cron', 'timeout', 'ttl',
                         'expiry', 'lead time', 'delivery', 'queue']),
        # Multi-service / distributed state
        lambda t, r: r.get('services_touched', 0) >= 2,
        # Database state changes
        lambda t, r: any(k in t.lower() for k in ['migration', 'schema', 'transaction',
                         'saga', 'compensat']),
    ]

    if any(f(task, recon_facts) for f in FORCE_PATTERNS):
        return False  # Never skip for these
    if any(f(task, recon_facts) for f in SKIP_PATTERNS):
        return True  # Safe to skip
    return False  # Default: run Flow Designer (conservative)
```

**Decision output by Lead:**
- Skip → `"Flow Designer: SKIP (reason: <pattern>). Proceeding to PLAN directly."`
- Run → `"Flow Designer: spawning 3 agents (Temporal Modeler, Failure Analyst, State Dependency Mapper) in parallel"`

---

## 11. Weaknesses and Mitigations

### 11.1 Honest Assessment

| Weakness | Severity | Mitigation |
|---|---|---|
| **Latency overhead** — 90-120s added to every non-trivial task | MEDIUM | Skip criteria exclude ~60% of tasks. For remaining 40%, the PFD prevents rework that costs 10-30min. Net positive for complex tasks, net negative if skip criteria are too conservative. |
| **Context bloat** — PFD adds ~2-4k tokens to the Artifact Contract | LOW | YAML is compact. STATIC variables excluded. Lead context grows by ~12k for synthesis but that's within Opus 1M budget. Monitor via compact_advisor. |
| **False positives in temporal classification** — agent marks something VOLATILE when it's actually stable | MEDIUM | Synthesizer cross-checks with State Mapper. If no mechanism exists to change the value AND no failure branch affects it, override to STATIC. File:line evidence required. |
| **Agents hallucinate file:line references** — cite non-existent code locations | HIGH | Synthesizer MUST spot-check 2-3 file:line references (quick Read). If >1 is wrong, reject that agent's output and rerun. This is the same pattern as audit lens verification. |
| **Diminishing returns on simple tasks** — Flow Designer on a CRUD endpoint is waste | LOW | Skip criteria handle this. If a CRUD endpoint has no temporal concern, no branches beyond 404/500, no cascades — it skips. If it DOES have these (e.g., CRUD on financial records with reconciliation) — it correctly runs. |
| **Information loss in synthesis** — three rich outputs compressed into one PFD | MEDIUM | Full agent outputs preserved in `state/flow_designer/` for debugging. PFD is the distilled version; Lead can reference originals if PLAN phase needs detail. |
| **Coordination cost if agents need context from each other** — e.g., Failure Analyst would benefit from knowing temporal classification | MEDIUM | By design, agents are independent (no inter-agent communication). This is a feature (parallelism, no deadlock) and a bug (Failure Analyst might identify a timeout branch without knowing the affected variable is DECAYING — making it worse). Synthesizer handles the cross-pollination. Trade-off accepted: parallel speed > perfect individual analysis. |
| **Codex model quality variance** — gpt-5.3-codex may produce shallower analysis than Opus would | MEDIUM | Failure Analyst already routes to gpt-5.5 (strongest Codex). If quality is consistently low, escalation ladder promotes to Opus. First 5 real-world runs will calibrate whether gpt-5.3-codex is sufficient for Temporal/StateDep or needs bump to gpt-5.4. |

### 11.2 What This Does NOT Solve

- **Cross-session temporal awareness** — PFD is per-task. If a decision made 3 sessions ago has a temporal consequence materializing now, PFD won't catch it unless RECON surfaces the old decision. Solution: rolling_memory entries with `temporal_deadline` field (future enhancement).
- **Real-time monitoring** — PFD identifies risks at design time but doesn't monitor runtime. Solution: PFD action items can include "add monitoring for X" as an implementation requirement.
- **Human temporal blindness** — if the user's task description omits the temporal context ("fix reorder point" without mentioning lead time), agents may not discover it unless the code itself reveals the time dependency. Mitigation: agents read code, not just task description.

---

## 12. Integration with Claude Booster Pipeline

### 12.1 Phase Placement

```
RECON → FLOW_DESIGNER (new) → PLAN → IMPLEMENT → VERIFY → AUDIT → DELIVER
```

Flow Designer runs AFTER RECON (it needs the Verified Facts Brief) and BEFORE PLAN (its output shapes the implementation plan). It's a sub-phase of PLAN preparation, not a separate top-level phase.

### 12.2 Hook Integration

No new hooks required. Flow Designer is invoked by Lead logic:
- **Trigger:** Lead completes RECON, evaluates `should_skip_flow_designer()`, decides to run
- **Execution:** Lead spawns 3 Codex workers via `codex_worker.sh` / `codex_sandbox_worker.sh` (read-only analysis, so `codex_worker.sh`)
- **Output storage:** `state/flow_designer/{task_id}_pfd.yaml` + raw agent outputs in same directory
- **Consumption:** Lead injects PFD into Artifact Contract before spawning Worker+Verifier

### 12.3 Interaction with Existing Commands

| Command | Interaction |
|---|---|
| `/consilium` | Can request Flow Designer for temporal analysis before debate. Bio-agents receive PFD as additional context. |
| `/audit` | Audit lenses can reference PFD to check: "did implementation actually handle the temporal traps identified?" New audit criterion. |
| `/hackathon` | All contestants receive same PFD in their Artifact Contract. Judge tests branch handling from PFD. |
| `/simplify` | Runs AFTER implementation. Can flag: "this code doesn't handle branch X from PFD" as a finding. |

### 12.4 Memory Integration

After task completion, extract from PFD for rolling_memory:
```python
# In memory_session_end.py, when task with PFD completes:
memorize(
    content=f"Task '{task}': temporal traps identified: {traps}. "
            f"Branches handled: {branches}. Cascades fixed: {cascades}.",
    memory_type="process_flow",
    category=classify_error(task),  # reuse existing taxonomy
    scope="project",
    tags=["flow_designer", "temporal", project_name]
)
```

This builds institutional knowledge: "in this project, reorder calculations always need lead-time projection" — future tasks get this context from `/start`.

---

## 13. Complete Example: Broker Partial Fill

**Task:** "Fix handle_fill() to correctly update positions on partial fills"

### RECON produces:
```
Verified Facts Brief:
  Project: Horizon Trading
  Stack: Python, asyncpg, IBKR API
  Key files: services/broker/execution.py, services/portfolio/positions.py, services/reconcile/nav.py
```

### Flow Designer spawns 3 agents. Results:

**Temporal Modeler output:**
```yaml
temporal_analysis:
  time_horizon: "50ms (fill notification) to 24h (settlement)"
  variables:
    - name: position_qty
      location: "services/portfolio/positions.py:89"
      classification: ACCUMULATING
      current_value: "SELECT qty FROM positions WHERE symbol=..."
      projected_at_effect: "+partial_qty immediately, +remaining_qty on next fill (unknown timing)"
      confidence: LOW
      temporal_trap: true
      trap_description: "Between partial fill and final fill, position_qty is in limbo state.
                         Any NAV calculation in this window uses incomplete position."
    - name: buying_power
      location: "services/portfolio/positions.py:112"
      classification: DECAYING
      current_value: "account.cash - sum(open_orders * avg_price)"
      projected_at_effect: "Decreases by partial_fill_value immediately, but open_order
                           reservation may not be proportionally released"
      confidence: MEDIUM
      temporal_trap: true
      trap_description: "Partial fill reduces cash but if order reservation isn't partially
                         released, buying_power is double-counted."
  critical_windows:
    - window: "partial_fill → final_fill (seconds to hours)"
      affected_variables: [position_qty, buying_power, nav]
      risk: "All derived metrics use incomplete position data"
```

**Failure Analyst output:**
```yaml
failure_analysis:
  entry_point: "services/broker/execution.py:handle_fill()"
  happy_path:
    - step: receive_fill
      operation: "Parse IBKR fill notification"
      state_after: "fill_event validated"
    - step: update_position
      operation: "positions.add_qty(symbol, filled_qty)"
      state_after: "position_qty += filled_qty"
    - step: update_cash
      operation: "account.debit(filled_qty * fill_price)"
      state_after: "cash reduced"
    - step: recalculate_nav
      operation: "nav.recalc()"
      state_after: "NAV reflects new position"

  branch_points:
    - step: receive_fill
      branches:
        - type: PARTIAL
          trigger: "filled_qty < order_qty (IBKR partial fill)"
          probability: LIKELY
          state_after: "position partially updated, order still open"
          downstream_effects:
            - affected: nav_calculation
              effect: "NAV uses partial position — technically correct but misleading for risk"
              blast_radius: USER
            - affected: rebalance_trigger
              effect: "Rebalance sees 'position achieved' threshold when only partial — may not trigger"
              blast_radius: SYSTEM
          recovery:
            mechanism: NONE
            exists_in_code: false
            gap: "No logic distinguishes 'position complete' from 'position in progress'"
        - type: RACE
          trigger: "Two partial fills arrive simultaneously (rare with IBKR but possible)"
          probability: UNLIKELY
          state_after: "position_qty += fill1 + fill2 but if not atomic, intermediate state visible"
          downstream_effects:
            - affected: position_qty
              effect: "Non-atomic update: concurrent NAV calc reads between two adds"
              blast_radius: RECORD
          recovery:
            mechanism: RETRY
            exists_in_code: false
            gap: "No locking on position update"
```

**State Dependency Mapper output:**
```yaml
state_dependency_map:
  primary_state:
    - name: position_qty
      location: "services/portfolio/positions.py:89"
      modified_by_task: true
    - name: account_cash
      location: "services/portfolio/positions.py:95"
      modified_by_task: true

  derivations:
    - source: position_qty
      derived: nav_total
      computation_location: "services/reconcile/nav.py:23"
      staleness_tolerance: SECONDS
      update_mechanism: EVENT
      missing_cascade: false
    - source: position_qty
      derived: rebalance_threshold_met
      computation_location: "services/portfolio/rebalance.py:67"
      staleness_tolerance: MINUTES
      update_mechanism: POLL
      missing_cascade: true
      missing_cascade_risk: "Rebalance checks every 5min. Between partial fill and next check,
                            threshold appears met when position is incomplete."
    - source: account_cash
      derived: buying_power
      computation_location: "services/portfolio/positions.py:112"
      staleness_tolerance: REALTIME
      update_mechanism: TRIGGER
      missing_cascade: true
      missing_cascade_risk: "buying_power subtracts open_order_reservations but doesn't
                            proportionally release on partial fill"

  hidden_consumers:
    - variable: position_qty
      consumer: "daily_reconcile_report"
      location: "services/reconcile/daily_report.py:45"
      update_awareness: POLLS
      risk_if_stale: "Report run during partial-fill window shows inconsistent positions"
```

### Synthesized PFD (excerpt):

```yaml
pfd_version: "1.0"
task: "Fix handle_fill() to correctly update positions on partial fills"
time_horizon: "50ms (fill notification) to 24h (settlement)"

action_items:
  - priority: 1
    risk: CRITICAL
    description: "Introduce 'pending_position' state: after partial fill, position is
                  marked PENDING until final fill or order cancel. Derived metrics
                  (rebalance_threshold, buying_power) must query CONFIRMED position only."
    source_agents: [temporal, failure, state_dep]
    evidence: "services/portfolio/rebalance.py:67 polls position_qty without
              distinguishing partial from complete"

  - priority: 2
    risk: HIGH
    description: "Proportionally release order reservation on partial fill.
                  buying_power = cash - (remaining_order_qty * price), not full original."
    source_agents: [temporal, state_dep]
    evidence: "services/portfolio/positions.py:112 subtracts full order reservation
              even after partial fill reduces the remaining quantity"

  - priority: 3
    risk: MEDIUM
    description: "Add advisory lock or atomic CAS on position update to prevent
                  concurrent partial fills from creating intermediate visible state."
    source_agents: [failure]
    evidence: "services/portfolio/positions.py:89 — plain UPDATE without locking"
```

This PFD then becomes part of the Artifact Contract for the Worker who implements the fix, and the Verifier builds tests that specifically check: "after partial fill, does rebalance_threshold use confirmed-only position?"

---

## 14. Implementation Roadmap

| Phase | Work | Estimate |
|---|---|---|
| **0. Validate** | Run Flow Designer manually (Lead executes prompts by hand) on 3 real past tasks that had temporal bugs. Check if PFD would have caught them. | 1 session |
| **1. Core** | Implement `should_skip_flow_designer()` in Lead logic. Store prompt templates. Wire Codex worker spawns. | 1 session |
| **2. Synthesizer** | Build PFD YAML generation in Lead reasoning. Variable alignment logic. Quality gate checks. | 1 session |
| **3. Integration** | Inject PFD into Artifact Contract template. Update `/audit` to check PFD compliance. Update memory extraction. | 1 session |
| **4. Calibrate** | Run for 2 weeks. Tune skip criteria. Adjust model routing if quality is low. Measure: did rework decrease? | Ongoing |
