---
name: "Consilium 2026-04-18 — Memory / Instructions / MCP re-architecture"
description: "3 internal perspectives + GPT-5.4 neutral + GPT-5.4-pro against. Verdicts on Q1 memory re-arch, Q2 enforcement, Q3 MCP."
type: consilium
date: 2026-04-18
scope: global
preserve: true
category: Claude_Booster
---

# Consilium 2026-04-18 — Memory / Instructions / MCP re-architecture

## Executive summary

Three verdicts across memory, enforcement, and MCP layers, driven by the three-part continuity failure diagnosed in `audit_2026-04-17_agent_context_dysfunction.md` and stress-tested by the `scenario_planning_2026-04-18.md` pre-mortem.

**Q1 verdict — PARTIAL MIGRATION.** Stay on SQLite + FTS5, perform an additive schema v5 (no data migration of 100+ rows), add four columns: `status TEXT`, `verified_at TEXT`, `superseded_by_id INTEGER`, `resolve_by_date TEXT`. Drop confidence scoring as a primary field — GPT-pro convinced the panel it creates false-precision canon without provenance. Drop automatic decay — access_count already exists and would reward retrieval frequency over truth. Retrieval ordering updated to demote `status='superseded'`. Effort ~6h. Rollback via `ALTER TABLE DROP COLUMN` (SQLite 3.35+) or bak restore.

**Q2 verdict — HOOK + STRUCTURED JSON.** Ship PreToolUse hook on `/handover` + `TaskUpdate status=completed` that validates a structured JSON self-verification block. N/A escape hatch allowlist-driven (from `git diff --name-only`, not path-blocklist). Reject external judge agent (cost + collusion risk), reject typed frontmatter assertions for v1 (too rigid), reject JSON-alone (honor system). Hook must also reject fake-evidence patterns (`localhost`, `|| true`, missing status codes, curl outputs >20 lines away from the claim). Per-project opt-in flag `verify_gate: enforcing|warn|off` in `.claude/CLAUDE.md` frontmatter. Effort ~6h.

**Q3 verdict — STATUS QUO + TELEMETRY SCRIPT + --json CLI FLAG.** Keep `rolling_memory.py` as CLI subprocess; add `--json` output mode (70-80% of MCP's structured-reasoning value at 10% of the cost). Ship telemetry aggregator as a Python script surfaced at /start, not an MCP server. Reject promoting rolling_memory to MCP, reject evidence-gate MCP (duplicates the hook). Re-evaluate MCP in 60 days using telemetry data. Effort ~3h.

**Self-supersession clause.** This consilium expires **2026-07-17**. After that date, re-tag findings as `[UNDER REVIEW]` if hook/telemetry KPIs have not been re-verified.

---

## Context

This consilium was commissioned following `audit_2026-04-17_agent_context_dysfunction.md` (commit `90813f1`) and `scenario_planning_2026-04-18.md`, which together diagnosed why horizon ran 39 days in a symptom-chasing loop on portfolio NAV divergence. The audit identified three root causes:

1. **Authority without supersession** — `institutional.md:2` ("Permanent knowledge — never auto-prune") treats March rules as canon while April consiliums invalidate them, with no structural link between the two.
2. **Rules as prose, not blocking mechanics** — `pipeline.md` and `commands.md` describe required evidence collection (curl, RECON, hypothesis) but 10 consecutive handovers produced zero runtime artifacts.
3. **Runtime reintroduces divergence faster than patches** — dead broker-snapshot write path, async startup reconcile, is_reconciliation NAV exclusion recreate the class-of-bug every deploy.

The audit bundled 14 recommendations in 4 tranches. T2.3 (supersession tags) and T2.4 (Open Blockers) landed foreground in this session. T2.1 (VERIFY gate), T2.2 (Hypothesis Contract), T2.5 (RECON gate) were deferred pending this consilium's decisions on enforcement mechanism.

Scenario planning produced 10 ranked risks; the top three are cross-dependencies with this consilium:
- R1 VERIFY gate N/A escape hatch (H/H)
- R2 audit_2026-04-17 itself becoming next stale canon (H/H)
- R3 UNDER REVIEW tag metastasis without resolution dates (H/M)

This consilium answers: what enforcement surface + memory schema + tooling architecture minimizes those risks without introducing more ceremony than substance.

Referenced files: `audit_2026-04-17_agent_context_dysfunction.md`, `scenario_planning_2026-04-18.md`, `handover_2026-04-18_001728.md`, `~/.claude/rules/institutional.md`, `~/.claude/rules/commands.md`, `~/.claude/rules/tool-strategy.md`, `.claude/CLAUDE.md`, `~/.claude/scripts/rolling_memory.py`.

---

## Q1 — Memory re-architecture

### Agent positions

| Perspective | Position | KPI | Objection to others |
|---|---|---|---|
| **Memory architect** | Additive schema v5 (confidence + superseded_by_id + resolve_by_date); no decay; `check_review_ages.py` surfaces overdue tags at /start. | Latest-audit references ≥1/session; zero overdue UNDER REVIEW tags. | Enforcement without supersession edges moves theater upstream — agent cites stale canon, hook cannot tell it's stale. |
| **Workflow engineer** | Status quo DB + T2.3/T2.4 prose tags suffice; 100+ rows are already FTS5-indexed. | Schema without hook changes nothing. | Schema work is premature optimization; prove hook raises signal first. |
| **MCP specialist** | Indifferent on schema; need machine-readable output. Add `--json` CLI flag before schema changes. | Agent consumes structured output instead of shell-parsing prose. | Decay/confidence math is engineering-without-observability. |
| **GPT-5.4 neutral** | Endorse additive v5; no decay. Flag three confidence-without-decay failure modes (frozen certainty, update starvation, ranking ambiguity). Confidence must not dominate retrieval yet. | Upgrade KPI: "% cited rows ACTIVE or re-verified ≤30d" (not gameable by title-citation). | Supersession + resolve-by dates solve more than confidence does. |
| **GPT-5.4-pro against** | Refine: drop confidence as primary, add `status` enum + `verified_at` + `superseded_by_id` + `resolve_by_date`. Confidence without provenance is false precision. Retrieval must use new fields — today ORDER BY `priority DESC, created_at DESC` ignores supersession entirely. | Stale-authority KPI: superseded citations = 0; top-3 /start places superseder above superseded. | Schema is dead metadata until retrieval changes. |

### GPT synthesis

Both GPTs converged on: additive migration not replacement; no auto-decay (rewards retrieval over truth); supersession edges matter more than confidence numerics; retrieval ordering must change or schema work is cosmetic. GPT-pro's strongest addition: drop confidence from v1 because without `verified_at`/`verified_from` provenance it becomes the next bully value. GPT-neutral's strongest addition: the "latest audit referenced" KPI is gameable — switch to "% cited rows ACTIVE or re-verified ≤30d".

### Verdict

**PARTIAL — additive schema v5, supersession semantics, no confidence, no decay.** Effort ~6h. Rollback ALTER TABLE DROP COLUMN (SQLite 3.35+) or restore from `~/claude_backup_20260418_000829.tar.gz`.

Columns to add to `agent_memory`:

| Column | Type | Default | Semantics |
|---|---|---|---|
| `status` | TEXT | `'active'` | `active\|under_review\|superseded` — replaces the prose `[UNDER REVIEW]` tags in institutional.md with queryable state. |
| `verified_at` | TEXT | NULL | ISO timestamp of last explicit re-verification (audit/consilium that confirmed or re-enacted). |
| `superseded_by_id` | INTEGER | NULL | FK to `agent_memory.id` of the row that supersedes this one. NULL when active. |
| `resolve_by_date` | TEXT | NULL | ISO date; when a row is `under_review`, this is the deadline. Past-deadline rows surfaced by `check_review_ages.py` at /start. |

Changes to retrieval (`rolling_memory.py:1083-1205 build_start_context` and `:994-1042 search`):

```python
# Demote superseded, penalize past-deadline under_review rows
ORDER BY
  CASE status
    WHEN 'superseded' THEN 2
    WHEN 'under_review' THEN
      CASE WHEN resolve_by_date < date('now') THEN 2 ELSE 1 END
    ELSE 0
  END,  -- supersession demotion
  CASE WHEN category = ? THEN 0 ELSE 1 END,  -- existing category bias
  priority DESC, created_at DESC
```

New companion script (~1h): `~/.claude/scripts/check_review_ages.py` — runs on /start (`SessionStart` hook), emits a block listing any `status='under_review' AND resolve_by_date < today()` rows. Format:

```
=== OVERDUE [UNDER REVIEW] TAGS ===
  * [audit_2026-04-15] 7 IBKR rules — resolve_by 2026-05-15 (3 days left)
  * [audit_2026-02-16] WebSocket reconnect — resolve_by 2026-05-16 (OVERDUE +4d)
```

**Migration path**: (1) Mirror v1→v4 ALTER ADD pattern in `init_db()` (`:250-310`). (2) Existing rows default `status='active'`, nulls elsewhere — zero-risk. (3) Port the 7 prose `[UNDER REVIEW]` tags from institutional.md §Financial/Trading to DB rows with `resolve_by_date='2026-05-15'`; keep prose as visual aid but DB becomes source of truth.

**KPIs**: (a) superseded-source citations in handovers = 0 (measurable once `superseded_by_id` populated); (b) top-3 /start on contradicted topics ranks superseder above superseded; (c) overdue `[UNDER REVIEW]` >7d = 0; (d) % cited rows ACTIVE or re-verified ≤30d ≥80% (measured by telemetry script from Q3).

**Rejected alternatives** (Q1): `confidence REAL` column — false precision without provenance, deferred to v6 paired with `verified_at`/`verified_from`. Auto time/access decay — rewards retrieval frequency over truth, demotes durable truths, tuning-drift failure. Full model migration (NoSQL/graph/vector) — overshoot for 100 rows; FTS5 handles current query patterns. Separate supersessions table — over-engineered for <30 edges/year; single FK column suffices until chains exceed 3 hops. Prose-only T2.3 (status quo) — scenario §1.6 metastasis: prose without enforcement degrades into universal distrust.

---

## Q2 — Instruction enforcement

### Agent positions

| Perspective | Position | KPI | Objection to others |
|---|---|---|---|
| **Memory architect** | Hook without supersession semantics blocks on active rules but waves through stale-but-cited ones. | Defers to Workflow. | Hook needs memory-layer integration to reject citations of `status='superseded'` rows. |
| **Workflow engineer** | **PreToolUse hook + structured JSON self-verification** (a+c). Hook validates artifact adjacency; rejects N/A unless `git diff` is entirely in allowlist. Per-project `verify_gate` flag. | Curl/SQL artifacts/handover ≥1 (baseline 0/10); N/A ratio <30%; blocked handovers/week trended. | Hook is the bite; schema is observability; MCP adds protocol without bite. |
| **MCP specialist** | Hook OK for block/allow, but agent needs pre-emptive "what satisfies this gate?" query. `--json` CLI captures 70-80% of this. | Zero hook-surprises (agent sees gate before /handover, not after). | Hook without agent-queryable policy becomes opaque; blocked sessions retry identically. |
| **GPT-5.4 neutral** | Endorse (a)+(c). Reject (d) external judge — latency+collusion. Reject (b) typed frontmatter for v1 — too rigid. Hook must validate **artifact adjacency**, not just keyword presence. | Blocked handovers/week; N/A ratio trend; overdue UNDER REVIEW tags. | N/A defense needs two locks: edited-path + artifact-adjacency. |
| **GPT-5.4-pro against** | Make N/A **allowlist-based from `git diff --name-only`**, not blocklist. Blocklist (backend/, frontend/) defeated within weeks by Dockerfiles, CI configs, nonstandard dirs. Allowlist (docs/reports/.claude/tests/) is narrow and durable. Explicitly reject `localhost`, `\|\| true`, missing HTTP status, SQL without rowcount. | Stale-authority KPI + fake-evidence rejection rate + agent-adaptation lag. | Blocklist is brittle against an adapting agent. |

### GPT synthesis

Both GPTs unanimously endorsed hook+JSON v1. Crux disagreement: FOR/neutral accepted a blocklist of runtime-touching paths (`backend/`, etc.); AGAINST/pro argued blocklist is defeated within 3-5 weeks by Dockerfiles, CI configs, nonstandard dirs, and config files with runtime impact. Panel accepts GPT-pro's **allowlist inversion** as v1 primary gate: N/A allowed only when `git diff --name-only` is entirely in `{docs/, reports/, .claude/, tests/, *.md}`. Blocklist retained as belt-and-suspenders secondary. Both GPTs also required explicit fake-evidence rejection: curl must have status/body within ±20 lines and no `|| true`; SQL must have rowcount; DevTools must have a specific response ID + status.

### Verdict

**HOOK + STRUCTURED JSON + ALLOWLIST N/A + FAKE-EVIDENCE REJECTION.** Effort ~6h. Rollback: remove hook entry from `~/.claude/settings.json`.

**Implementation:**

1. **Script** `~/.claude/scripts/verify_gate.py` (~4h):
   - Stdin: Claude Code hook payload (tool name, tool_input, transcript).
   - Fire only on `Bash` calls containing `/handover` or on `TaskUpdate` with `status=completed`.
   - Parse the last 200 lines of transcript for a JSON block matching:
     ```json
     {"verified": {"status": "pass|na", "evidence": ["curl https://api.../status 200", "psql: 3 rows"], "reason_na": null | "<text>"}}
     ```
   - Pass conditions:
     - `status=pass` AND at least one evidence entry matches `{curl|psql|sqlite3|list_network_requests}` AND artifact-adjacency validated (response body/status within ±20 lines of the claim AND not matching weak-patterns: `localhost(:\d+)?/`, `\|\| true`, empty response, `curl -s` without output redirection).
     - OR `status=na` AND `git diff --name-only HEAD~1 HEAD` intersects only {`docs/`, `reports/`, `.claude/`, `tests/`, `*.md`, comment-only line changes via `git diff --shortstat` heuristic}.
   - Fail: emit stderr block-message with specific reason ("curl found but no HTTP status within 20 lines" / "N/A claimed but diff touched backend/foo.py — not an allowlisted path").
   - Exit code: 0 pass, 2 block (Claude Code hook convention).

2. **Rule update** `~/.claude/rules/commands.md` §handover (~30min):
   - Before `git add + commit + push`, agent MUST emit the JSON block. Rule includes a template and enumerates fake-evidence patterns that will be rejected.

3. **Per-project opt-in** via `.claude/CLAUDE.md` frontmatter (~30min):
   - New key `verify_gate:` with values `enforcing` (block on fail), `warn` (log on fail but pass), `off` (skip). Default: `off` for projects without `deploy/` or `.github/workflows/` directory (detected by `verify_gate.py` on first run per project).
   - Promotion path: new projects start `off`, auto-promoted to `warn` after first commit touching `backend/` or `deploy/`, manually promoted to `enforcing` by Dmitry once stable.
   - **Defensive note** (scenario §5 warning): the flag file itself is prose that can decay. Mitigation: `verify_gate.py` logs every session's detected flag state to `~/.claude/logs/verify_gate_decisions.jsonl`; weekly review of distribution catches projects silently flagged `off`.

4. **settings.json hook registration** (~15min):
   ```json
   "PreToolUse": [{
     "matcher": {"tool_name": "Bash"},
     "hooks": [{"type": "command", "command": "python3 /Users/dmitrijnazarov/.claude/scripts/verify_gate.py"}]
   }]
   ```

5. **Hook self-test** (~1h, from scenario §4.1): first line of `verify_gate.py` reads `CLAUDE_CODE_HOOK_SCHEMA_VERSION` env-var or stdin schema marker; if unrecognized emits loud `SYSTEM WARNING: hook version mismatch, this hook may not be enforcing` to stderr + log. Combined with `check_hook_health.py` on /start, makes silent no-op impossible across Claude Code upgrades.

**KPIs:**

| KPI | Baseline | Target | Window |
|---|---|---|---|
| Curl/SQL artifacts per DONE handover | 0/10 (horizon baseline) | ≥1/session | 2 weeks |
| N/A annotation ratio | n/a | <30% of handovers | 4 weeks |
| Blocked handovers per week | 0 | >0 (proves gate bites), trending down | 4 weeks |
| Fake-evidence pattern rejections | 0 | >0 first week (proves filter works), then trending down | 4 weeks |
| Verify-gate decision log entries | 0 | ≥1 per project per session | 1 week |

**Rejected alternatives** (Q2): Typed frontmatter assertions — too rigid for heterogeneous v1 tasks; narrow opt-in possible in v6. External judge agent — ~$130/yr direct cost + 5-10s latency + collusion risk (LLM judging LLM on same prose patterns). JSON-only self-verification (no hook) — honor system = current failure mode. Blocklist-based N/A — defeated within weeks by Dockerfiles/CI/nonstandard dirs (GPT-pro). Hook without artifact-adjacency — agent adapts with `curl -s localhost || true` (GPT-neutral). Global enforcement (no per-project flag) — cross-project contamination per scenario §6.2.

---

## Q3 — MCP changes

### Agent positions

| Perspective | Position | KPI | Objection to others |
|---|---|---|---|
| **Memory architect** | MCP only helps if agent queries structured supersession chains — FTS5 suffices today. | n/a | MCP-for-its-own-sake is the over-engineering scenario warned against. |
| **Workflow engineer** | Hook is bite, telemetry is observability. Python script now; revisit MCP if telemetry grows rich. | Telemetry at /start = 5 signals. | Agent doesn't need interactive reasoning over telemetry. |
| **MCP specialist** | Keep CLI+hooks; add `--json` output ASAP; telemetry as script not MCP; reconsider MCP in 60 days. | `--json` adopted in ≥2 rules within 30 days; explicit go/no-go on 2026-06-18. | Don't mistake "not now" for "never". Retain option. |
| **GPT-5.4 neutral** | Endorse hooks-not-MCP for current flows. MCP becomes attractive only for future cross-session telemetry queries. | n/a | `--json` is the pragmatic middle ground. |
| **GPT-5.4-pro against** | `--json` captures 70-80% of MCP's value at 10% of the cost. Telemetry is the antidote to "forcing functions decay into theater". Track: blocked handovers, N/A ratio, unchanged hypothesis streaks, overdue tags, superseded citations. | Telemetry at /start = 5 anti-theater metrics; agent references telemetry in planning. | Server migration premature; JSON CLI is v1. |

### GPT synthesis

Both GPTs endorsed: (a) keep rolling_memory on CLI, (b) do not build evidence-gate MCP (duplicates hook), (c) do not build rolling_memory MCP (no observability payoff), (d) ship telemetry as a script, (e) add `--json` CLI output first as the cheap structured-tool win. The primary insight from GPT-pro: **JSON CLI output is the 80/20 of MCP benefits** without the protocol surface. Lets Claude consume status/supersession/confidence structures without parsing prose.

GPT-neutral explicitly noted: for **future** queries like "show me last 5 blocked verify events for this project", MCP becomes attractive. But that's phase 2, not week 1.

### Verdict

**STATUS QUO + `--json` FLAG + TELEMETRY SCRIPT.** Effort ~3h. Rollback: remove script + delete flag handling.

**Implementation:**

1. **`rolling_memory.py --json` flag** (~1h):
   - Modify `_cli()` to accept `--json` globally. When set, every subcommand emits a single JSON document to stdout with full schema (status, verified_at, superseded_by_id, etc.) instead of prose lines.
   - `start-context --json` emits `{"rows": [{"id": N, "title": "...", "status": "active", "category": "...", "source": "path"}]}`.
   - Backward compatible — absent flag keeps current prose output, so existing hooks and rule prose don't break.

2. **Telemetry script** `~/.claude/scripts/telemetry_agent_health.py` (~2h):
   - Input: last 10 handover files from current project + rolling_memory.db read-only.
   - Outputs (at /start, as a section in SessionStart hook output):
     ```
     === AGENT HEALTH (last 10 handovers, last 4 weeks) ===
     Evidence artifacts (curl/psql/DevTools): 7/10 handovers ✓ (target ≥8/10)
     N/A ratio: 2/10 (20%) ✓ (target <30%)
     Blocked handovers: 1 this week (gate is biting)
     Overdue [UNDER REVIEW] tags: 0 ✓
     Stale citations (cited superseded row without superseder): 0 ✓
     Unchanged hypothesis streak: 2 sessions on topic "broker parity" (watch — alert at 3)
     ```
   - Five signals chosen to address the scenario's top-10 risks directly.

3. **Rule update** `~/.claude/rules/commands.md` §start step 2b: mention telemetry section alongside existing `start-context` step.

4. **60-day MCP reconsideration trigger** — if telemetry shows (a) agent parsing prose errors in >2 instances, or (b) need for cross-session telemetry queries agent can't answer from /start output, reconsider evidence-gate or telemetry MCP. Create calendar entry for 2026-06-18.

**KPIs:**

| KPI | Baseline | Target | Window |
|---|---|---|---|
| Rules/hooks consuming `--json` output | 0 | ≥2 within 30 days | 30 days |
| Telemetry signals surfaced at /start | 0 | 5 | 1 week from ship |
| Agent referencing telemetry numbers in planning | 0 | ≥1 mention in /start handover | 2 weeks |
| MCP reconsideration decision | n/a | Explicit go/no-go on 2026-06-18 | 60 days |

**Rejected alternatives** (Q3): Promote rolling_memory to MCP server — solves no diagnosed failure, adds protocol+async+deployment complexity, zero observability payoff. Evidence-gate MCP — duplicates T2.1 hook, creates policy drift and "which one bites?" ambiguity (MCP tool calls can be skipped, hook cannot). Telemetry aggregator MCP — overkill for surfacing 5 signals at /start; revisit at 10+ signals. Pure status quo — misses scenario §5.1 measurement requirement.

---

## Concrete change plan

| Change | File/script | Effort | Risk | Rollback | KPI |
|---|---|---|---|---|---|
| **C1** Add schema v5 columns (status, verified_at, superseded_by_id, resolve_by_date) | `~/.claude/scripts/rolling_memory.py` `init_db()` | 2h | Low (ALTER ADD is additive, mirrors v1-v4 migrations). SQLite ≥3.25 accepts CHECK on ADD COLUMN. | `DROP COLUMN` (SQLite 3.35+) or restore from `~/claude_backup_20260418_000829.tar.gz`. | Schema migration succeeds with zero data loss on 100+ rows. |
| **C2** Update retrieval ordering in `build_start_context` + `search` to demote superseded | `rolling_memory.py:994-1205` | 1h | Low — existing rows default `status='active'` so ordering is unchanged until rows get tagged. | Revert ORDER BY clauses. | Top-3 /start results on contradicted topics put superseder above superseded. |
| **C3** Port prose `[UNDER REVIEW]` tags from institutional.md to DB rows with `status='under_review'` + `resolve_by_date` | Data migration — one-off SQL script `migrate_review_tags_to_db.py` | 1h | Low — idempotent + preserve=1 protection. | DELETE the migrated rows. Prose tags remain as backup. | 7 horizon IBKR rules have DB rows with `status='under_review'`, `resolve_by_date='2026-05-15'`. |
| **C4** Write `check_review_ages.py` — surface overdue tags at /start | New script | 1h | None (read-only). | Delete script + `/start` rule reference. | Overdue `[UNDER REVIEW]` tags >7 days = 0 at steady state. |
| **C5** Self-supersession clause added to `audit_2026-04-17_agent_context_dysfunction.md` + this file | Both reports | 15min | None. | Revert commit. | Audit explicitly expires 2026-07-17; this consilium expires 2026-07-17. |
| **C6** Write `verify_gate.py` — PreToolUse hook with allowlist N/A + artifact-adjacency + fake-evidence rejection | New script | 4h | Medium — false positives on legitimate refactor could frustrate sessions. Mitigation: start in `warn` mode globally for 1 week, promote to `enforcing` after tuning. | Remove hook entry from `~/.claude/settings.json`. | Curl/SQL artifacts per DONE handover ≥1; N/A ratio <30%. |
| **C7** Add `verify_gate: enforcing\|warn\|off` to `.claude/CLAUDE.md` project frontmatter with default-detection logic | `verify_gate.py` + per-project CLAUDE.md | 1h | Low — defaults to `off` if undetectable. | Remove key. | Weekly review of `verify_gate_decisions.jsonl` shows correct per-project distribution. |
| **C8** Update `commands.md` §handover — mandatory JSON verification block | `~/.claude/rules/commands.md` | 30min | Low. | `git revert`. | Agent emits `{"verified": ...}` block in 100% of handovers after rule update. |
| **C9** Hook self-test (scenario §4.1 defense) | `verify_gate.py` + `check_hook_health.py` | 1h | Low. | Remove. | Hook warns on Claude Code version mismatch before silent no-op. |
| **C10** Add `--json` flag to `rolling_memory.py` | `rolling_memory.py` `_cli()` | 1h | Low — backward-compatible. | Remove flag branch. | ≥2 rules/hooks consume `--json` within 30 days. |
| **C11** Write `telemetry_agent_health.py` — 5 signals at /start | New script | 2h | None (read-only). | Delete script + /start rule reference. | 5 signals at /start; agent references ≥1 in planning within 2 weeks. |
| **C12** Integrate scenario-planner's 5 companion artifacts (narrow N/A, self-supersession, review-age check, hypothesis-diff, hook-health canary) | Covered by C4, C5, C6, C9 + new `hypothesis_diff.py` (~1h) | +1h | Low. | Per-artifact revert. | 8/10 scenario top risks closed. |

**Total effort**: ~15h across 12 changes. Sequencing: C1→C2→C3→C4→C5 (memory hygiene, 5.5h), then C10→C11 (telemetry + JSON, 3h), then C6→C7→C8→C9 (enforcement hook, 6.5h). Ship in three commits by concern domain.

---

## Open questions for Dmitry

1. **Schema v5 migration timing** — deploy now (alongside T2.3 already landed) or wait for T1 horizon runtime fixes to soak 7 days first? Recommendation: deploy now, since schema changes are independent of horizon runtime and enable C4 (review-age check) which is a scenario P0 mitigation.

2. **Verify gate rollout mode** — start `warn` globally for 1 week then promote to `enforcing` on Claude_Booster + horizon only, OR start `enforcing` immediately on Claude_Booster + horizon? Recommendation: global `warn` for 1 week captures adaptation baseline, then selective `enforcing`.

3. **Allowlist N/A — exact path list.** Proposed: `docs/`, `reports/`, `.claude/`, `tests/`, `*.md`, `*.txt`, `README*`. Anything under these → N/A allowed. Edits to Python/TypeScript/Dockerfile/*.yml/*.toml/*.sql anywhere → evidence required. Should the list include `*.html` (for HTML visualizations) or treat those as runtime?

4. **Fake-evidence weak-pattern list.** Proposed blocks: `localhost`, `127.0.0.1` without explicit port binding, `\|\| true`, `curl -s` without `-o`/`-w`, empty response body, status code missing. Any false-positive scenarios?

5. **Per-project flag default for horizon and Claude_Booster specifically** — both have `deploy/` or `.github/workflows/` so auto-default would be `warn`, then promote to `enforcing`. Acceptable?

6. **MCP reconsideration date** — 2026-06-18 (60 days) acceptable, or earlier trigger (e.g., first week agent shows prose-parsing errors)?

7. **`--json` output adoption path** — ship passive (opt-in by scripts), or actively update 2-3 rules to consume it within 30 days as forcing function for adoption?

8. **Telemetry alerting threshold** — 5 signals surfaced at /start; should any signal ALSO trigger a proactive `AUTO-CONSILIUM` (per core.md auto-consilium clause) if it trips? Recommendation: unchanged-hypothesis-streak ≥3 triggers auto-consilium; others warn only.

---

## Self-supersession clause

**This consilium expires 2026-07-17 (90 days from 2026-04-18).**

Before citing this consilium as authority after that date, run:

1. `python ~/.claude/scripts/check_review_ages.py` — verify any tags this consilium created are resolved or re-verified.
2. `python ~/.claude/scripts/telemetry_agent_health.py --project horizon --window 30d` — verify the 5 KPI signals are hitting targets.
3. `ls -la ~/.claude/scripts/verify_gate.py` — verify the hook exists and `settings.json` references it.
4. Check `~/.claude/logs/verify_gate_decisions.jsonl` tail — verify gate has been firing (non-empty file, recent entries).

**If any of those checks fail:**
- Re-tag this file's frontmatter from `preserve: true` (unchanged) to include a new key `status: under_review` (via `ALTER TABLE` analog — update the DB row for this report and update the frontmatter).
- Spawn a mini-consilium (`/consilium memory_instructions_mcp_re-audit`) to re-verify each verdict against actual telemetry.
- Either confirm verdicts as `active` + set new `verified_at` + `resolve_by_date`, or mark specific verdicts `superseded_by_id=<new audit id>`.

This clause is the same anti-staleness principle this consilium imposed on `institutional.md` — applied to the consilium itself. `preserve: true` preserves the document; it does **not** preserve the trust level of the document.

---

## References

**Source reports** (read in full via `relevant_files` parameter to PAL consensus, not pasted into prompts):

- `/Users/dmitrijnazarov/Projects/Claude_Booster/reports/audit_2026-04-17_agent_context_dysfunction.md`
- `/Users/dmitrijnazarov/Projects/Claude_Booster/reports/scenario_planning_2026-04-18.md`
- `/Users/dmitrijnazarov/Projects/Claude_Booster/reports/handover_2026-04-18_001728.md`
- `/Users/dmitrijnazarov/.claude/rules/institutional.md`
- `/Users/dmitrijnazarov/.claude/rules/commands.md`
- `/Users/dmitrijnazarov/.claude/rules/tool-strategy.md`
- `/Users/dmitrijnazarov/Projects/Claude_Booster/.claude/CLAUDE.md`
- `/Users/dmitrijnazarov/.claude/scripts/rolling_memory.py`

**PAL consensus continuation ID**: `863a6246-89b3-4fd5-a8da-85556ee7b105` (gpt-5.4 neutral + gpt-5.4-pro against, confidence 8/10 each).

**Related foreground changes this session** (already landed):
- T2.3 `[UNDER REVIEW]` tags applied to 7 IBKR rules in `~/.claude/rules/institutional.md` §Financial/Trading.
- T2.4 Open Blockers section added to `~/.claude/projects/-Users-dmitrijnazarov-Projects-horizon/memory/MEMORY.md`.
- Session backup `~/claude_backup_20260418_000829.tar.gz` (50 MB) covers all `~/.claude/` modifications.
