---
name: consilium_2026-05-04_systemic_thinking
description: "Consilium: systemic thinking enforcement — dependency maps, DML gates, architecture documentation, fix-producer-not-data rule"
type: consilium
date: 2026-05-04
preserve: true
---

# Consilium — 2026-05-04: Systemic Thinking Enforcement

## Problem Statement

Claude правит функции точечно, без карты зависимостей. Починил A — сломал B, C, D. Патчит данные в БД вместо починки функции-производителя. Теряет синхронизацию между компонентами через сессии. Результат: петли рассинхронизации неделями, $93K NAV divergence.

**Корневая причина:** все текущие гейты — процедурные (фаза, наличие таска, evidence markers, delegation budget). НОЛЬ гейтов на системное мышление. "Think Two Steps Ahead" в core.md — чистая проза без enforcement hook.

## Participants

| Agent | Bio | Model |
|-------|-----|-------|
| RECON (×3) | Gate inventory, rule gaps, hook architecture | Haiku 4.5 |
| Systems Architect | C4, ADR, dependency mapping, 20y complex platforms | Sonnet 4.6 |
| Financial Engineer | Reconciliation, audit trails, data lineage, SOX, 15y trading | Sonnet 4.6 |
| Tooling Engineer | Git hooks, CI gates, static analysis, pre-commit frameworks | Sonnet 4.6 |
| Process Consultant | ADR, C4, Living Documentation, Docs-as-Code | Sonnet 4.6 |
| GPT-5.5 (PAL) | External independent validation | GPT-5.5 |
| Dmitry (user) | RECON→IMPLEMENT→VERIFY pipeline integration proposal | — |

## Verified Facts Brief (from RECON)

| Fact | Evidence |
|------|----------|
| Zero systemic-thinking gates | All PreToolUse hooks checked: phase_gate, require_task, verify_gate, delegate_gate — none check dependencies |
| "Think Two Steps Ahead" = prose only | core.md lines 30-50: says "mentally, not in output" — explicitly allows skipping evidence |
| Hook sees path + transcript, not content | verify_gate.py, phase_gate.py source: `tool_input.file_path`, no `file_content` field |
| <500ms latency budget for hooks | Hook architecture: PreToolUse is synchronous blocking |
| No architecture docs exist | grep for mermaid/C4/ADR/diagram across all rules/ and commands/ = zero matches |
| Artifact Contract missing "affected systems" | paired-verification.md: has "Out of scope" but NOT "affected downstream" |
| require_task checks existence, not content | require_task.py: regex for "TaskCreate" literal in transcript, ignores description |

## Agent Positions

| Agent | Core Position | Key Insight | Unique Contribution |
|-------|---------------|-------------|---------------------|
| Systems Architect | Hybrid SYSTEM_MAP.md (Mermaid) + dep_manifest.json (machine). Hook reads manifest, not AST | "Static analysis generates graph noise — you need the data propagation graph, which static analysis cannot see (DB-mediated deps)" | `data_patches_forbidden` list in manifest enables DML guard |
| Financial Engineer | financial_data_manifest.yaml with derived_readonly_columns + invariants. DML guard is highest leverage | "The $93K is not an AI problem — same failure at every firm without controls. Controls must be at tool call boundary, not LLM level" | 5 domain invariants: NAV sum, ledger append-only, position-broker match, no cash-negative, withdrawal ≤ balance |
| Tooling Engineer | 4 concrete gate designs with latency estimates. Task content validation = lowest risk, highest ROI | "dep_guard.py feasibility: hooks see paths not content — enforcement is procedural (did you consult the manifest?) not semantic" | Pseudocode for all 4 gates; honest false-positive analysis; DML gate recommended as warn-only due to 60-70% miss rate on file-based SQL |
| Process Consultant | ARCHITECTURE.md with C4 L1+L2 + dependency table. ADR for load-bearing decisions. Martraire's "Living Documentation" as primary reference | "A dependency table is O(1) lookup — Claude checks 'what calls this?' before editing. Diagrams show topology; tables capture 'what breaks what'" | Book recommendations: Martraire (primary), Nygard, Bass, Keeling |
| GPT-5.5 | Confirmed direction: machine-readable manifest + PreToolUse enforcement + DML protection. Hook-level enforcement > prose rules | Convergence confirmation across Claude and GPT reasoning chains | Independent validation of the architecture |
| Dmitry | Integrate arch map into RECON→IMPLEMENT→VERIFY: read at RECON, update after VERIFIER, with versioning | "The pipeline already has phases — use them. Don't add new ceremony, embed into existing flow" | Pipeline integration design — the missing glue between documentation and enforcement |

## Decision

### Architecture: Three Layers

```
Layer 1: ARCHITECTURE.md (human + AI readable)
  └── C4 Level 2 Mermaid diagram + Dependency Table
  └── Git-versioned, read at /start and RECON
  
Layer 2: dep_manifest.json (machine readable)
  └── Functions → feeds/fed_by/data_stores/critical
  └── data_patches_forbidden list
  └── Read by hooks at PreToolUse time (<5ms)
  
Layer 3: Gates (enforcement)
  └── financial_dml_guard.py — blocks DML on protected tables
  └── dep_guard.py — blocks edits on critical files without review evidence
  └── require_task.py extension — validates task content
  └── arch_freshness.py — warns when arch doc not updated
```

### Pipeline Integration (Dmitry's proposal, adopted)

```
RECON phase:
  ├── Read ARCHITECTURE.md (mandatory, like README)
  ├── Read dep_manifest.json for the functions in scope
  └── List affected downstream in the task brief

IMPLEMENT phase:
  ├── dep_guard.py blocks edit on critical files without review evidence
  ├── financial_dml_guard.py blocks DML on protected tables
  └── Worker's Artifact Contract includes "Affected downstream:" field

VERIFY phase:
  ├── Verifier tests observable behavior (unchanged)
  └── Verifier ALSO checks: if function is in dep_manifest, do downstream still work?

POST-VERIFY (new):
  ├── If interfaces changed → update ARCHITECTURE.md dependency table
  ├── If new function added to critical path → update dep_manifest.json
  ├── Version bump in ARCHITECTURE.md header (date + session)
  └── This is a separate Worker spawn, not inline Lead edit
```

### Artifact Contract Extension

Add to paired-verification.md Artifact Contract template:

```
Affected downstream: <functions/systems that consume this function's output>
Architecture map consulted: <yes/no — dep_manifest.json read>
Data stores touched: <tables written to, with producer function reference>
```

### "Fix Producer Not Data" Rule

Encode in quality-no-defects.md as Layer 2 extension:

> **Direct DB writes on derived fields are defects.** If data in the DB is wrong, the fix is the function that produces it. `UPDATE` on a derived-readonly column (per dep_manifest.json `data_patches_forbidden`) is a Three Nos violation — you are passing defective data downstream by masking the producer bug.

Gate enforcement: `financial_dml_guard.py` blocks `UPDATE`/`DELETE` on protected tables in Bash commands. Bypass requires explicit `CLAUDE_BOOSTER_DML_ALLOWED=1` with documented reason.

## Implementation Plan — Priority Order

### Tier 1 — Ship this week (gates + manifest)

| # | Deliverable | Mechanism | Effort | Files |
|---|-------------|-----------|--------|-------|
| 1 | `dep_manifest.json` template | JSON schema, per-project | 1h | `templates/dep_manifest.json` |
| 2 | `financial_dml_guard.py` | PreToolUse on Bash, exit 2 on protected DML | 3h | `templates/scripts/financial_dml_guard.py` |
| 3 | `require_task.py` content validation | Extend existing hook to check "affected:" field | 2h | `templates/scripts/require_task.py` |
| 4 | `quality-no-defects.md` update | Add "fix producer not data" Layer 2 section | 30min | `~/.claude/rules/quality-no-defects.md` |

### Tier 2 — Ship next week (documentation + pipeline)

| # | Deliverable | Mechanism | Effort | Files |
|---|-------------|-----------|--------|-------|
| 5 | `ARCHITECTURE.md` template | C4 L2 Mermaid + dependency table | 2h | `templates/ARCHITECTURE.md` |
| 6 | Pipeline integration rule | RECON reads arch map, post-VERIFY updates it | 1h | `~/.claude/rules/paired-verification.md` |
| 7 | `dep_guard.py` | PreToolUse on Edit/Write, checks manifest + transcript | 3h | `templates/scripts/dep_guard.py` |
| 8 | `arch_freshness.py` | PostToolUse warning when arch doc not updated | 2h | `templates/scripts/arch_freshness.py` |

### Tier 3 — Ship in 2 weeks (practice + domain invariants)

| # | Deliverable | Mechanism | Effort | Files |
|---|-------------|-----------|--------|-------|
| 9 | ADR template + practice | `docs/adr/` directory, minimal template | 1h | `templates/docs/adr/ADR-TEMPLATE.md` |
| 10 | Reconciliation gate | Extend require_evidence.py for financial edits | 3h | `templates/scripts/require_evidence.py` |
| 11 | Domain invariant checks | NAV sum, ledger append-only, position-broker | 4h | Per-project scripts |
| 12 | Artifact Contract "Affected downstream" | Extend paired-verification.md | 30min | `~/.claude/rules/paired-verification.md` |

## Rejected Alternatives

| Alternative | Reason for rejection | Who proposed / rejected |
|-------------|---------------------|----------------------|
| Auto-generated dependency graph from AST (pydeps, importlab) | Too slow for 500ms hook budget; generates noise; misses DB-mediated deps which are the actual failure mode | Architect proposed, Tooling rejected |
| C4 at Component level | Decays too fast to maintain; Container level is the right granularity | Architect, Process both rejected |
| Figma for diagrams | 6 calls/month on Starter plan; rate-limited; not version-controlled | Process rejected |
| Hard DML block (exit 2) on ALL SQL | 60-70% miss rate on file-based SQL creates false security; recommend warn-only for non-protected tables | Tooling proposed warn-only |
| Full transcript semantic analysis | Hooks can't parse "did the agent truly understand dependencies" — enforcement must be procedural | Tooling, Architect consensus |
| Prohibit all direct DB writes | Legitimate uses: migrations, seed data, one-time corrections. Targeted `data_patches_forbidden` is better | Financial Engineer refined |
| Weekly architecture review cadence | Decouples update from understanding; trigger-based (per session) is more effective | Process — citing Martraire |

## Risks

| Risk | Mitigation |
|------|------------|
| dep_manifest.json staleness (manual maintenance) | Post-VERIFY update step + arch_freshness.py warning |
| False positives on DML guard (bash variables, heredocs) | Pre-filter regex + `CLAUDE_BOOSTER_DML_ALLOWED=1` bypass |
| Gate fatigue (too many blocks → agent learns to bypass) | Graduated rollout; Tier 1 only, then Tier 2 after validation |
| ARCHITECTURE.md grows too large (>15 nodes = wall) | Nygard heuristic: split by domain at 15 nodes |
| ADR sprawl | Restrict to decisions where tradeoff was real and alternative was seriously considered |
| Task content validation false positives on simple tasks | Allowlist for docs/chore tasks (like existing evidence gate) |

## Recommended Reading

| Book | Author | Why relevant | Priority |
|------|--------|-------------|----------|
| Living Documentation | Cyrille Martraire | Core thesis: docs synchronized with code, staleness immediately obvious | **Primary** |
| Release It! | Michael Nygard | Stability patterns, failure mode mapping, "architecture = decisions you can't easily change" | High |
| Documenting Software Architectures | Bass, Clements, Kazman | Views and Beyond framework, interface documentation, module/C&C views | Medium |
| Design It! | Michael Keeling | ADR practice, "just enough architecture," practical workshop format | Medium |
| Building Evolutionary Architectures | Ford, Parsons, Kua | Fitness functions, architectural governance (revisit when more automation) | Later |

## Session Reference

This consilium was triggered by Dmitry's observation that Claude:
1. Не мыслит системно — правит точечно без карты зависимостей
2. Патчит данные вместо починки функции-производителя
3. Теряет синхронизацию через сессии
4. Не имеет живой документации архитектуры

Kill date context: Claude Booster 2026-05-22 (18 days). This consilium's Tier 1 deliverables are designed to ship within that window as demonstrable "positive results."
