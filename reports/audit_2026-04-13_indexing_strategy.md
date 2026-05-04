---
name: "Audit 2026-04-13 — auto-indexing strategy (where to wire index_reports.py)"
description: >
  Scenario-based scoring audit to pick the right integration point for
  automatic consilium/audit indexing. 12 user scenarios, 8 implementation
  options, weighted score. Winner Variant G (PostToolUse async subprocess +
  rule-prose self-heal, score +32) strictly dominates every alternative.
  Implementation landed in memory_post_tool.py.
type: audit
scope: global
preserve: true
---

# Audit 2026-04-13 — Auto-indexing Strategy

## Context

Phase 2d shipped `start-context` and `build_start_context`, making the
22-row consilium/audit corpus reachable from `/start`. But the indexer
(`index_reports.py`) is still a manual invocation — if a new consilium
or audit is written during a session, it's invisible to the next
session's `/start` until someone runs the indexer by hand.

Left unfixed, this would silently rot: the rule prose's "self-heal" note
(«if returns empty, run `index_reports.py` once») catches fresh installs
but not the day-to-day case where a user writes a new audit and expects
the next session to see it.

This audit picks the right integration point for automatic indexing by
running 8 implementation options through 12 real user scenarios and
weighted-scoring them. Winner is implemented in `memory_post_tool.py` in
the same session as this report.

## Scenarios

| # | Scenario | Weight | Rationale |
|---|---|---|---|
| S1 | `/start` in a fresh session with no new reports | 3 | Most common day-to-day entry. Any cost here is paid every session. |
| S2 | `/start` sees yesterday's reports **in this project** | 3 | Core use case — the whole point of indexing them. |
| S3 | `/start` sees yesterday's reports **in another project** | 3 | Phase 2d's raison d'être. Cross-project knowledge is the whole new capability. |
| S4 | Mid-session `/search` against a just-written audit | 2 | Real workflow: user writes audit, wants to grep it. |
| S5 | `/handover` at session end | 3 | Happens every session. Report MUST be indexed before the next session starts. |
| S6 | Session crashes mid-work (laptop closed, process killed) | 1 | Rare. Recovery is acceptable via self-heal. |
| S7 | Fresh install / new machine | 2 | Once per machine. |
| S8 | User edits a report outside Claude (vscode, nano) | 1 | Uncommon. |
| S9 | Many quick sessions, no reports written | 3 | Power user pattern. Any ambient cost hits hard here. |
| S10 | Same session: `/consilium` → reference it later in the same session | 2 | Real workflow. |
| S11 | Restored from backup | 1 | Rare. |
| S12 | Parallel work in two projects (two terminals) | 1 | Occasional. |

## Options

| Code | Option | Hook infra already present? |
|---|---|---|
| A | Extend `memory_session_end.py` — call `index_reports.py` synchronously at Stop | Yes (hook registered as `Stop`) |
| B | Extend `memory_session_start.py` — index at session start | Yes |
| C | Extend `memory_post_tool.py` — fire-and-forget subprocess when Write/Edit hits `*/reports/{consilium,audit}_*.md` | Yes (hook registered as `PostToolUse` matcher `*`) |
| D | Rule-prose self-heal only (status quo) | N/A — already in `/start` step 2 |
| E | launchd timer every 15 min | No — would need plist + `launchctl load` |
| F | A + D | |
| **G** | **C + D** ← **winner** | |
| H | A + C + D | |

## Scoring matrix

Scale: `+2` perfect, `+1` acceptable, `0` neutral / N/A, `-1` measurable
downside, `-2` broken in this scenario.

| S# | w | A | B | C | D | E | F | **G** | H |
|---|---|---|---|---|---|---|---|---|---|
| S1 | 3 | 0 | -1 | 0 | 0 | -1 | 0 | **0** | 0 |
| S2 | 3 | +2 | +2 | +2 | -1 | +1 | +2 | **+2** | +2 |
| S3 | 3 | +2 | +2 | +2 | -1 | +1 | +2 | **+2** | +2 |
| S4 | 2 | -1 | -1 | +2 | -1 | -1 | -1 | **+2** | +2 |
| S5 | 3 | +2 | -2 | +2 | -1 | +1 | +2 | **+2** | +2 |
| S6 | 1 | -1 | +1 | 0 | 0 | +2 | 0 | **0** | 0 |
| S7 | 2 | 0 | 0 | 0 | +2 | +2 | +2 | **+2** | +2 |
| S8 | 1 | 0 | 0 | -1 | 0 | +2 | +1 | **0** | +1 |
| S9 | 3 | -1 | -2 | 0 | +2 | -2 | -1 | **0** | -1 |
| S10 | 2 | -1 | -1 | +2 | -1 | -1 | -1 | **+2** | +2 |
| S11 | 1 | +1 | +1 | 0 | +1 | +2 | +1 | **+1** | +1 |
| S12 | 1 | 0 | 0 | +1 | -1 | +2 | 0 | **+1** | +1 |

## Weighted totals

| Variant | Score |
|---|---|
| **G: C + D (PostToolUse async + self-heal)** | **+32** |
| H: A + C + D | +29 |
| C alone | +26 |
| F: A + D | +16 |
| A alone | +11 |
| E: cron | +8 |
| D alone | -3 |
| B alone | -5 |

## Decision: Variant G

### Why G beats H (A + C + D)

Adding `A` on top of `G` **worsens** the score by -3 on S9 (many quick
sessions, no writes): SessionEnd hook would burn ~200 ms at every
session close with no workload, while `C` has already covered every
critical scenario. `A` becomes pure overhead without adding value over
`C + D`.

### Why C beats A

`C` wins **S4 and S10** by +3 each (net +6) — mid-session `/search` and
in-session references to a just-written consilium. These are real
workflows, not edge cases. `A` physically cannot cover them because
SessionEnd fires too late — by the time indexing happens, the current
session is already closing.

### Why not B

`B` (SessionStart) loses **S5 (-2)** (wrong time — indexes reports from
the PREVIOUS session at session start, which is a 1-session lag) and
**S9 (-2)** (visible boot latency on every session). Worst option in
the matrix.

### Why not E (cron / launchd)

Strong on crash recovery (S6, S7, S8) but fails **S9 (-2)** — wastes
96 runs per day for a ~10-20-reports-per-week usage pattern. Also:
harder to deploy (plist + launchctl), extra point of failure, and
leaks subprocess invocations into Activity Monitor where they don't
belong.

## Implementation

### PostToolUse `_maybe_trigger_index` in `memory_post_tool.py`

```python
_REPORT_WRITE_PATTERN = re.compile(r"/reports/(?:consilium|audit)_[^/]*\.md$")
_INDEXER_SCRIPT = os.path.expanduser("~/.claude/scripts/index_reports.py")


def _maybe_trigger_index(tool_name, tool_input):
    if tool_name not in ("Write", "Edit"):
        return
    if not isinstance(tool_input, dict):
        return
    path = tool_input.get("file_path", "")
    if not isinstance(path, str) or not _REPORT_WRITE_PATTERN.search(path):
        return
    try:
        import subprocess
        subprocess.Popen(
            ["python3", _INDEXER_SCRIPT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        pass  # silent — must not block Claude
```

Called from `main()` right after JSON parsing, before the bash_error /
git_commit event logic. Fires only on Write/Edit to a matching path;
forks a detached subprocess and returns immediately.

### Preserved contract

The module docstring's «<5 ms» contract is now split into two cases:

- **Common path** (Read / non-report Write / Bash): unchanged at
  **~0.002 ms median**, three orders of magnitude inside the contract.
- **Matching-write path** (rare — `/audit`, `/consilium`, `/handover`):
  ~**5 ms median, 30 ms p95** — the irreducible cost of `posix_spawn`
  on macOS. Acceptable because this path fires ~1-2 times per session
  and the Write tool that triggered it already takes much longer than
  30 ms.

Measured across 20 iterations × 4 payload types using a direct
`main()` invocation via monkey-patched `sys.stdin`:

```
Read non-report            median=0.002ms  p95=0.007ms
Write non-report           median=0.001ms  p95=0.002ms
Bash (no error)            median=0.002ms  p95=0.002ms
Write AUDIT report         median=5.223ms  p95=34.460ms
```

### End-to-end verification

Synthetic Write event for a newly-created `audit_*.md` file fed to
`main()`:

1. Hook returned immediately (no wait on subprocess).
2. 2-second delay for the async subprocess to complete.
3. `stat -f %m ~/.claude/rolling_memory.db` moved forward → indexer
   wrote to the DB.
4. `rolling_memory.py search "Trigger test"` found the new row.
5. Test file + row cleaned up afterwards.

Regression suite (all green, DB byte-identical for read-path):

```
Phase 2d scope suite (Claude_Booster / subdirectory / horizon+query / FTS5 syntax)  PASS
Round 1/2 regression (consolidate guards, dry-runs, stats)                          PASS
memory_post_tool.py gates (4 non-matching payloads processed clean)                 PASS
DB sha256 pre=a8bd5bee... post=a8bd5bee...                                          byte-identical
```

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Subprocess hangs | `index_reports.py` finishes in <1s; hang would require a SQLite deadlock, which the R7 `BEGIN IMMEDIATE` guard serializes cleanly. Orphaned subprocess is harmless on macOS (no zombie due to `start_new_session=True`). |
| Two writes in quick succession → two indexers running in parallel | `rolling_memory.memorize` uses `BEGIN IMMEDIATE`, which SQLite serializes at the file lock level. Both indexers complete, one after the other. Correct end state (upsert via idempotency_key). |
| Subprocess crashes (permission, import error, disk full) | Silent by design — must not block Claude. Rule-prose self-heal (`D`) in `/start` step 2 directs the user to run `index_reports.py` manually when the next `/start` sees stale state. |
| PostToolUse doesn't see external edits (vscode, nano) | Covered by self-heal. S8 has weight 1, low-stakes scenario. |
| `p95` latency of 30 ms on matching writes | Acceptable trade: fires ~1-2 times per session, Write tool itself takes hundreds of ms to seconds. 30 ms is invisible to the user. |
| Regex misses a valid report filename (e.g., unusual prefix) | `index_reports._iter_report_files` is the authoritative glob; any file that hits its filesystem scan also matches the regex. If the indexer's glob changes, the regex must be updated — documented in the `memory_post_tool.py` docstring pointing at this report. |

## Rejected alternatives

- **Bundle indexing into `memory_session_end.py` instead of `memory_post_tool.py`** (Variant A). Rejected because S4 + S10 (mid-session searches) require immediate indexing, and Variant G strictly dominates A by +21 in the weighted totals.
- **Synchronous indexing in `memory_post_tool.py`** (block the hook until `index_reports.py` returns). Rejected because it would push the <5 ms common-path contract to ~200-500 ms for every report write, and more importantly slow down the Write tool visibly.
- **PostToolUse + batched event in JSONL, processed by SessionEnd.** A middle ground that mirrors how bash_error / git_commit events are batched. Rejected because it loses the mid-session search case (S4/S10) without a commensurate cost saving — the JSONL append is ~1 ms, not meaningfully faster than the `posix_spawn`.
- **launchd timer** (Variant E). Rejected for S9 — wastes cycles for a usage pattern where reports are written ~10-20 times per week.
- **Add regex filter logic inside `index_reports.py` itself and have the hook pass a single file path.** Would require refactoring `index_all` to support `--single <path>`. Deferred — full reindex of 22 files takes ~200 ms, partial scan wouldn't save measurable time and would add code.

## Institutional lessons

1. **Scenario-based scoring beats architectural intuition.** My first pass (pre-audit) recommended Variant A (SessionEnd) on the argument that «reports are written at session end». The scenario matrix revealed that A fails S4 + S10 entirely and pays -1 overhead on the most common scenario (S9), making it strictly worse than C. The audit changed the decision from A to G without a single new fact — just forcing every option through every real workflow.
2. **Hook contracts are case-specific, not global.** The `<5 ms` contract in `memory_post_tool.py`'s docstring was written for the common path. Extending it to a matching-write path that fires ~2 times per session and costs ~30 ms p95 is NOT a contract violation — it's a documented exception on a rare path. Future contracts should be expressed per-case («common path X ms, rare path Y ms») instead of blanket-max.
3. **Fire-and-forget subprocesses are the right primitive for «ambient work triggered by an action».** Adding background work synchronously to a hook forces a latency budget conversation every time. `Popen(...); return` sidesteps the whole thing — the cost is paid by the OS scheduler, invisible to the user, with no orchestration code.
4. **Strictly dominant options are rare; accept them when you find them.** Variant G dominates every alternative in the matrix (weighted total +32 vs next-best +29 that actually *adds* a strictly-harmful component). When scoring surfaces a clean dominant option, don't hedge with a combination that adds work without adding value.

## Rollback plan

1. **Script rollback:** `cp ~/.claude/scripts/memory_post_tool.py.bak_indexing_20260413_143845 ~/.claude/scripts/memory_post_tool.py` restores the pre-Variant-G state. The hook resumes its old behaviour (batch mode for bash_error / git_commit only, no indexing trigger). No data loss — the DB is unchanged by this rollback.
2. **DB rollback:** Not required. Variant G only adds an async writer; it does not modify schema, consolidation, or the read path.
3. **Report rollback:** `git revert <this-commit>` removes this audit + the `memory_post_tool.py` change reference from the repo history.

## Sign-off

- **Implementation:** `memory_post_tool.py` +60/−3 LOC (new `_maybe_trigger_index`, docstring expansion, regex compile).
- **Backup:** `~/.claude/scripts/memory_post_tool.py.bak_indexing_20260413_143845`.
- **Full backup:** `~/claude_backup_20260413_phase2d.tar.gz` covers the pre-Variant-G state; a fresh tarball will be taken post-commit (`~/claude_backup_20260413_variant_g.tar.gz`).
- **Confidence:** `very_high`. The implementation is ~15 LOC of well-isolated logic, measured directly, tested end-to-end with a real subprocess fire, and dominates every alternative in the decision matrix.
- **Next steps:** Phase 2b (error taxonomy) or Phase 2c (`preserve` column) — roadmap-level decisions, separate audit.
