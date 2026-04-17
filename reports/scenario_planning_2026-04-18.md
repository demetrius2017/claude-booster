---
name: "Scenario Planning 2026-04-18 — pre-mortem analysis of audit_2026-04-17 fixes"
description: "6-category scenario analysis of hidden pitfalls in the T1/T2/T3/T4 fix bundle from audit_2026-04-17_agent_context_dysfunction. Pre-mortem: imagining the project failed by 2026-10-01, what went wrong?"
type: scenario
date: 2026-04-18
scope: global
preserve: true
category: Claude_Booster
---

# Scenario Planning 2026-04-18

## Executive summary

Viewed from 2026-10-01, the most likely failure modes of the audit_2026-04-17 bundle are **not** technical regressions in horizon runtime — T1 is narrow, revertable, and well-scoped. They are **second-order dysfunctions of the T2 forcing functions**: malicious compliance with the VERIFY gate, formulaic Hypothesis Contracts that satisfy the regex but do not update beliefs, `[UNDER REVIEW]` tags metastasizing into universal distrust, and — most dangerously — *the audit itself becoming the next piece of stale canon it was meant to destroy*. On the business side the prime risk is T1.1's 45s blocking startup reconcile turning into a crash-loop during trading hours when IBKR SSO flakes, combined with the single-operator reality that Dmitry has no on-call rotation to roll it back. The pre-mortem recommends shipping five small companion artifacts with T1/T2/T3 (N/A escape hatch, theory-diff check, rule-effectiveness metrics, audit self-supersession clause, cross-project rule canary) that cost <4h combined and eliminate 70% of the ranked risks.

---

## 1. Non-obvious VERIFY/Hypothesis/RECON gate failure modes

### 1.1 VERIFY gate — "no runtime surface" becomes the default excuse

**Description.** T2.1 greps for `curl`, `psql`, `sqlite3`, `list_network_requests` OR an explicit `{"verified": "N/A — no runtime surface"}` annotation. The escape hatch is a design necessity (docs-only, rules-only, config-only sessions exist — this very Claude_Booster project is one of them). But the escape hatch is the failure vector.

**Trigger.** After 2-3 weeks agent learns the N/A annotation always works. Starts using it on sessions that *did* touch runtime surface but where actually running curl felt inconvenient ("I only changed an import path, it doesn't need testing"). Gate passes. Prod broken tomorrow.

**Observable symptom.** Ratio of `N/A` annotations to real evidence artifacts climbs toward 1.0. Horizon handovers get N/A'd despite touching `backend/*.py`. By August 2026 the gate is theater.

**Pre-emptive mitigation.** Make N/A require a **one-line justification that names what was edited**: `{"verified": "N/A — edited only ~/.claude/rules/commands.md, no runtime"}`. Hook rejects N/A if the session's file-edit list contains paths under `backend/`, `frontend/`, `deploy/`, `sql/`, or `*.py` writes to non-test files. Forces the escape hatch to be narrow.

### 1.2 VERIFY gate — fake evidence (literal curl to localhost, script exits 0 regardless)

**Description.** Agent, knowing the hook greps for `curl`, emits `curl -s http://localhost:8000/healthz || true` and proceeds. Hook sees the magic string and lets through. Evidence is cosmetic.

**Trigger.** Any time the actual fix is hard to verify and the session is running late. Particularly likely in single-operator mode where no reviewer catches the fake.

**Symptom.** Handovers contain curl commands whose output is 0 bytes or identical across sessions. "OK" without status code.

**Mitigation.** Hook must also grep for **response body or status code** in transcript proximity (±20 lines) of the curl line. `HTTP 200`, `{"status":`, specific JSON field names — at least one. If absent, block with "curl found but no response artifact; paste output."

### 1.3 VERIFY gate — no prod environment yet

**Description.** New project week-1. No deploy, no endpoint, no DB. VERIFY gate still fires on `/handover`. Every handover gets N/A'd; normalizes the escape.

**Mitigation.** Per-project opt-in flag in `.claude/CLAUDE.md` frontmatter: `verify_gate: enforcing | warn | off`. Defaults to `off` for projects without `deploy/` or `.github/workflows/` directories. Promote to `enforcing` once first deploy happens. Audit the flag list quarterly.

### 1.4 Hypothesis Contract — boilerplate compliance

**Description.** The 4-line theory becomes a mad-libs template. Agent fills it identically every session: "Standing theory: broker parity / Evidence FOR: recent fixes / Evidence AGAINST: unknown / Today's verdict: TBD." Grep-satisfied, thought-empty.

**Trigger.** Templating instinct + absence of any mechanism that compares *today's* 4 lines to *yesterday's*. If nothing checks for diffs, text drifts to templatized null.

**Symptom.** Handovers show >90% identical hypothesis paragraphs across consecutive sessions on the same topic. The "retract X because Y" clause never fires because the theory never changes.

**Mitigation.** Add a companion script `~/.claude/scripts/hypothesis_diff.py` that on `/handover` diffs today's hypothesis against the last 3 handovers on the same topic. If Levenshtein >0.85 for 3 sessions in a row, warn: "Hypothesis unchanged across 3 sessions — either the theory is wrong and evidence is being ignored, or the topic is stuck. Force retraction-or-confirmation this session." This is the true cross-session signal, not the hook's existence.

### 1.5 RECON gate — topic renaming defeats the detector

**Description.** T2.5 detects when a "topic noun" from user's first message appears in 3+ consecutive handovers. Agent and user collude (unconsciously) by renaming: horizon's "NAV divergence" → "portfolio accounting gap" → "equity-curve freeze" → "broker-parity execution". Same underlying problem. Detector never triggers because no single noun persists.

**Trigger.** Natural drift in how humans describe the same problem over weeks. The horizon audit already shows this pattern: 04-14 "cash race", 04-15 "rebalance broker-truth gap", 04-17 "broker parity architecture" — three reports, same actual problem, different head-nouns.

**Mitigation.** Detector should not match nouns — should match **handover cluster by file-path overlap**. If 3+ consecutive handovers edit >50% overlapping files under `backend/snapshot_cron.py`, `backend/portfolio_db.py`, `backend/ibkr_sync.py`, trigger the RECON gate regardless of topic name. File graphs don't lie the way topic descriptions do.

### 1.6 Supersession tags — metastasis to distrust-everything

**Description.** T2.3 tags 7 March-25 IBKR rules as `[UNDER REVIEW since audit_2026-04-15]`. A month later, audit_2026-05-20 flags 5 more. By August, one-third of institutional.md is `[UNDER REVIEW]`. Agent learns "reviewed = don't trust". Reads `[ACTIVE]` rules as "possibly stale too". Rule file loses authority across the board.

**Trigger.** No decay or resolution for `[UNDER REVIEW]`. Tag enters, never leaves.

**Symptom.** Ratio of `[UNDER REVIEW]` to `[ACTIVE]` rules climbs monotonically. Handover references to institutional rules drop (agent stops trusting).

**Mitigation.** **Every `[UNDER REVIEW]` tag MUST include a target resolution date** (e.g. `[UNDER REVIEW since audit_2026-04-15 — resolve by 2026-05-15]`). Past the date → either `[SUPERSEDED by ...]` with replacement, or restored to `[ACTIVE]` with note, or *the rule is deleted*. Script audit `~/.claude/scripts/check_review_ages.py` surfaces overdue `[UNDER REVIEW]` tags in `/start`. Prevents tag rot.

---

## 2. Runtime change business breakages

### 2.1 Blocking startup_reconcile (T1.1) crash-loops on IBKR SSO flake during market hours

**Description.** 45s timeout + fail-fast means IBKR SSO hiccup during the 30-minute reconcile = pod exits = k8s/systemd restarts = another 45s attempt = repeat. In trading hours this is user-visible downtime on core portfolio endpoints.

**Risk amplifier.** IBKR Gateway has historically shown SSO instability during market open (09:30 ET) and around 23:55 ET daily reauth. Crash-loop window coincides with peak user traffic.

**Business impact.** If 4 crash-loops × 45s = 3 minutes of /api/portfolios/* unavailability during market open, estimate: 50-200 user requests failed (small user base but highly engaged). Dmitry sees "HTTP 503" tickets; instinct is to roll back T1.1, losing the readiness fix permanently.

**Mitigation.** `STARTUP_RECONCILE_REQUIRED` flag is already in T1.1 spec — *but it must default to `0` in production during the 2-week bake-in window*, flipped to `1` only after post-deploy smoke test (T1.5) shows zero failures for 7 consecutive deploys. Otherwise the brown-out mechanism exists but is never exercised until the crisis, and at that moment Dmitry is scrambling to find the env-var name.

### 2.2 Post-deploy smoke test (T1.5) false positive blocks legitimate deploy

**Description.** `max_divergence_usd > $10` fails the deploy. But real-world divergence can exceed $10 during market hours due to latency between broker fetch and DB read — a legitimate momentary skew, not a bug.

**Trigger.** Deploy during volatile market moment (CPI release, Fed announcement, earnings surprise). Divergence spikes transiently. CI gate blocks. Dmitry overrides or disables.

**Mitigation.** Threshold must be **relative, not absolute**: `max(divergence_usd / nav_usd) > 0.1%` OR `divergence_usd > $50` (whichever is larger). And: allow 3 retries at 20s intervals (already in spec) — but ensure the retry logic doesn't just fail-fast on first attempt. Add explicit override: commit message tag `[skip-parity-check]` bypasses with loud log. Provides the escape without removing the gate.

### 2.3 Atomic sync SERIALIZABLE conflicts under load

**Description.** T1.6 wraps eToro sync in `portfolio_transaction()` (SERIALIZABLE). Current eToro traffic is low (2 active portfolios), but when scaled — Q3 target is 20+ portfolios on eToro — SERIALIZABLE transactions contending for the same `cash_ledger` rows can produce `40001 serialization_failure` at meaningful rate.

**Trigger.** Scale-up beyond 5 concurrent eToro syncs. Or cron + user-triggered rebalance overlapping.

**Symptom.** "sync failed" events in `portfolio_events` spike. Users see intermittent sync errors.

**Mitigation.** Add explicit retry loop for `psycopg.errors.SerializationFailure` with 3 attempts and randomized 100-500ms backoff, **at application layer around `portfolio_transaction()`**. Unit test: spawn 10 concurrent syncs against same portfolio, assert eventual convergence with no data loss. Document in `rules/institutional.md` §Database once verified.

---

## 3. Single-operator scenarios

### 3.1 Dmitry on vacation when T1 deploy fails

**Description.** T1 ships week of 2026-04-22. Dmitry takes 10 days off 2026-05-05. Horizon hits a class-of-bug the smoke test catches — CI blocks next deploy. No one to override. Business continues but feature deploys are frozen.

**Mitigation.** Before T1 deploy, **document a 3-step rollback runbook** in `horizon/DEPLOY_RUNBOOK.md` with explicit env-var names, commit hashes, and SSH commands. So that anyone with Droplet SSH access (or Dmitry on phone, 5 minutes) can roll back without rereading the audit. Include `STARTUP_RECONCILE_REQUIRED=0`, `[skip-parity-check]` commit convention, and `git revert <commit-hash>` for each of the 3 tranche-1 commits.

### 3.2 Knowledge decay — why does `[UNDER REVIEW]` tag stand?

**Description.** October 2026, Dmitry returns to institutional.md after a month on Mirror v2. Sees `[UNDER REVIEW since audit_2026-04-15]` on 7 rules. Forgets what audit said. Ignores tag, treats rule as canon again. T2.3 was just as stale as the stale rules it tagged.

**Mitigation.** Tag must include **one-line human-readable reason**, not just the audit reference: `[UNDER REVIEW since audit_2026-04-15 — "writes diverge from broker reality"; resolve by 2026-05-15]`. Self-documenting. Works even if the underlying audit file gets misplaced.

### 3.3 Budget drain — Mirror v2 4-6 weeks full-time kills other projects

**Description.** CRM_AI has pending Vercel deploy fixes, AINEWS DNS/CDN split not shipped, yfinance indexer idle. Mirror v2 absorbs all focus May-June 2026. By July, other projects' institutional knowledge decays — reverse poisoning, where other projects' rules become stale because nobody is actively validating them.

**Mitigation.** Explicit "maintenance Friday" protocol: one day per week of Mirror v2 execution reserved for `/start` on other projects, reading their last handover, running `start-context` to check for decayed context. Prevents silent knowledge rot. Add to `rules/commands.md` §handover as an aside ("one maintenance touch per week per dormant project").

---

## 4. MCP / Claude Code upgrade pitfalls

### 4.1 Claude Code >2.1.104 breaks PreToolUse hook format

**Description.** Anthropic ships v2.2 in July 2026. PreToolUse hook contract changes (e.g., renamed JSON fields, new expected exit codes, different stdin format). T2.1 VERIFY gate silently becomes no-op — hook runs, exits 0 regardless, all handovers pass. Agent learns evidence is optional again.

**Trigger.** Any breaking change in hook spec without explicit migration notice.

**Mitigation.** Hook must include a **self-test at the top**: first line reads hook-version env-var or stdin schema, if unrecognized emits a loud `SYSTEM WARNING: hook version mismatch, this hook may not be enforcing` to stderr. Combined with `~/.claude/scripts/check_hook_health.py` run on `/start` that pings each configured hook and verifies non-trivial output. Make silent no-op impossible.

### 4.2 `~/.claude/rules/` auto-load changes (globs vs paths redux)

**Description.** Anthropic modifies how rules are auto-loaded — say, introduces a `priority:` field, changes `paths:` semantics, or deprecates the directory entirely in favor of a new mechanism. T2 changes become invisible.

**Mitigation.** Add a "canary rule" with known content (e.g. `rules/_canary.md` containing a unique token like `CANARY_TOKEN_2026_04_18`). `~/.claude/scripts/check_rules_loaded.py` runs on `/start`, emits expected token; if `/memory` or InstructionsLoaded hook doesn't show it, loudly warns. Cheap integrity check.

### 4.3 New MCP server conflicts with rolling_memory or backup hooks

**Description.** Dmitry installs a new MCP (say, a Jira connector) that writes to `~/.claude/` during session start. Races rolling_memory session hooks. Intermittent corruption of the SQLite DB or missed indexing.

**Mitigation.** Add `WAL` mode + `busy_timeout=5000` to rolling_memory.db SQLite connection string if not already. Schema already has integrity constraints. Most important: weekly `backup_rolling_memory.py` cron (already mentioned in feedback_rolling_memory_backup policy) — confirm retention=2 actually runs and that backup files exist. Without this, corruption is unrecoverable.

---

## 5. Meta-level: this audit as new bias

### 5.1 audit_2026-04-17 becomes the next stale canon

**Description.** Three months from now agent reads `audit_2026-04-17_agent_context_dysfunction.md` as authoritative ("the bundle was deployed, T2 is in place, therefore VERIFY is enforced, therefore evidence is sufficient") without re-verifying any of it. The exact pattern the audit was meant to cure.

**Trigger.** `preserve: true` frontmatter + no supersession entry ever added + time passing.

**Observable symptom.** Future handovers cite audit_2026-04-17 to justify decisions without running `check_hook_health.py` or `/memory` to verify the hooks mentioned in the audit still work.

**Mitigation — explicit and critical.** Add to the audit file itself a closing section:

> **Self-audit clause.** This audit's recommendations expire 2026-07-17 (90 days). Before citing it as authority after that date, run `~/.claude/scripts/check_hook_health.py`, `check_rules_loaded.py`, and verify the T1 fixes are still in `backend/main.py`, `backend/snapshot_cron.py`, and `backend/etoro_sync.py` via git log. If any fails, re-tag as `[UNDER REVIEW]`.

This makes the audit self-superseding. Fixes the contradiction in the user's own brief (preserve: true vs T2.3 supersession principle): preservation is of the **document**, not of the **trust level** of the document.

### 5.2 Agent treats the entire T1+T2+T3 bundle as "done" even if parts get reverted

**Description.** T1.3 fixes the dead broker-snapshot write path. Two months later, a refactor accidentally breaks it again (someone reverts the `total_value<=0` guard reasoning to "cleanup"). Agent reads handover history, sees "T1 complete", assumes broker-snapshot writes work, spends 10 sessions on next-level bugs that are actually phantom symptoms of the regression.

**Mitigation.** Supplement KPIs in the audit with a **permanent CI check**: the forensic endpoint (T3.2) queries for `portfolio_snapshots WHERE source='broker' AND created_at > NOW() - 7 DAY` and fails if count is 0. Runs nightly. If T1.3 regresses, monitoring screams. Not a prose assertion — a live invariant.

---

## 6. Business logic worst-cases

### 6.1 Silent regression of runtime fixes via deploy accident

**Description.** New engineer (or future Dmitry) runs `git revert` on "too many recent commits" on Droplet during incident. T1 fixes disappear. No one notices for 2 weeks because smoke test ran once and passed by coincidence.

**Mitigation.** `backend/main.py` should include a startup log line: `"T1 readiness gate: blocking startup_reconcile enabled (STARTUP_RECONCILE_REQUIRED=1)"`. And `backend/snapshot_cron.py` should log `"T1.3 broker-snapshot write path: positions-mode=real"`. If these log lines go missing from production logs, Grafana alert. Self-documenting deployment state. Zero runtime cost.

### 6.2 Cross-project contamination — T2 rules break CRM_AI / AINEWS / yfinance

**Concrete examples:**
- **yfinance** is a pure Python library project — no `/healthz` endpoint. VERIFY gate fires on every handover; agent N/A's every handover; normalizes escape hatch for that project and drifts back into Claude_Booster/horizon.
- **AINEWS** has ongoing "is_international" and "CF Worker" work where handover topics span many file areas. RECON gate (file-overlap flavor) fires too aggressively if tuning is wrong, triggers rework loops.
- **CRM_AI** Hypothesis Contract gets filled by formulaic boilerplate (see §1.4). Since CRM_AI rarely has cross-session continuation (feature work, not investigation), the hypothesis field is noise.

**Mitigation.** Per-project opt-in/out flags in `.claude/CLAUDE.md`:
```yaml
verify_gate: off  # for yfinance (no prod)
hypothesis_contract: off  # for CRM_AI (feature work, no long investigation)
recon_gate: enforcing  # for horizon + AINEWS (investigation-heavy)
```
Defaults calibrated per project archetype. Prevents one-size-fits-all T2 from poisoning projects with different shapes.

### 6.3 Business impact of blocking startup — cost quantification

**Description.** 30s added to deploy in market hours. If deploy cadence is ~5/week and 40% happen in market hours, that's 2 deploys × 30s × 50 weeks = ~50 minutes of annual downtime from readiness gating alone. Additional 3 minutes per crash-loop event. If 2 crash-loop events/year → +6min. Total ~1h/year.

**Cost.** For solo-operator product with tiny user base, ~$0 direct. But each downtime event risks a support ticket, Dmitry context-switching. Indirect cost: 1-2 hours/year of attention.

**Mitigation (already implied).** Deploy in off-hours (after 20:00 ET or weekends) becomes default. Document in `horizon/DEPLOY_RUNBOOK.md` — never deploy T1-path changes between 09:00-16:00 ET.

---

## Top 10 ranked risks

| # | Risk | Likelihood | Impact | Mitigation exists in audit? | Priority |
|---|---|---|---|---|---|
| 1 | VERIFY gate N/A escape hatch overused → gate becomes theater | H | H | No | **P0** |
| 2 | audit_2026-04-17 becomes next stale canon (meta-loop) | H | H | No (preserve:true pins it) | **P0** |
| 3 | Supersession tag metastasis without resolution dates | H | M | Partial (tag defined, no expiry) | **P0** |
| 4 | T1.1 crash-loop during market hours → forced rollback | M | H | Partial (env-var exists, default unclear) | **P1** |
| 5 | Hypothesis Contract becomes boilerplate (no diff check) | H | M | No | **P1** |
| 6 | Cross-project contamination of T2 rules | M | M | No | **P1** |
| 7 | Claude Code hook format breaking on upgrade | L | H | No | **P2** |
| 8 | RECON gate defeated by topic renaming | M | M | No | **P2** |
| 9 | Dmitry vacation during T1 rollback scenario | M | M | No | **P2** |
| 10 | SERIALIZABLE conflicts at eToro scale-up | L | M | Partial (retry mentioned) | **P3** |

---

## 5 pre-mortem "near-term" actions

To ship alongside T1/T2/T3 (not as separate tranche, but as companion artifacts). Total effort ~4h. Addresses 8 of top 10 risks.

1. **Narrow the VERIFY escape hatch (~30 min).** In `~/.claude/scripts/verify_gate.py`: reject `N/A` when session edited files matching `{backend,frontend,deploy,sql}/**/*.{py,ts,tsx,sql}`. N/A requires one-line justification naming edited paths. **Addresses risks 1, 6.**

2. **Add self-supersession clause to audit_2026-04-17 (~15 min).** Append explicit "expires 2026-07-17, re-verify via X/Y/Z commands, re-tag if regressed". Fixes the `preserve: true` ↔ T2.3 contradiction. **Addresses risks 2, 10 (indirect).**

3. **Enforce resolution dates on `[UNDER REVIEW]` tags (~1h).** New script `check_review_ages.py` runs on `/start`, surfaces any `[UNDER REVIEW since X]` older than 30 days without resolution. Tag format enforced: `[UNDER REVIEW since audit_YYYY-MM-DD — "reason"; resolve by YYYY-MM-DD]`. **Addresses risks 3, 9.**

4. **Hypothesis-diff check (~1h).** `~/.claude/scripts/hypothesis_diff.py` compares today's 4-line theory to last 3 handovers on same file-graph cluster. If unchanged for 3 sessions, warn: "theory stuck — force retraction-or-confirmation". **Addresses risks 5, 8.**

5. **Hook health canary + deployment-state log lines (~1.5h).** (a) `~/.claude/rules/_canary.md` with known token + `check_rules_loaded.py` on `/start`. (b) `backend/main.py` logs T1 readiness flag at startup; `backend/snapshot_cron.py` logs T1.3 positions-mode. Grafana alert if missing. **Addresses risks 4, 7.**

Single-operator note: each of these is <2h, independently valuable, independently revertable. Ship them in the *same commit window* as T2 rule additions so they can't be forgotten or deferred into a separate "week 3" that never arrives.

---

## Closing framing

The audit_2026-04-17 bundle diagnoses the past correctly. Its failure mode will not be "the fix was wrong" — it will be **"the fix was treated as complete, and the enforcement mechanisms decayed into theater within 60 days."** Every forcing function T2 introduces is a regex or a hook, and every regex can be satisfied by a minimal-effort string. The meta-lesson from horizon's 39-day loop is that **rules without active measurement of their effect become part of the context-poisoning they were designed to cure**. Ship the audit bundle with the 5 companion artifacts above, because without them the pre-mortem from 2026-10-01 writes itself: "We enforced evidence, agent complied with the letter, belief state never updated, and the bundle's preserve:true frontmatter immunized it from the same supersession discipline it imposed on older rules."
