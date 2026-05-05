---
name: consilium_2026-05-05_architecture_stability
description: "Consilium: per-component git-based stability for dep_manifest.json — dual-signal model, /architecture verify command"
type: consilium
date: 2026-05-05
preserve: true
---

# Consilium — Architecture Stability Model

**Date:** 2026-05-05
**Topic:** Per-component stability classification for dep_manifest.json
**Agents:** Systems Architect, Data Scientist, DevOps Engineer, Product/UX Designer, GPT-5.5

## Task Context

dep_manifest.json describes a project's dependency graph (24-30 components). Problem: manifest can be stale — describes yesterday's code, not today's. Real incident in Horizon: manifest said `auto_fix_discrepancies_j2t` was an active writer, but the function was deliberately disabled 3 commits ago. Claude's audit treated it as active, risking re-enabling a disabled path.

User insight: architecture docs should have "release" vs "dev" status per BRANCH (component), not per file. Stability should be derived from git mutation history — components unchanged for many commits are empirically stable.

## Verified Facts Brief

- dep_manifest.json schema: components with file, reads_from, writes_to, called_by, critical, notes
- 5 consumers: dep_guard.py, financial_dml_guard.py, paired-verification.md, audit-trace.md, /start
- `git log -S` per-component: 0.026s, works perfectly, stdlib subprocess
- Claude_Booster manifest: 1 commit (brand new). Horizon: 2 commits, 1304 total repo commits
- Source velocity >> manifest velocity: 6 code commits vs 2 manifest commits in same period
- Available infra: subprocess, _gate_common.py, arch_freshness.py pattern

## Agent Positions

| Agent | Position | Key Insight | KPI |
|-------|----------|------------|-----|
| Systems Architect | Dual signals (manifest + source), drift_risk | Cold-start: source file age fallback | Drift detection recall ≥80% |
| Data Scientist | Calendar time primary, 2×2 matrix, composite score | "code-drift" = highest risk cell | False-positive on stable <10% |
| DevOps Engineer | Ephemeral cache, ref-based invalidation | Don't commit stability JSON | Cold computation <2s / 30 components |
| Product/UX | Continuous detection, rename to /architecture verify | Surface warnings, don't block | False-trust incidents = 0 |
| GPT-5.5 | Hybrid: manifest + git discovery + paired-verification | Manifest absence ≠ safety; unknown = unknown risk | Silent risky edits prevented |

## Convergence (all 5)

1. Source file stability must be primary signal, not just manifest entry stability
2. Don't commit stability data to git — derived view or ephemeral cache
3. Cold-start is the immediate reality — design for it FIRST
4. "code-drift" (source changed, manifest didn't) = highest-risk classification
5. dep_guard should warn, not block, on stability signals

## Decision

### Signal Design

Dual-signal stability per component:

**Signal 1 — Source file recency (weight 0.7):** `git log --follow -1 --format=%at -- <source_file>` → days since last code change. This is ground truth — works from day one, no cold-start problem.

**Signal 2 — Manifest churn rate (weight 0.3):** `git log -S "<component>" --since="90 days ago" -- docs/dep_manifest.json` → commits/90d. Time-normalized, not raw count.

### Risk Classification (2×2 Matrix)

| Source stable (≥30d) | Source unstable (<30d) |
|---|---|
| **STABLE** (manifest stable) | **CODE-DRIFT** ⚠ (manifest stable) |
| **INTERFACE-FLUX** (manifest unstable) | **DEV** (manifest unstable) |

- **STABLE**: Trust manifest entries as assertions
- **CODE-DRIFT**: Highest risk — source changed but manifest didn't catch up. RECON must cross-check with code.
- **INTERFACE-FLUX**: Component works but API/contract is being refactored. Manifest entries reflect ongoing changes.
- **DEV**: Everything recently changed. Treat manifest as navigation hint only.

Connectivity (number of dependents) acts as severity MULTIPLIER, not stability input. A CODE-DRIFT component with 8 consumers is louder than one with 1.

### Cold-Start Strategy

When manifest has < 5 commits touching a component:
- Use source file age as primary signal: `stability_basis: "source_history"`
- Emit "insufficient manifest history" annotation
- Falls back to source-only risk assessment
- Converges to dual-signal when manifest accumulates history

When a source file has NO manifest entry at all:
- Run `git grep`/`git log -S` discovery for consumers
- Classify as "unknown risk" (NOT "low risk")
- Warn: "Manifest has no entry for X. Discovered consumers: Y, Z."
- Every discovery = opportunity to improve manifest

### Storage

Ephemeral `.cache/arch_stability.json` (gitignored). Ref-based invalidation: recompute when `git rev-parse HEAD` differs from `computed_at_ref`. Guards recompute live from git; never read cached JSON for gating decisions.

### Command

`/architecture verify` — explicit full recomputation + report. Also runs automatically during RECON (computed on demand, cached for session).

Output format:
```
=== Architecture Stability Report ===
STABLE (12):   compute_portfolio_return, snapshot_nav, ...
CODE-DRIFT (3): auto_fix_discrepancies_j2t ⚠, ...
DEV (5):       j2t_post_apply_reconcile, ...
UNMAPPED (2):  backend/new_feature.py, backend/utils/helper.py

Overall: 60% stable, 15% code-drift ⚠, 25% dev
Manifest coverage: 28/30 source files mapped
```

### Integration Points

| Consumer | How it uses stability |
|----------|---------------------|
| RECON (paired-verification) | STABLE entries → trust for Architecture constraints. CODE-DRIFT → cross-check with code first |
| audit-trace | STABLE divergence → real finding. CODE-DRIFT/DEV divergence → architecture-docs-stale |
| dep_guard.py | Warn (not block) when editing CODE-DRIFT component without review |
| arch_freshness.py | Suppress warning for non-interface edits (GPT's recommendation) |
| /start | Auto-compute stability during session init, surface ⚠ signals in plan |

### Implementation Plan

1. `templates/scripts/arch_stability.py` (~200 LOC) — core computation engine
   - Dual-signal calculation per component
   - `git log -S` for manifest churn, `git log --follow` for source recency
   - Shallow-clone guard (`git rev-parse --is-shallow-repository`)
   - Ref-based cache in `.cache/arch_stability.json`
   - ThreadPoolExecutor above 20 components
   - Output: JSON report + human-readable table

2. `templates/commands/architecture.md` — add `verify` subcommand spec (~100 LOC addition)

3. Integration updates:
   - paired-verification.md RECON: read stability during Architecture constraints population
   - audit-trace.md: use stability for finding classification
   - /start: auto-compute stability alongside architecture check

## Rejected Alternatives

| Alternative | Reason |
|-------------|--------|
| Stored stability field in dep_manifest.json | Chicken-and-egg: writing stability creates mutation that resets it |
| Raw commit count thresholds (10/3/1) | Conflates velocity with time; team-dependent |
| Blocking based on stability | Derived metric with failure modes → rage-bypass |
| `/architecture stabilize` name | Implies mutation, not assessment |
| Manifest-only trust model | Manifest brand new; absence as safe = silent gaps |
| Complex scoring with connectivity | Connectivity = severity multiplier, not stability input |

## Risks

1. **Source file rename breaks tracking**: `git log --follow` handles this, but has limits with complex restructuring
2. **Generic component names**: `git log -S "get"` overmatch. Mitigation: use full qualified names from manifest
3. **Calendar time thresholds arbitrary**: 30-day "stable" cutoff needs calibration per project
4. **Discovery false positives**: `git grep` may find test files, docs, comments as "consumers"

## Implementation Recommendations

- Ship arch_stability.py as standalone first, integrate into hooks incrementally
- Start with /architecture verify command, add auto-computation later
- Use 30-day default for stable threshold, make configurable per-project
- Do NOT change dep_guard.py blocking behavior based on stability — warn only
