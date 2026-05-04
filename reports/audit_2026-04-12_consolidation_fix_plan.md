---
name: Audit 2026-04-12 — Consolidation Regression & Fix Plan
description: Root cause audit of the Karpathy compounding pattern's consolidation stage after the dry-run exposed a half-applied audit fix; synthesized fix plan validated by GPT-5.4 via PAL codereview
type: audit
scope: global
preserve: true
---

# Audit 2026-04-12 — Consolidation Regression & Fix Plan

## Context

The Karpathy compounding pattern shipped earlier today (commit `12c818d`). An earlier GPT code review caught and fixed 3 HIGH issues in `memorize_with_merge`. The first real-corpus dry-run (`reports/consolidation_dryrun_2026-04-12.md`) exposed that **the fixes never propagated to `_cluster_memories`**, and surfaced additional issues around cross-scope safety, session_end insertion patterns, and candidate selection.

This report consolidates:
- My code-path walk of `~/.claude/scripts/rolling_memory.py` and `memory_session_end.py`
- External validation via PAL MCP → GPT-5.4 codereview (continuation `6a766bc1-2d55-47c4-953e-d1268982ffb7`)
- A prioritized fix plan (R1–R9) with gates

No code edits were made during this audit — the system remains in its current state.

## Verified Facts Brief

| # | Fact | Evidence |
|---|---|---|
| 1 | `_cluster_memories` uses the permissive `_token_overlap = inter/min` metric | `rolling_memory.py:539` calls function defined at `:273` |
| 2 | Merge path uses the strict 3-way gate correctly | `:427-431` — `subset >= 0.6 AND jaccard >= 0.4 AND shared >= 3` |
| 3 | `_STOPWORDS` excludes only English filler + `{session, command, failed, error, exit, stderr}` | `:103-111` |
| 4 | Tokenizer regex `[a-zA-Z0-9_./:-]{3,}` splits paths on `/` producing separate path-component tokens | `:269` |
| 5 | `consolidate` default types exclude `session_summary` but explicit `--type session_summary` is accepted | `:583-586` |
| 6 | `consolidate(scope="all")` passes `None` to `recall()`, enabling cross-scope clustering | `:596` + `:652` uses `cluster[0].scope` on write |
| 7 | `memorize_with_merge` only inspects `similar[0]` (top-subset candidate) before deciding MERGE / LINK / NEW | `:423` |
| 8 | `memory_session_end.py` calls plain `memorize()` for session_summary, not `memorize_with_merge` | `:188` |
| 9 | `error_lesson` correctly goes through `memorize_with_merge` | `memory_session_end.py:175` |
| 10 | Cluster threshold `0.3` is hardcoded in two places | `rolling_memory.py:511` and `:603` |
| 11 | `consolidate(dry_run=True)` still updates `last_accessed_at` via `recall()` | Empirically confirmed — DB sha256 changes, row count unchanged |
| 12 | MARKETING scope has 10 session_summary rows for the same `session_id=fe5fa115` | Dry-run #4, sqlite3 spot-check |

## Issues by Severity

### HIGH

**H1 — Cluster regression (`rolling_memory.py:539`).** The audit's strict 3-way gate was applied to the merge path but not the cluster path. `_cluster_memories` uses `_token_overlap` — the exact permissive metric audit #1 replaced. BFS transitivity amplifies weak pairwise edges, so even a slightly loose threshold produces chained false-positive clusters.
*Evidence:* AINEWS dry-run — 6 rows from 3 distinct `session_id`s false-clustered on `users`, `dmitrijnazarov`, `projects`, `ainews-sre-agent`, `cat`, `eof`, `insights`, `decision` template tokens.

**H2 — Stopword blind spot (`rolling_memory.py:103-111`).** Template tokens (`commit(s)`, `insights`, `decision`, `cat`, `eof`, `docs`, `feat`, `fix`, `handover`, `co-authored-by`) pass straight through. On any template-heavy type, real-content keywords are outweighed by boilerplate overlap.

**H3 — `scope="all"` reintroduces cross-scope contamination (`rolling_memory.py:596`, `:652`).** ⚠️ **GPT-5.4 found this, not me.** `consolidate(scope="all")` passes `scope=None` to `recall()` — all scopes land in one `memories` list — and `_cluster_memories` clusters across scope boundaries. On write (`:666`), the synthesized row is assigned to `cluster[0]["scope"]` — an arbitrary project scope inherits the merged result of memories from multiple projects. This is the same cross-scope contamination class that the audit already fixed in `_find_similar`. The fix was reintroduced via the CLI surface.

### MEDIUM

**M1 — `memorize_with_merge` only considers the top candidate (`rolling_memory.py:423`).** ⚠️ **GPT-5.4 found this.** `similar[0]` is sorted by `subset` score only; a short generic memory with high subset but low jaccard/shared can shadow a later candidate that would satisfy the full 3-way gate. The function falls to LINK or NEW when it should have MERGEd. Fix: iterate `similar` and pick the first candidate passing all three gates.

**M2 — BFS transitivity amplifies weak edges (`rolling_memory.py:513-516`).** Even after the H1 fix, transitive BFS can chain unrelated rows through a boilerplate-heavy "bridge" memory. Mitigation: after BFS builds a cluster, re-verify each non-seed member against the seed using the cluster gate; reject bridges.

**M3 — `session_summary` consolidation is permitted via explicit `--type` (`rolling_memory.py:583-586`).** Default excludes it, but the CLI accepts `--type session_summary` and proceeds. Should require `--force`.

**M4 — `memory_session_end.py:188` inserts a fresh `session_summary` per hook fire.** The session_end hook fires multiple times per session (PostToolUse batching). Each fire creates a new row with the same `session_id` but accreting content. This is snapshot spam, not intentional memory. Root cause of the MARKETING 10-row cluster.

**M5 — Cluster threshold `0.3` duplicated across `:511` and `:603`.** No single source of truth. Promote to module constants next to `MERGE_*_THRESHOLD`.

### LOW

**L1 — Dry-run touches `last_accessed_at` (`rolling_memory.py:recall`).** Dry-run is logically read-only but physically updates access metadata. Add `touch_access: bool = True` flag to `recall()`, pass `False` from `consolidate(dry_run=True)`.

**L2 — `consolidate()` has no LLM budget cap.** 50 clusters = 50 Haiku calls uncapped. Add `max_clusters_per_run` env knob.

**L3 — Tokenizer preserves `.` and `:` in tokens.** Not primary bug, but inflates overlap on `2026-04-12`, `v1.2.3`, `localhost:8080`, `docs/foo.md`. Lower priority than H1/H2.

## Fix Plan (R1–R9)

Ordered by priority. Each row: scope, file:line, change, verification.

| # | Severity | File:Line | Change | How to verify |
|---|---|---|---|---|
| **R1** | HIGH | `rolling_memory.py:539` | Replace `_token_overlap` with `_similarity_metrics` + 3-way gate. Add `CLUSTER_SUBSET_THRESHOLD=0.5`, `CLUSTER_JACCARD_THRESHOLD=0.3`, `CLUSTER_MIN_SHARED=5` next to `MERGE_*` at `:97-100`. Remove `threshold` param from `_cluster_memories` signature (no longer scalar). | Re-run AINEWS dry-run from `consolidation_dryrun_2026-04-12.md` — expect `clusters_found: 0` (the 6 AINEWS rows are genuinely distinct). |
| **R2** | HIGH | `rolling_memory.py:103-111` | Extend `_STOPWORDS` with generic template tokens: `commit commits insights decision cat eof docs feat fix handover co-authored-by`. **Do NOT** add personal/project slugs (`dmitrijnazarov`, `ainews-sre-agent`) — that would make extraction repo-local and brittle. GPT-5.4 pushback accepted. | Re-run AINEWS dry-run; combined with R1, expect zero false clusters. Unit test: `_extract_keywords("fix(foo): bar")` must not contain `fix`. |
| **R3** | HIGH | `rolling_memory.py:consolidate` | Refuse `scope="all"` with `ValueError("consolidate(scope='all') is unsafe; run per-scope")`. Document that the caller must iterate scopes explicitly. | Unit test: `consolidate(scope="all")` raises ValueError. Run dry-run on two project scopes separately — no cross-scope cluster possible. |
| **R4** | MED | `rolling_memory.py:422-490` | Iterate `similar` instead of `similar[0]`. For each candidate: if full merge gate passes → MERGE; else track first LINK candidate; if loop exhausts without merging → LINK (if any) or NEW. | Unit test: `_find_similar` returns `[(generic_short, 0.8, 0.2, 2), (specific, 0.62, 0.41, 4)]` — expect MERGE into `specific`, not LINK to `generic_short`. |
| **R5** | MED | `rolling_memory.py:_cluster_memories` | After BFS, for each cluster verify every non-seed member satisfies the cluster gate against the seed (not just the transitive parent). Reject members that fail. | Unit test: construct A~B~C where A~C fails — expect cluster `[A,B]`, not `[A,B,C]`. |
| **R6** | MED | `rolling_memory.py:consolidate` CLI | Refuse `--type session_summary` unless `--force` flag passed. Raise with clear message referencing template dominance. | CLI integration test: `consolidate --type session_summary` exits non-zero without `--force`. |
| **R7** | MED | `memory_session_end.py:188` + `rolling_memory.py:memorize` | Option A (preferred): add `idempotency_key` param to `memorize` that upserts on `(memory_type, session_id, scope)` — re-firing session_end replaces the existing row. Option B: wire session_summary through `memorize_with_merge` with a lifted guard at `:407`. **Recommend A** — cleaner semantic, no coupling to merge thresholds. | Integration test: fire session_end 3 times with same `session_id` + different content. Expect 1 row at the end, not 3. |
| **R8** | MED | `rolling_memory.py:97-100`, `:511`, `:603` | Centralize cluster threshold as module constant. Delete duplicated `0.3` at both call sites. | grep for the literal `0.3` in `rolling_memory.py` — expect 0 hits in non-test code. |
| **R9** | LOW | `rolling_memory.py:recall` + `:consolidate` | Add `touch_access: bool = True` to `recall()`. Pass `False` from `consolidate(dry_run=True)`. Byte-identical DB sha256 before/after dry-run. | `shasum -a 256` before/after dry-run — must match. |

### Deferred to a separate task

**D1 — LLM budget cap (L2).** Add `ROLLING_MEMORY_MAX_CLUSTERS_PER_RUN` env knob read in `consolidate()`. Small PR, no blockers, but not strictly required for correctness.

**D2 — Tokenizer regex tightening (L3).** Only if false positives persist after R1+R2. GPT-5.4 explicitly warned against removing `/` from the token class (would make path explosion worse, not better). If needed, post-filter timestamp/version/URL-shaped tokens instead.

## Implementation Order & Gates

```
R1 + R2 + R5  →  re-run dry-run  →  must show 0 false clusters on AINEWS corpus
         ↓ (gate: dry-run clean)
R3 + R8  →  re-run dry-run with explicit per-scope iteration  →  must remain clean
         ↓ (gate: dry-run clean)
R4  →  unit test with synthetic candidates  →  merge picks correct row
         ↓ (gate: unit tests pass)
R6  →  CLI integration test  →  force flag works, bare command refuses
         ↓ (gate: CLI behavior correct)
R7  →  session_end integration test (fire hook 3x, expect 1 row)
         ↓ (gate: 1 row, not 3)
R9  →  sha256 verification after dry-run  →  byte-identical
         ↓ (gate: hash matches)
REAL CONSOLIDATION on error_lesson (--scope global)  →  gated on all above
```

**Do NOT run any real (non-dry-run) consolidation until R1, R2, R3, R5 are applied and re-validated.** Running real consolidation now would destroy the AINEWS session_summaries (false cluster → Haiku synthesis → active rows deactivated → data loss).

**Do NOT start Phase 2a (FTS5 scope + report indexing) until this audit's R1–R7 are done.** Migrating schema under a broken consolidator compounds the problem.

## Verification Plan (end-to-end)

After all R1–R9 land:

1. **Snapshot DB:** `cp ~/.claude/rolling_memory.db ~/.claude/rolling_memory.db.prefix` — baseline for rollback.
2. **Unit tests** on new helpers (`_cluster_memories`, extended `_STOPWORDS`, `memorize_with_merge` iteration, `recall(touch_access=False)`).
3. **Repeat dry-runs** from `consolidation_dryrun_2026-04-12.md` sections 1–5. Acceptance: all three HIGH issues produce `clusters_found: 0` on current corpus.
4. **sha256 check:** dry-run is byte-identical (R9 gate).
5. **Fire a real session_end hook 3× with same session_id:** exactly 1 `session_summary` row at end.
6. **Synthetic merge test:** insert two real-ish error_lessons sharing `>=5` tokens and high jaccard → verify MERGE path triggers; insert a generic short lesson with high subset but low shared → verify NEW path triggers.
7. **Run actual consolidation** on `--scope global --type error_lesson` (will run with 3 rows — still zero clusters expected, but confirms no crash).
8. **Commit snapshot** as `rolling_memory.db.after_fixes_<timestamp>` for post-fix baseline.

## Rollback Plan

- DB snapshots: `rolling_memory.db.startup_20260412_183934` (pre-audit) and whatever prefix snapshot R-verification creates.
- Script snapshot: `~/claude_backup_20260412_compounding.tar.gz` from yesterday's handover.
- If a fix causes hook failures: restore `~/.claude/scripts/rolling_memory.py` from the tarball, DB untouched.
- If data loss: restore DB from `.startup_*` snapshot, re-apply fixes, re-validate dry-run before any real consolidation.

## Process Lessons

1. **A code-review that fixes a metric in one place must check every call site of that metric.** The original audit fixed `_similarity_metrics` in the merge path but didn't grep for other `_token_overlap` callers. `_cluster_memories` was the half-left-behind.
2. **Scope boundaries must be enforced at every surface, not just `_find_similar`.** The CLI `--scope all` flag was a back door that reintroduced cross-scope contamination. Fixes to data-safety invariants should be function-level invariants, not call-site conventions.
3. **"First real-corpus dry-run" is a mandatory gate for any compounding feature.** The audit previously verified tests with hand-crafted happy/sad paths, but the template dominance issue only showed up when running against real session_summary content. Add: before declaring compounding stable, run dry-run on real corpus.
4. **Per-candidate iteration beats top-1 ranking for multi-signal gates.** When the ranking metric (`subset`) is one of three gate conditions, the top-ranked candidate is not necessarily the one that passes all three.

## External Expert Credit

Three findings in this report (H3, M1, M2) were contributed by GPT-5.4 via PAL `codereview`. I independently found H1, H2, M3, M4, M5, L1, L2, L3. GPT-5.4 also correctly pushed back on my proposed tokenizer-regex change (option 2c in the dry-run report) — removing `/` would have made path explosion worse, not better. Validated pushback is reflected in the R2 description.

## Open Questions (for next session)

1. **R7 — upsert semantics:** if a session_end hook fires mid-session with partial content, then again at true end with more content, should the final row contain both (accretion) or just the latest (replace)? Current snapshot spam is the worst of both — multiple rows with accreting content. Accretion via upsert requires a content-merge strategy; replace loses mid-session insights.
2. **R4 — iteration order:** should candidates be iterated by `subset`, by `jaccard`, or by `shared_count`? Currently sorted by `subset`. Iterating by `shared_count DESC` favors merges with more evidence, which matches the audit philosophy.
3. **R1 threshold numbers:** `0.5/0.3/5` is a starting point. After R1 lands we should run dry-run on a larger corpus (error_lesson after 2–3 weeks of accumulation) and tune up or down based on false-positive / false-negative rate.
