---
name: Audit 2026-04-12 round 2 — Phase 2a post-fix review
description: Second-pass GPT-5.4 audit of the Phase 2a changes after round-1 fixes landed; caught 1 HIGH (consolidate guard for consilium/audit), 2 MED (dry-run init_db violation, docstring vs contract drift), 1 LOW (fixed-depth globs); all addressable findings fixed in-session
type: audit
scope: global
preserve: true
---

# Audit 2026-04-12 — Phase 2a Round 2

## Context

After round 1 fixed 6 issues (3 HIGH + 2 MED + 1 LOW) surfaced by GPT-5.4's first pass on the Phase 2a implementation, the user requested a re-audit on the post-fix state. The goal: catch bugs introduced by the fixes themselves and anything the first pass missed.

Round 2 was run via `mcp__pal__codereview` with `model=gpt-5.4`, `thinking_mode=high`, same continuation ID as round 1.

## Sign-off from GPT-5.4 on round-1 fixes

GPT-5.4 validated all six round-1 fixes as correct:

1. **v1→v2 ALTER committed separately from v2→v3 atomic block** — safe. A crash between them leaves a consistent v2 schema; next `init_db()` re-enters cleanly because the column check (`PRAGMA table_info`) is authoritative, not `user_version`.
2. **`search()` parameter ordering** — correct for all 4 branches (`scope=None`, `scope='global'`, `scope='/proj/x'+include_global=True`, `scope='/proj/x'+include_global=False`). Trace verified externally.
3. **`index_reports.py` × `memory_session_end.py` writer contention** — no correctness issue. SQLite serializes writers via `BEGIN IMMEDIATE`; transactions are short (ms-scale) and bounded at ~20 calls per indexing run.
4. **Atomic v2→v3 migration** — executescript with explicit BEGIN/COMMIT is the correct idiom. No concurrent reader can observe an empty FTS during migration.
5. **FTS self-heal via `fts_exists_before`** — pragmatic recovery path for backup restore scenarios.
6. **`content_hash × idempotency_key` interaction** — no data-loss bug. `memorize()` upsert wraps DELETE+INSERT in one transaction; if INSERT hits IntegrityError the rollback reverts both and the target row stays in place. The only caveat is policy: two different reports with identical content would collapse under the global dedupe key (astronomical).

## New findings

### R2-HIGH-1 — `consolidate()` still allows clustering of consilium/audit rows

**Location.** `rolling_memory.py:724-727` (pre-fix line numbers).

**Issue.** The round-1 review noted that `preserve: true` wasn't wired from report frontmatter into `agent_memory` rows and deferred the fix to Phase 2c. Round 2 widened the inspection: `consolidate()`'s default types are `['error_lesson', 'feedback', 'decision', 'directive']`, so consilium/audit rows are NOT touched on a default run. BUT — a user running `consolidate --type audit --scope global` with a valid `ANTHROPIC_API_KEY` would still fuse the 10 indexed audit rows into a synthesized single row. Silent data loss; the exact failure mode the Karpathy-compounding refactor was supposed to prevent.

**Fix.** Land the simple guard now instead of waiting for Phase 2c metadata wiring. Mirrors the `session_summary` + `--force` pattern from session 3. Added at the top of `consolidate()`, right after the `scope == "all"` check:

```python
if memory_type in ("consilium", "audit"):
    raise ValueError(
        f"consolidate(memory_type='{memory_type}') is disabled until "
        "`preserve` is stored on agent_memory rows (Phase 2c)"
    )
```

**Verification.** `rolling_memory.py consolidate --type audit` → `ValueError: ... Phase 2c`. `--type consilium` → same. `--type directive --dry-run` → still works (no side-effect, no guard hit).

### R2-MED-1 — `--dry-run` violates its own "no DB write" contract

**Location.** `index_reports.py:209` (`rolling_memory.init_db()` at top of `index_all`).

**Issue.** The indexer's `--dry-run` flag is supposed to be a pure parse + report with zero DB writes. But `index_all()` unconditionally calls `rolling_memory.init_db()` before walking files, and `init_db()` can perform a schema migration (v1→v2 ALTER or v2→v3 FTS rebuild) depending on the current DB state. A user running `--dry-run` on a pre-migration DB would silently migrate it. Contract violation.

**Fix.** Gate `init_db()` on `not dry_run`. Dry-run now touches zero DB state.

```python
if not dry_run:
    rolling_memory.init_db()
```

**Verification.** sha256 pre/post of `rolling_memory.db` around `index_reports.py --dry-run` now match byte-for-byte (`9ec363ba...`).

### R2-MED-2 — Docstring contradicts actual fallback behaviour

**Location.** `index_reports.py` module docstring, `Limitations` section.

**Issue.** The docstring said "Reports without YAML frontmatter are skipped with a warning." The code actually ingests them using filename-based type inference (`_infer_type_from_name` returns `'consilium'` for `consilium_*.md` even without frontmatter). GPT-5.4: "If this fallback behaviour is actually intentional, then update the file header/docstring instead; right now code and contract disagree."

**Decision.** The fallback is intentional — it's how we index legacy reports that predate the frontmatter convention. Documentation fix, not code fix.

**Fix.** Rewrote the `Limitations` section to describe the actual behaviour: filename-prefix fallback enables frontmatter-less files; only files with neither valid frontmatter nor a matching filename are skipped. Also documented the fixed-depth glob limitation (R2-LOW-1) in the same section.

### R2-LOW-1 — Fixed-depth report discovery

**Location.** `index_reports.py:65-77` (`_iter_report_files`).

**Issue.** `_iter_report_files` uses hardcoded patterns `*/reports/*`, `*/*/reports/*`, and `*/audits/*/audit_report.md`. A project nested 3+ levels under `~/Projects` (e.g., `~/Projects/Foo/Bar/Baz/reports/audit_x.md`) is silently missed.

**Decision.** Not fixed in-session — no such project exists in the current filesystem (verified 20 files match). Documented as a known limitation in the module docstring so a future maintainer hitting this will know to switch to a constrained recursive scan rather than adding more depth levels.

## Process evidence

**Verification run after all three code fixes:**

```
=== R2-HIGH-1: consolidate --type audit must be rejected ===
ValueError: consolidate(memory_type='audit') is disabled until `preserve` is stored on agent_memory rows (Phase 2c)
PASS

=== R2-HIGH-1: consolidate --type consilium must be rejected ===
PASS

=== R2-HIGH-1: consolidate --type directive still works ===
{"clusters_found": 0, "consolidated": 0, "conflicts": [], "errors": 0}

=== R2-MED-1: index_reports --dry-run must not touch DB ===
DRY-RUN summary: indexed=20 skipped=0 errors=0
pre=9ec363ba...  post=9ec363ba...
PASS: dry-run is byte-identical

=== Regression: session 3 invariants still hold ===
PASS: consolidate dry-run still byte-identical

=== Regression: real index run still works on existing DB ===
INDEX summary: indexed=20 skipped=0 errors=0
20 rows (10 consilium + 10 audit)
```

## Files changed in round 2

| File | Lines | Change |
|---|---|---|
| `~/.claude/scripts/rolling_memory.py` | +11 | consolidate() guard for consilium/audit |
| `~/.claude/scripts/index_reports.py` | +6 -1 | `not dry_run` gate on init_db + rewritten Limitations docstring |
| `~/Projects/Claude_Booster/reports/audit_2026-04-12_phase_2a_round2.md` | new | this audit report |

## Decisions

- **Land the consolidate guard today, don't wait for Phase 2c.** Phase 2c's full solution (a `preserve` column on `agent_memory` + wiring through `index_reports.py`) is still on the roadmap, but the narrow guard closes the data-loss path immediately with ~10 LOC. Zero reason to leave it open.
- **Keep the filename-prefix fallback in `index_reports.py`.** Useful for legacy reports; documented explicitly so it's not mistaken for a bug.
- **Don't fix `_iter_report_files` fixed-depth globs.** No current project triggers it. A recursive scan is the right fix eventually but wastes tokens today.

## Rejected alternatives

- **Add a `preserve` column to `agent_memory` right now as part of round 2.** Rejected because it's a real schema change (v3→v4) that touches `init_db`, `memorize`, and every caller that constructs rows. Scope creep for a round 2 cleanup pass; belongs in Phase 2c with its own backup + audit cycle.
- **Strip the filename-prefix fallback and force frontmatter on every report.** Rejected because some older reports in `AINEWS/reports/` and `horizon/reports/` predate the frontmatter convention and their indexing is a strict improvement over not indexing them at all.
- **Switch `_iter_report_files` to `rglob`.** Rejected for round 2 — would need to re-add filename prefix filtering anyway, and the current patterns cover 100% of the real corpus. Deferred.

## Institutional lessons

1. **Round 2 audit IS mandatory, not optional.** Round 1 declared Phase 2a "done pending external review". Round 2 found a HIGH (consolidate guard) + 2 MED that round 1's author + GPT's first pass missed. The pattern from session 3 holds: one round-trip is not enough; a post-fix re-audit catches issues introduced by the fixes themselves and anything narrowed by the new knowledge.
2. **Simple guards beat elaborate metadata plans.** The "preserve column + metadata wiring" plan for Phase 2c is correct but non-trivial. Dropping a 4-line `raise ValueError` in `consolidate()` closes the same data-loss path today with zero schema change. Ship the guard, plan the full solution.
3. **Dry-run contracts must be actually dry.** A `--dry-run` that silently triggers a schema migration is worse than a `--dry-run` that doesn't exist, because the user trusts the flag. Always pipeline the skip-side-effects logic through whatever nested helpers the dry-run path touches.
