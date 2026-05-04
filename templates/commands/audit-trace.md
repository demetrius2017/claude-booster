---
description: "Data continuity audit — trace a concept through all computation paths and find divergences. Like an electrical continuity test: same concept, different paths, divergence = defect."
argument-hint: "[<concept> | --all | --diff <path>]"
---

Parse `$ARGUMENTS` and route below. No argument → run **DISCOVER** mode. Single word or quoted string → run **TRACE** mode on that concept. `--all` → run **TRACE ALL** mode. `--diff <path>` → run **DIFF** mode.

---

## Pre-flight: Architecture check

Before anything else, verify the dependency graph exists:

```bash
# Check for architecture docs (use Read, not glob — zsh nomatch)
```

Read `ARCHITECTURE.md` at the project root. Then read `docs/dep_manifest.json`.

If **neither file exists**:
```
Error: /audit trace needs the dependency graph.
Run /architecture first to generate ARCHITECTURE.md and docs/dep_manifest.json.
```
Stop. Do not proceed.

If only one file exists: warn but continue — use what's available.

Build a **Verified Facts Brief** from the architecture docs:
- Project name and primary stack
- Number of components in dep_manifest.json
- List of all `writes_to` values across all components (these are candidate concepts)
- List of all `reads_from` values across all components
- Any invariants documented

This brief goes into every agent prompt below.

---

## MODE: DISCOVER (no argument)

When invoked without arguments, identify which data concepts are most worth tracing.

**Step 1 — Extract all traceable concepts:**

A concept is traceable when: a value appears in `writes_to` of at least one component AND in `reads_from` of at least one DIFFERENT component.

Parse dep_manifest.json components. For each component C:
- For each value V in `C.writes_to`:
  - Find all components R where V ∈ `R.reads_from` AND R ≠ C
  - If any R found → V is traceable; record (V, writer=C, readers=[R...])

**Step 2 — Score by divergence risk:**

Score each concept by:
- `reader_count` — more readers = more paths = higher divergence risk (weight: 3)
- `critical_writer` — if any writer has `"critical": true` in dep_manifest (weight: 2)
- `is_invariant_target` — if concept appears in any `invariants[].formula` or `enforced_in` (weight: 2)
- `is_forbidden_patch` — if concept appears in `data_patches_forbidden` (weight: 3)

Sort descending by total score. Take top 10.

**Step 3 — Present candidates:**

```
## Traceable Data Concepts

Found <N> traceable concepts. Top candidates by divergence risk:

 #  Concept                     Writers  Readers  Risk Score
 1  db:nav_snapshots            2        4        HIGH (score: 14)
 2  cache:portfolio_summary     1        3        HIGH (score: 11)
 3  db:orders                   1        2        MED  (score: 8)
...

To trace a specific concept:  /audit trace db:nav_snapshots
To trace all:                 /audit trace --all
To trace highest-risk only:   /audit trace (re-run, will auto-select #1)
```

If the user re-runs `/audit trace` without argument a second time in the same session (i.e., they've already seen the candidates list), auto-select concept #1 (highest score) and run TRACE mode on it. Do not ask again.

---

## MODE: TRACE (single concept)

Trace one data concept through all its computation paths.

The concept name comes from `$ARGUMENTS`. It may be:
- A dep_manifest key like `db:nav_snapshots` or `cache:portfolio_summary`
- A plain English name like `NAV` — in this case, find the closest matching concept in dep_manifest by substring/fuzzy match and confirm: `Tracing: db:nav_snapshots (matched "NAV")`

### Phase 1 — PROBE PLACEMENT

From dep_manifest.json, for the target concept C:

**Writers** = all components where C ∈ `writes_to`
**Readers** = all components where C ∈ `reads_from`

For each Writer W:
- Trace UP: what does W read? (`W.reads_from`) — these are data sources (Layer 2)
- Who calls W? (`W.called_by`) — these are triggers (Layer 1)

For each Reader R:
- Who calls R? look for components where R.id ∈ their `called_by`, or grep `dep_manifest.json` for R's id in other components' `reads_from`
- Trace DOWN: what does R write? (`R.writes_to`) — what does R feed downstream?

Group into **flow clusters**:
- **Display flows** — Readers whose callers are API endpoints, CLI handlers, or named components containing "dashboard", "view", "api", "route", "handler", "report", "export"
- **Write flows** — Writers whose callers are cron jobs, workers, sync jobs, event handlers (look for: "cron", "scheduler", "worker", "job", "sync", "ingest", "event")
- **External flows** — components that read from `external:*` or `api:*` in their `reads_from`

A component can belong to multiple clusters. Create 2–4 clusters; if only 1 makes sense, keep 1.

Print the probe placement summary:
```
## Probe Placement for: <concept>

Writers (Layer 1–2):
  - <component_id> [<file>] — called by: <callers>
  - ...

Readers (Layer 3–5):
  - <component_id> [<file>] — feeds: <downstream>
  - ...

Flow clusters:
  A. Display: <component_ids>
  B. Write:   <component_ids>
  C. External: <component_ids> (if any)
```

### Phase 2 — CODE TRACE (parallel Explore agents)

Spawn **2–3 parallel Explore agents** (one per flow cluster), all `subagent_type: "Explore"`, `model: "haiku"`.

Each agent receives:
- The Verified Facts Brief (project context, stack)
- Its specific flow cluster (list of component IDs + file paths from dep_manifest)
- The Artifact Contract below (same for all agents)
- The agent-specific search prompt

**Artifact Contract (identical for all Explore agents):**
```
Objective: Fill a Layer-5-to-Layer-1 trace table for the <concept> concept in the <flow_cluster> flow.
Artifact path: return structured trace table as text output (Lead will collect it)
Invocation: return output directly, no file write needed
Inputs: component file paths from dep_manifest.json + codebase
Expected observable behavior: one row per unique end-to-end path found; table columns: Flow, Layer 5, Layer 4, Layer 3, Layer 2, Layer 1
Out of scope: do not trace concepts other than <concept>; do not modify files; do not run tests
Environment constraints: read-only grep and file inspection only
Acceptance emphasis: Layer 3 (computation function name + file:line) and Layer 2 (exact source: table name, cache key, column) are mandatory; others best-effort
Affected downstream: Lead collects all tables for Phase 3 compare
Architecture map consulted: yes
```

---

### Agent A — Display Flow Tracer

**Prompt:**

```
You are Display Flow Tracer for /audit trace. Your job: follow the <concept> concept from its presentation layer (Layer 5) down to its raw data source (Layer 1), through display/API/UI paths only.

Verified Facts Brief:
<insert brief from pre-flight>

Target concept: <concept>
Your flow cluster: Display — components: <component_ids and file paths>

## Your task

For each component in your cluster, trace the call chain top-down and bottom-up:

1. Start from presentation: find the UI component, API endpoint, or CLI output that shows this concept to users.
   - For web: grep for the concept's display name, field name, or variable in *.tsx /*.jsx /*.html /*.py (template) files
   - For API: grep for the JSON field name in endpoint handlers
   - For CLI: grep for the print/output statement

2. Follow the call chain DOWN:
   - What function does the endpoint/component call?
   - What does THAT function call?
   - Continue until you reach a DB query, cache read, or external API call

3. For each level, record:
   - Layer 5: component/view name + file:line
   - Layer 4: API field name or function signature that returns the value + file:line
   - Layer 3: computation function (where is the value calculated/transformed?) + file:line
   - Layer 2: data source (exact table.column, cache key pattern, or external API endpoint) + file:line
   - Layer 1: who writes to that source (cron job name, event handler, or sync function) + file:line

## Search strategy

Start with the component file paths from dep_manifest, then grep outward:
- `grep -rn "<concept_keyword>" --include="*.py" --include="*.ts" --include="*.tsx" .`
- Follow import chains to find callers
- Look for the function name in other files to find callers of callers

## Output format

Return a plain-text trace table — one row per unique path found. Use | as separator:

FLOW: Display
PATH-1: <path_label> | <Layer5 component:file:line> | <Layer4 field:file:line> | <Layer3 function:file:line> | <Layer2 source:file:line> | <Layer1 writer:file:line>
PATH-2: ...

If a layer cannot be determined: write UNKNOWN:<what you tried>
If multiple distinct paths exist through Display (e.g., dashboard vs. export): produce one row per path.
If no display path found: write NO-DISPLAY-PATH-FOUND and explain why (component not yet built, or data not surfaced to UI).

Do NOT write prose. Return only the trace table rows.
```

---

### Agent B — Write Flow Tracer

**Prompt:**

```
You are Write Flow Tracer for /audit trace. Your job: trace how the <concept> value gets WRITTEN — from the raw data source (Layer 1) up to the point where it's persisted or cached.

Verified Facts Brief:
<insert brief from pre-flight>

Target concept: <concept>
Your flow cluster: Write — components: <component_ids and file paths>

## Your task

For each Writer component in your cluster:

1. Find the function that performs the write:
   - grep for INSERT INTO / UPDATE / SET / .save() / .create() near the concept's table/key name
   - For cache writes: grep for SET, SETEX, hset near the cache key pattern

2. Trace UP — what does this write function receive as input?
   - What is the caller (cron schedule, event, API POST handler)?
   - What does the caller read? (DB query, external API fetch, calculation)
   - What calculation produces the value being written?

3. For each level, record:
   - Layer 1: who/when triggers the write (cron schedule string or event name) + file:line
   - Layer 2: what source data is read before the write (table.column or external endpoint) + file:line
   - Layer 3: computation function (what formula/transform produces the written value) + file:line
   - Layer 4: the write interface (function name and signature that accepts the value) + file:line
   - Layer 5: NOT APPLICABLE for write flows — write "WRITE-FLOW (no presentation)"

## Search strategy

Start with file paths from dep_manifest. Key patterns:
- `grep -rn "INSERT\|UPDATE\|\.save\|\.create\|\.update" <file_path>` to find the write statement
- `grep -rn "<function_name>" --include="*.py" .` to find who calls the writer
- Look in scheduler configs, cron definitions, celery/apscheduler setup for trigger timing

## Output format

Return a plain-text trace table:

FLOW: Write
PATH-1: <path_label> | WRITE-FLOW | <Layer4 write_func:file:line> | <Layer3 compute_func:file:line> | <Layer2 source:file:line> | <Layer1 trigger:file:line>
PATH-2: ...

If a layer cannot be determined: write UNKNOWN:<what you tried>
Do NOT write prose. Return only the trace table rows.
```

---

### Agent C — External Flow Tracer (only if External cluster is non-empty)

**Prompt:**

```
You are External Flow Tracer for /audit trace. Your job: trace any paths where <concept> originates from or flows to an external system (broker API, third-party service, webhook).

Verified Facts Brief:
<insert brief from pre-flight>

Target concept: <concept>
Your flow cluster: External — components: <component_ids and file paths>

## Your task

For each External component:

1. Find where the external data is fetched or pushed:
   - HTTP client calls (requests.get, httpx.get, fetch, axios)
   - Broker API calls (ibapi, alpaca, binance, ccxt patterns)
   - Webhook handlers (incoming POST endpoints)

2. Trace the value from its external origin to where it's stored:
   - What field in the external response contains <concept>?
   - How is it transformed/normalized before storage?
   - Where is it stored (table.column, cache key)?

3. Also check: does this concept flow OUT to an external system?
   - Any place where <concept> is sent to an external endpoint
   - What value is sent — raw DB value or computed value?

## Output format

Return a plain-text trace table:

FLOW: External-In
PATH-1: <path_label> | <Layer5 N/A> | <Layer4 external_field:source_system> | <Layer3 transform_func:file:line> | <Layer2 stored_at:file:line> | <Layer1 external_api:file:line>

FLOW: External-Out  
PATH-1: <path_label> | <Layer5 N/A> | <Layer4 sent_field:target_system> | <Layer3 compute_func:file:line> | <Layer2 source:file:line> | <Layer1 trigger:file:line>

Do NOT write prose. Return only the trace table rows.
```

---

### Phase 3 — COMPARE

After **all Explore agents return**, collect their trace tables.

**Step 1 — Normalize the trace table:**

Merge all PATH rows into a single table. Remove duplicate rows (exact matches). For UNKNOWN entries, note them but do not drop — unknown paths are potential risk.

**Step 2 — Group by source, then compare across groups:**

Group all paths by their Layer 2 value (data source). Paths with identical Layer 2 belong to the same "source cluster."

- If ALL paths land in one cluster → Layer 2: CONSISTENT for all. Proceed to Layer 3 check within the cluster.
- If paths split across multiple clusters → FINDING for each cross-cluster pair: type=source-divergence, severity=HIGH.
- If one cluster is cache and another is DB (without documented cache-as-of-write guarantee) → FINDING: type=stale-cache-risk, severity=HIGH.

Within each source cluster, check Layer 3 (computation):
- Same function name and file? → Layer 3: CONSISTENT
- Different functions? → compare function bodies if possible (are they identical logic? different rounding? different formula?)
  - Identical logic in two different functions → FINDING: type=computation-duplication, severity=MED
  - Different logic (different formula, different rounding) → FINDING: type=computation-divergence, severity=HIGH
  - One path has no computation (raw value passthrough) vs. one that transforms → FINDING: type=transform-asymmetry, severity=MED

**Step 3 — Check against documented invariants:**

For each FINDING, check ARCHITECTURE.md `## Invariants` table and dep_manifest.json `invariants` section:
- If the divergence is explicitly described as a design decision in an invariant → mark as `documented: yes (INV-XX)` — noted, not a defect
- If not documented → mark as `documented: no → DEFECT`

**Step 4 — Verify consistent pairs:**

For every pair where both Layer 2 and Layer 3 are CONSISTENT → add to the "Verified Consistent" section of the report. This is positive evidence: "we checked, and these paths agree."

### Phase 4 — REPORT

**Step 1 — Write the report file:**

Path: `reports/audit_trace_<concept_slug>_<date>.md`

Where `<concept_slug>` = concept name with `:` and `/` replaced by `_` (e.g. `db_nav_snapshots`), `<date>` = today's date (YYYY-MM-DD).

Write the report using the template below.

**Step 2 — Print summary to stdout:**

```
/audit trace: <concept>
  Flows traced: <N> (Display: <n>, Write: <n>, External: <n>)
  Findings: <total> (<HIGH count> HIGH, <MED count> MED, <LOW count> LOW)
  Verified consistent pairs: <N>
  Unknown layers: <N> (paths where we couldn't determine source or computation)
  Report: reports/audit_trace_<concept_slug>_<date>.md
  Status: PASS (no HIGH findings) | FINDINGS REQUIRE ACTION (<N> HIGH)
```

**Step 3 — Exit signal:**

If any HIGH findings exist: print `EXIT: 1 — HIGH findings require action.`
If no HIGH findings: print `EXIT: 0 — no HIGH findings.`

---

## MODE: TRACE ALL (`--all`)

Run TRACE mode for every traceable concept discovered (per DISCOVER step 1). Process them in descending risk-score order.

Before starting: print the concept list with count.
```
/audit trace --all: found <N> traceable concepts. Tracing in risk-score order.
```

For each concept: run Phases 1–4 of TRACE mode sequentially. Write one report per concept.

After all concepts:
```
/audit trace --all complete.
  Concepts traced: <N>
  Reports written: reports/audit_trace_*_<date>.md
  Total findings: <N> HIGH, <N> MED, <N> LOW
  Overall status: PASS | FINDINGS REQUIRE ACTION
```

If `--all` produces more than 10 concepts, pause after the first 5 and print:
```
Traced 5 of <N> concepts. Continue with remaining <N-5>? (y/n)
```
Wait for user confirmation before proceeding. This prevents runaway cost on large codebases.

---

## MODE: DIFF (`--diff <path>`)

Compare the current trace results against a previous report to show delta in findings.

Parse `--diff <path>` where `<path>` is an absolute or relative path to a previous audit_trace report.

**Step 1 — Read the previous report:**
Read the file at `<path>`. Parse the frontmatter (`concept`, `date`, `high_findings`) and the `## Findings` section.

**Step 2 — Run current trace:**
Run TRACE mode for the same concept (read from the previous report's frontmatter `concept:` field). Complete Phases 1–4 normally.

**Step 3 — Compute delta:**

Compare findings between old and new:
- **Resolved findings:** present in old report, absent in new (fix confirmed)
- **New findings:** absent in old report, present in new (regression or newly discovered)
- **Persistent findings:** present in both (not yet fixed)
- **Changed severity:** same finding but different severity (e.g., HIGH → MED after partial fix)

**Step 4 — Print delta report:**

```
/audit trace --diff: <concept>
  Previous report: <path> (<old date>)
  Current trace:   <today's date>

  Resolved (fixed):   <N>
    - FINDING-1: <title> [was HIGH]
  
  New (regressions):  <N>
    - FINDING-X: <title> [now HIGH]
  
  Persistent:         <N>
    - FINDING-2: <title> [still HIGH — <age in days> days open]
  
  Severity changes:   <N>
    - FINDING-3: HIGH → MED after partial fix

  Net delta: -<resolved> +<new>
  Status: IMPROVING | REGRESSING | STABLE
```

Write a new report file for the current trace (standard naming). The previous report is not modified.

---

## Report template

Write this exact structure to `reports/audit_trace_<concept_slug>_<date>.md`:

```markdown
---
type: audit
subtype: trace
concept: <data concept name, e.g. db:nav_snapshots>
date: <YYYY-MM-DD>
status: <PASS | FINDINGS>
high_findings: <count>
med_findings: <count>
flows_traced: <count>
verified_consistent: <count>
---

# Audit Trace: <Concept> — <Date>

**Project:** <project name>
**Concept:** <concept name from dep_manifest>
**Traced by:** /audit trace command
**Flows:** <Display | Write | External — list which were found>

---

## Trace Table

| Flow | Layer 5 (Presentation) | Layer 4 (Interface) | Layer 3 (Computation) | Layer 2 (Source) | Layer 1 (Writer) |
|------|------------------------|--------------------|-----------------------|------------------|------------------|
| Display / <path_label> | <component:file:line> | <field:file:line> | <function:file:line> | <table.col:file:line> | <trigger:file:line> |
| Write / <path_label> | WRITE-FLOW | <write_func:file:line> | <compute_func:file:line> | <source:file:line> | <trigger:file:line> |
| External-In / <path_label> | N/A | <ext_field:system> | <transform:file:line> | <stored_at:file:line> | <external_api> |

_UNKNOWN entries indicate layers that could not be determined from static analysis. These require manual investigation._

---

## Findings

<!-- If no findings: write "No findings — all traced paths are consistent." -->

### FINDING-1: <short descriptive title>

- **Flow A:** <flow_label> → <Layer3 function> → <Layer2 source>
- **Flow B:** <flow_label> → <Layer3 function> → <Layer2 source>
- **Divergence at:** Layer <N> — <what is different>
- **Impact:** <what can go wrong in production; e.g. "Dashboard shows NAV computed from stale cache while reconcile reads from live DB — divergence window = cache TTL">
- **Severity:** HIGH | MED | LOW
- **Documented:** yes (ARCHITECTURE.md <INV-ID>) | no → **DEFECT**

### FINDING-2: ...

---

## Verified Consistent

The following flow-pairs were checked and found consistent — same source, same computation:

| Flow A | Flow B | Layer 2 match | Layer 3 match | Evidence |
|--------|--------|---------------|---------------|----------|
| Display/dashboard | Write/cron_snapshot | db:nav_snapshots | snapshot_nav():services/nav.py | both read db:nav_snapshots; both call snapshot_nav() |

_A consistent pair is positive evidence: these paths cannot diverge from each other._

---

## Unknown Layers

Paths where static analysis could not determine source or computation. Manual investigation required before declaring PASS.

| Path | Unknown at | What was tried | Risk |
|------|-----------|----------------|------|
| Display/export | Layer 2 | Grepped export handler — calls calculate_export() but source not traceable without runtime | MED |

---

## Action Items

| # | Finding | File:line | Fix description | Priority |
|---|---------|-----------|-----------------|----------|
| 1 | FINDING-1 | services/nav.py:142 | Unify computation: replace inline formula with call to snapshot_nav() | HIGH |
| 2 | FINDING-2 | ... | ... | MED |

---

## Methodology Notes

- Layers discovered from: ARCHITECTURE.md + docs/dep_manifest.json
- Code traced by: <N> parallel Explore agents (haiku)
- Comparison method: pairwise Layer 2 + Layer 3 match
- Invariants checked against: ARCHITECTURE.md §Invariants + dep_manifest.json .invariants
- Unknown layers: <N> (see §Unknown Layers above)
- This trace covers static-analysis paths only. Dynamic dispatch, runtime polymorphism, and A/B flags may create additional paths not visible here.
```

---

## Severity reference

| Severity | Condition | Example |
|----------|-----------|---------|
| **HIGH** | Different Layer 2 source for the same concept in two paths | Dashboard reads from `cache:portfolio` (potentially stale), reconcile reads from `db:positions` (live) — they can diverge |
| **HIGH** | Different Layer 3 formula/rounding for the same source | Two functions both read `db:orders.fill_price` but one uses `Decimal` rounding, one uses float — results differ by epsilon |
| **MED** | Same source, different computation function (not formula — just two separate implementations of same logic) | `calculate_nav()` and `compute_nav()` exist; both correct but duplication means they can drift |
| **MED** | Transform asymmetry — one path transforms raw value, another passes it through raw | One endpoint normalizes commission sign with `abs()`, another does not |
| **LOW** | Naming inconsistency only — same source, same computation, different field names in output | `nav` in one endpoint, `net_asset_value` in another — cosmetic, not data divergence |

---

## Error handling

**dep_manifest.json missing `components` key:**
```
Error: docs/dep_manifest.json exists but has no "components" key.
The file may be from an older format. Run /architecture --update to regenerate.
```

**Concept not found in dep_manifest:**
```
Concept "<X>" not found in docs/dep_manifest.json.
Available concepts (writes_to values): <list top 10>
Did you mean: <closest fuzzy match>?
```

**No traceable concepts found (dep_manifest has no cross-component flows):**
```
No traceable concepts found.
All writes_to values are consumed only by their own component, or dep_manifest has only one component.
Either the project has no inter-component data flow, or dep_manifest.json is incomplete.
Run /architecture --update to re-analyze component dependencies.
```

**Explore agent returns empty output:**
If an agent returns nothing or only "NO-DISPLAY-PATH-FOUND": note it in the report's Unknown Layers section. Do not re-run the agent — empty output means the flow genuinely does not exist in that cluster (or the component is not yet implemented).

**Concept spans too many readers (>10):**
```
Warning: <concept> has <N> readers — full trace will spawn many agents.
Proceeding with top 5 readers by call depth. Use /audit trace --all for complete coverage.
```
Rank readers by `called_by` chain length (deeper = more likely presentation layer) and trace the top 5.

---

## Integration notes

- `/architecture` must run before `/audit trace` — it generates the dep_manifest.json that this command reads.
- `/audit trace` reports live in `reports/` alongside consilium and handover reports.
- After a bug fix that resolves a HIGH finding: run `/audit trace --diff reports/audit_trace_<concept>_<old_date>.md` to confirm the finding is gone.
- The report's frontmatter `high_findings:` count is machine-readable — future `/start` telemetry can surface open HIGH findings at session start.
- This command does NOT modify any code or architecture docs. It is read-only + report-write only.
