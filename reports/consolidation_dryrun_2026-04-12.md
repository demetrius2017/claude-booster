---
name: Consolidation Dry-Run 2026-04-12
description: First real-corpus dry-run of the Karpathy compounding pattern; found audit-regression in _cluster_memories and session_summary template dominance
type: audit
scope: global
preserve: true
---

# Consolidation Dry-Run — 2026-04-12

## Context

Hours after the Karpathy compounding pattern landed (commit `12c818d`), this dry-run validates the clustering stage on the real rolling memory corpus before any real (LLM-calling) consolidation runs.

Dry-run is the `--dry-run` flag on `rolling_memory.py consolidate` — it runs `recall()` → `_cluster_memories()` but stops before Haiku synthesis and before any write to active rows.

## TL;DR

1. **Audit regression.** The strict 3-way merge gate (`subset ≥ 0.6 AND jaccard ≥ 0.4 AND shared ≥ 3`) introduced to fix audit finding #1 lives inside `memorize_with_merge` only. `_cluster_memories` still uses the old permissive `_token_overlap = intersection / min(|a|,|b|)` — the exact metric the audit flagged. Real consolidation on template-heavy types would produce false-positive clusters.
2. **Corpus is tiny and template-dominated.** 38 active rows total (not 1558 — that was the autoincrement max). Only `session_summary` has volume, and its boilerplate (`$(cat <<'EOF'`, `Insights:`, `Decision:`, path components) dominates keyword extraction — 6 AINEWS sessions from 3 different session_ids got false-clustered on shared template tokens.
3. **No real consolidation should run** until `_cluster_memories` uses the strict gate and stopwords are extended.
4. **Dry-run is not byte-identical to the DB.** `recall()` updates `last_accessed_at` on touched rows. Content-identical, but the file hash changes. Minor — worth documenting.

## Environment Snapshot

| Item | Value |
|---|---|
| DB path | `~/.claude/rolling_memory.db` |
| DB size | 106 KB |
| Active rows | 38 |
| All rows | 43 |
| Autoincrement max | 1558 (many trimmed) |
| Pre-run sha256 | `a513867267c5df4424e461ed6632ccbfc94d25bfecc52e703d3a8f67242cd7d3` |
| Post-run sha256 | `cb5ba4797f5decff00bf6fcea56aa3f4f4c0a90c83889ece6be2cf5c30a65853` |
| Backup snapshot | `~/.claude/rolling_memory.db.startup_20260412_183934` |

### Active corpus by type

| memory_type | count |
|---|---|
| session_summary | 30 |
| directive | 4 |
| error_lesson | 3 |
| decision | 1 |
| **feedback** | **0** |

### Active corpus by scope (top)

| scope | count |
|---|---|
| `MARKETING/marketing_agent` | 10 |
| `global` | 8 |
| `AINEWS/ainews-sre-agent` | 6 |
| `AINEWS/monitoring` | 3 |
| other projects | 1–2 each |

## Runs

### 1. `consolidate --dry-run --scope global --type error_lesson`

```json
{"clusters_found": 0, "consolidated": 0, "conflicts": [], "errors": 0}
```

**Corpus checked:** ids 8, 9, 10.
- #8: IBKR Gateway CORS must be `*`
- #9: Alpine Linux localhost resolves to IPv6 ::1
- #10: Docker `--cap-drop=ALL` breaks healthchecks

**Verdict: correct.** Three genuinely distinct infra lessons. Zero clusters is the right answer.

### 2. `consolidate --dry-run --scope global --type feedback`

```json
{"clusters_found": 0, "consolidated": 0, "conflicts": [], "errors": 0}
```

**Corpus checked:** 0 rows. Not informative — no feedback entries have been promoted to `global` scope.

### 3. `consolidate --dry-run --scope global --type session_summary`

```json
{"clusters_found": 0, "consolidated": 0, "conflicts": [], "errors": 0}
```

**Corpus checked:** 0 rows. All session_summaries live at project scope, not global. The `--scope global` filter makes this run uninformative for stress-testing.

### 4. `consolidate --dry-run --scope MARKETING/marketing_agent --type session_summary`

```json
{"clusters_found": 1, "consolidated": 0, "conflicts": [], "errors": 0}
```

One cluster containing **all 10** MARKETING session_summaries.

Spot-check:
- #1518: "Session fe5fa115; ... no significant events captured"
- #1532: "Session fe5fa115; ... 1 commit(s). fix: classify RSY partner sites ..."
- #1533: "Session fe5fa115; ... 1 commit(s). docs: handover 2026-04-12 — Ad Buyer audit ..."

**Verdict: mixed.** All 10 entries belong to the same `session_id` (`fe5fa115`). They are incremental snapshots of the same session's state, so clustering them is arguably correct. But that also means the session_end hook is creating a fresh row per fire instead of merging on session_id — worth investigating separately. For this report: the cluster *is* genuine duplication at the session level, but the root cause is upstream (session_end not merging on session_id), not clustering.

### 5. `consolidate --dry-run --scope AINEWS/ainews-sre-agent --type session_summary`

```json
{"clusters_found": 1, "consolidated": 0, "conflicts": [], "errors": 0}
```

One cluster containing **6** entries from **3 distinct session_ids** (f2107f60, 1f72e50f, fb68e637).

Spot-check:
- #1515: "Session 1f72e50f; ... docs: handover 2026-04-10 session 3 — audit fixes + topology verification ..."
- #1519: "Session fb68e637; ... feat: media delivery monitoring system — Prometheus, Grafana, RUM ..."
- #1556: "Session f2107f60; ... fix(postgres_health): replace CheckStatus.DEGRADED → FAIL ..."

**Verdict: false positive.** These are three unrelated work streams (topology audit, media monitoring rollout, postgres health fix) from three different sessions on three different days. The only common ground is the project path and the `Session …; in …` / `Insights:` / `Decision:` / `$(cat <<'EOF'` boilerplate. Real consolidation would synthesize these into one entry and destroy the per-session detail.

## Root Cause

Two independent issues stack:

### A. `_cluster_memories` uses the permissive metric flagged by the audit

`rolling_memory.py:539`:
```python
if _token_overlap(kw_cache[i], kw_cache[j]) >= threshold:
```

`_token_overlap` (`rolling_memory.py:273`) is `intersection / min(|a|, |b|)` — the exact formula audit finding #1 (HIGH) flagged as too permissive in `memorize_with_merge`. The audit fix added `_similarity_metrics` with a 3-way gate and wired it into the merge path, but **not** the clustering path. Cluster threshold is `0.3` (loose) vs merge thresholds `0.6/0.4/3` (strict).

### B. Stopwords miss the session_summary template

`_STOPWORDS` at `rolling_memory.py:103-110` filters common English words plus `session command failed error exit stderr`. It does **not** filter the template tokens that dominate every session_summary:

- `commit`, `commits`
- `insights`, `decision`, `cat`, `eof`
- `docs`, `feat`, `fix`, `handover`
- `users`, `dmitrijnazarov`, `projects` (path components repeated in every row)
- project subdirectory slugs (`ainews-sre-agent`, `marketing_agent`)

After stopword filtering, the effective keyword set for a session_summary is ~60% template and ~40% real content. Overlap between any two summaries in the same project scope hits 30% trivially from path + template alone.

## Recommendations

Ordered by priority.

### R1 (required before any real consolidation) — Propagate the 3-way gate to clustering

Replace the `_token_overlap` call in `_cluster_memories` with `_similarity_metrics` and apply the same `shared >= 3` floor that the merge path uses. A reasonable cluster gate: `subset >= 0.5 AND jaccard >= 0.3 AND shared >= 5` (slightly looser than merge, since clustering is a precondition for LLM review, not a final action).

**File:** `~/.claude/scripts/rolling_memory.py` — `_cluster_memories()` lines 510–544.

### R2 (required before any real consolidation) — Extend stopwords

Add template + path tokens to `_STOPWORDS`:
```
commit commits insights decision cat eof docs feat fix handover
users dmitrijnazarov projects
```

Alternative: strip leading path prefix from content before keyword extraction when `memory_type == "session_summary"`. More surgical but adds a type-aware branch.

**File:** `~/.claude/scripts/rolling_memory.py` — `_STOPWORDS` at lines 103–110.

### R3 (recommended) — Exclude session_summary from default consolidation

`consolidate()` at line 583 already excludes session_summary from its default type list (`error_lesson`, `feedback`, `decision`, `directive` only). But it still runs if explicitly requested via `--type session_summary`. Add a guard that refuses session_summary unless `--force` is passed, with a log message explaining template dominance.

### R4 (investigation) — session_end merge-on-session_id

The MARKETING cluster of 10 entries from the same session_id suggests the session_end hook is inserting fresh rows instead of merging on session_id. This predates the compounding work and is a separate bug. Out of scope for this report, but worth a follow-up task.

### R5 (minor) — Document that dry-run updates `last_accessed_at`

Either:
- Clarify in the CLI help text that `--dry-run` still touches access metadata, or
- Wrap the `recall()` inside consolidate's dry-run path in a read-only transaction that suppresses access updates.

The latter is cleaner but touches more code.

## What Should NOT Happen Next

- No real (non-dry-run) consolidation before R1 and R2 are applied and re-validated on this same corpus
- No schema migration (Phase 2a) before the clustering logic is fixed — otherwise we'd be migrating a table under a consolidator that produces garbage
- No launchd scheduling before the above — automating a broken consolidator is worse than not automating it

## Verification That Was Performed

- DB snapshot created before run: `rolling_memory.db.startup_20260412_183934`
- Row counts pre/post: 43 total / 38 active, unchanged
- `last_accessed_at` set on 18 → 30 rows (incidental, not content change)
- `~/.claude/logs/memory_hooks.log` tail: no new error lines after the dry-runs
- No edits to `rolling_memory.py` were made during this session (read-only audit)

## Open Questions for Next Session

1. Should R1 use a separate set of thresholds tuned for clustering, or reuse the merge thresholds as-is?
2. Is the session_end multi-insert (R4) a bug or intentional snapshot behavior?
3. Should session_summary eventually be consolidated at all, or is it ephemeral by design (meant to be trimmed, not synthesized)?
