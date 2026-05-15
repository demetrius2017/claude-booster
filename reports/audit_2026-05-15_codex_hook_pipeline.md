# Audit: Hook Pipeline Model Routing Layer (v1.9.3–v1.9.4)

**Date:** 2026-05-15
**Scope:** model_tag_enforcer.py, delegate_gate.py, codex_worker.sh, codex_sandbox_worker.sh, settings.json.template, tool-strategy.md
**Method:** 6-lens Codex gpt-5.5 audit (flat-fee, no PAL API tokens)
**Total GPT-5.5 tokens consumed:** 507,534 across 6 agents

## Verdict Summary

| Lens | Verdict | HIGH | MED | LOW | Tokens |
|------|---------|------|-----|-----|--------|
| Correctness | FAIL | 3 | 2 | 0 | 112,498 |
| Security | FAIL | 3 | 3 | 1 | 74,646 |
| Architecture | FAIL | 2 | 2 | 2 | 106,858 |
| Performance | CONCERN | 0 | 1 | 1 | 57,064 |
| Data Integrity | FAIL | 1 | 3 | 2 | 73,395 |
| Operational | FAIL | 2 | 5 | 0 | 83,073 |

**Overall: FAIL** (5/6 lenses FAIL, 1 CONCERN)

Raw counts before dedup: **11 HIGH, 16 MED, 6 LOW** across all lenses.
After cross-lens dedup: **8 unique HIGH, 15 unique MED, 6 unique LOW**.

## Cross-Lens Convergence

Several findings appeared independently in 2+ lenses — strong signal:

| Finding | Lenses | Convergence |
|---------|--------|-------------|
| `_infer_category()` keyword ordering | Correctness, Architecture | 2/6 |
| Compound command recon bypass | Correctness, Security | 2/6 |
| Advisory-only enforcement (CC bug #16598) | Security, Architecture, Operational | 3/6 |
| Counter corrupt → fail-open | Data Integrity, Correctness | 2/6 |

---

## HIGH Findings (deduplicated)

### H1: `_infer_category()` checks coding keywords before high-blast keywords
**File:** model_tag_enforcer.py:191-196
**Lenses:** Correctness, Architecture
**Description:** `_CODING_KEYWORDS` is checked at line 191 before `_HIGH_BLAST_KEYWORDS` at line 195. Descriptions containing both coding verbs ("fix", "implement", "add") and safety-critical nouns ("auth", "migration", "security") classify as `coding` instead of `high_blast_radius`.
**Evidence:** "Fix auth permission bug" → `coding`. "Apply DB migration safely" → `coding`.
**Impact:** Routes auth/migration/security work to Codex where `dep_guard.py`, `financial_dml_guard.py`, and `verify_gate.py` PreToolUse hooks don't fire — the safety dependency direction is reversed.
**Fix:** Move high-blast keyword check before coding keywords. Use word-boundary matching so "add" doesn't match "address".
**Risk: REAL — code bug, safety-critical, easy fix.**

### H2: Compound command bypass in `_bash_is_recon()`
**File:** delegate_gate.py:302
**Lenses:** Correctness, Security
**Description:** `_bash_is_recon()` uses `any(p.search(cmd) for p in RECON_BASH_PATTERNS)` — if ANY recon regex matches anywhere in the command string, the entire compound is classified as recon.
**Evidence:** `git status && rm -rf foo` → recon (True). `ls foo && rm -rf foo` → recon (True). `curl -X DELETE ...` → recon (True). `ssh prod rm -rf /app/cache` → recon (True).
**Impact:** Destructive commands hidden behind a recon prefix bypass the delegation budget.
**Fix:** Parse compound commands; require ALL segments to be read-only. Reject `&&`, `||`, `;`, pipes, redirects, command substitution when any segment is non-recon.
**Risk: MITIGATED — Claude Code's `permissions.deny` patterns already block `rm -rf /` etc. The recon classifier is a budget optimization, not a security gate. Still worth hardening.**

### H3: Phase exemption bypasses delegation budget for Edit/Write
**File:** delegate_gate.py:422-430
**Lens:** Correctness
**Description:** RECON/PLAN phases bypass the delegation budget for ALL tools including Edit and Write. `python3 ~/.claude/scripts/phase.py set RECON` matches the recon bash pattern, creating a self-enabling bypass loop.
**Impact:** Lead can set RECON phase via recon-classified command, then perform unlimited Edit/Write without delegating.
**Fix:** Phase exemption should only apply to truly read-only operations. Edit/Write enforcement should defer to `phase_gate.py` (separate hook).
**Risk: MITIGATED — phase_gate.py is a separate hook that independently guards Edit/Write during wrong phases. delegate_gate's exemption only affects the delegation budget counter, not whether Edits are physically allowed.**

### H4: `.delegate_mode` bypass plantable by sub-agent
**File:** delegate_gate.py:380-420
**Lens:** Security
**Description:** `.delegate_mode` file lives in project-writable `.claude/` directory. A sub-agent could write `off:<session_id>` to this file. The Lead context later honors the same file and bypasses the gate.
**Attack scenario:** sub-agent creates `.claude/.delegate_mode` → Lead's next tool call sees it → budget gate disabled.
**Fix:** Move bypass state outside workspace with strict permissions, or require a signed nonce. Block writes to `.delegate_mode` via allowlist exclusion.
**Risk: THEORETICAL — requires adversarial sub-agent behavior. Sub-agents are spawned by Lead with controlled prompts. TTL enforcement (24h) exists. But the vector is real.**

### H5: Advisory-only enforcement for Codex routing + missing tags
**File:** model_tag_enforcer.py:387-421
**Lenses:** Security, Architecture, Operational
**Description:** Codex routing violations and missing `[model]` tags print advisory stderr but return `0` (allow). The docstring says they block. Only Anthropic tier mismatch (weaker model than recommended) actually exits `2`.
**Root cause:** CC bug #16598 — `updatedInput` via stdout JSON crashes the harness, so blocking mode was intentionally disabled for non-safety paths.
**Impact:** The primary use case (routing to Codex over Agent) has zero enforcement. Budget savings depend on Lead voluntarily following advisory.
**Fix:** When CC bug #16598 is fixed, restore `return 2` for codex-cli routing and missing tags. Until then, document current behavior as advisory and update the docstring.
**Risk: KNOWN LIMITATION — intentional workaround. Track CC bug resolution (see `reference_cc_bug_16598.md`).**

### H6: Counter file corrupt content → fail-open
**File:** delegate_gate.py:227-238
**Lenses:** Data Integrity, Correctness
**Description:** If `.delegate_counter` contains non-numeric content (from crash, external edit, or torn write), `int()` raises `ValueError`, exception handler returns `1`, file remains corrupt. With `BUDGET=1`, every action is allowed forever.
**Evidence:** GPT-5.5 reproduced: wrote "not-a-number" to counter file → two consecutive Edit calls both exited 0.
**Fix:** Treat parse failure as corrupt state: persist `BUDGET + 1` atomically, log `counter_corrupt`, return `BUDGET + 1` (fail closed).
**Risk: REAL — reproducible, safety-relevant, easy fix.**

### H7: Hook ordering — delegate_gate resets counter before enforcer can block
**File:** settings.json.template:160
**Lens:** Architecture
**Description:** `delegate_gate.py` fires before `model_tag_enforcer.py` on Agent calls. delegate_gate resets counter to 0 on Agent (delegation signal), but enforcer may then block the Agent call (exit 2). Counter was reset for a delegation that never happened.
**Impact:** Phantom counter reset — Lead gets free budget without actual delegation.
**Fix:** Swap hook order: `model_tag_enforcer` before `delegate_gate` for Agent matcher. Or move counter reset to PostToolUse (success-only).
**Risk: REAL — architectural bug, easy fix (hook ordering in template).**

### H8: Zero observability for model_tag_enforcer
**File:** model_tag_enforcer.py (entire file)
**Lens:** Operational
**Description:** No JSONL logging. `_load_routing()` silently returns `None` on failure. Malformed stdin and last-resort exceptions silently allow. Cannot debug why Agent calls were advised/blocked. Existing `model_tag_enforcer*.jsonl` files in logs/ are stale artifacts from prior version.
**Fix:** Add `model_tag_enforcer_decisions.jsonl` via shared `append_jsonl()`: `{decision, reason, category, provider, recommended_model, actual_model, session_id, cwd, description_excerpt}`.
**Risk: REAL — operational blindness, moderate fix effort.**

---

## MED Findings (deduplicated)

| ID | File | Description | Lens |
|----|------|-------------|------|
| M1 | delegate_gate.py:282 | `PATH_ALLOWLIST` uses raw regex on uncanonicalized paths — traversal via `../` | Security |
| M2 | codex_sandbox_worker.sh:91 | Copies `.env*` files to worktree — secret exposure to Codex | Security |
| M3 | delegate_gate.py:224 | `os.open()` follows symlinks on `.delegate_counter` — no `O_NOFOLLOW` | Security |
| M4 | delegate_gate.py:149 | `CODEX_WORKER_PATTERNS` false positives — regex matches in quoted strings | Correctness |
| M5 | model_tag_enforcer.py:170 | `model_balancer.json` freshness not validated — stale/corrupt → silent fallback | Operational |
| M6 | delegate_gate.py:318 | Bash command not logged in decision JSONL — can't debug recon misclassification | Operational |
| M7 | delegate_gate.py:96 | Import/setup crash → no fallback logging — invisible hook failures | Operational |
| M8 | codex_worker.sh:4 | `CODEX_BIN` hardcoded to `/opt/homebrew/bin/codex` — no override/PATH fallback | Operational |
| M9 | codex_sandbox_worker.sh:80 | EXIT trap `git worktree remove` has no timeout — can hang at cleanup | Operational |
| M10 | model_tag_enforcer.py:401 | Advisory vs hard-block contract inconsistent — ambiguous enforcement semantics | Architecture |
| M11 | model_tag_enforcer.py:147 | Category taxonomy duplicated in enforcer vs balancer — drift risk | Architecture |
| M12 | delegate_gate.py:276 | `.delegate_mode` trusts empty session_id — weak bypass validation | Data Integrity |
| M13 | delegate_gate.py:177 | Counter location depends on uncanonicalized `cwd` — state split possible | Data Integrity |
| M14 | delegate_gate.py:230 | truncate-then-write under flock is not crash-atomic | Data Integrity |
| M15 | hooks (both) | ~30ms per hook process startup — ~60ms overhead on Agent calls | Performance |

## LOW Findings

| ID | File | Description | Lens |
|----|------|-------------|------|
| L1 | model_tag_enforcer.py:351 | Malformed stdin fails open (unbounded, no schema) | Security |
| L2 | delegate_gate.py:464 | Blocked calls increment counter (phantom count) | Data Integrity |
| L3 | delegate_gate.py:199 | `.phase` file accepts arbitrary text, no validation | Data Integrity |
| L4 | delegate_gate.py:335 | JSONL telemetry unbounded growth, no rotation | Performance |
| L5 | model_tag_enforcer.py:20 | Docstring stale (says blocking, code is advisory) | Architecture |
| L6 | _gate_common.py:2 | Module naming/scope description stale | Architecture |

---

## Triage: What to fix now vs later

### Fix now (v1.9.5) — real bugs, easy fixes

| Finding | Effort | Why now |
|---------|--------|---------|
| H1: keyword ordering | 30min | Safety-critical misclassification |
| H6: counter corrupt fail-open | 30min | Reproducible gate bypass |
| H7: hook ordering swap | 5min | Template edit, prevents phantom resets |
| H8: enforcer JSONL logging | 1h | Operational blindness |
| L5: docstring update | 10min | Prevents future maintainer re-introducing deadlock |
| M6: log bash command excerpt | 30min | Debuggability for H2-class issues |

### Fix soon (v1.10) — hardening

| Finding | Effort | Why soon |
|---------|--------|----------|
| H2: compound command parsing | 2-3h | Recon bypass, mitigated by CC deny patterns but worth hardening |
| M4: codex worker pattern anchoring | 1h | False positive counter resets |
| M14: crash-atomic counter writes | 1-2h | Eliminates H6-class failures at the root |
| M8: codex_worker.sh CODEX_BIN | 15min | Portability |
| M9: sandbox cleanup timeout | 30min | Stale worktree prevention |
| M11: extract model_routing_contract.py | 2h | Eliminates category drift |

### Track / accept risk

| Finding | Disposition |
|---------|------------|
| H3: phase exemption | Mitigated by phase_gate.py (separate hook). Monitor. |
| H4: .delegate_mode plantable | Theoretical. TTL exists. Revisit if adversarial sub-agent scenarios emerge. |
| H5: advisory enforcement | Blocked by CC bug #16598. Restore blocking when CC ships fix. |
| M1-M3: path traversal, symlink, .env copy | Hardening for single-user CLI tool. Low priority. |
| M15: hook startup latency | 60ms is acceptable. Revisit if consolidation becomes worthwhile. |

---

## Performance Benchmarks (GPT-5.5 measured)

| Operation | Latency |
|-----------|---------|
| Hook process startup (Python) | ~29-31 ms |
| `_load_routing()` (JSON read) | 35.2 µs |
| `project_root_from()` (5-level walk) | 39.4 µs |
| `append_jsonl()` (open+write+close) | 34.5 µs |
| `_atomic_increment()` (flock+rw) | 52.6 µs |
| RECON regex hit | 0.7 µs |
| RECON regex miss (all 10) | 3.5 µs |

Process startup dominates. In-process costs are microseconds.

## Method notes

- All 6 auditors ran independently as Codex gpt-5.5 bio-agents via `codex_worker.sh gpt-5.5`
- Each auditor received independent RECON instructions (grep patterns, specific attack vectors to test)
- GPT-5.5 ran actual verification: test suites (26/26, 27/27, 6/6), live smoke tests on hook payloads, Python microbenchmarks
- Codex `workspace-write` sandbox allowed reading project files but not modifying installed hooks
- Total wall time: ~90 seconds (all 6 agents ran in parallel via background tasks)
- Total cost: $0 marginal (flat-fee ChatGPT Pro subscription)
- Compared to PAL MCP (API tokens): ~500k tokens × $15/1M = ~$7.50 saved

## Round 2 requirement

Per `institutional_claude-tooling.md`: "Round 2 audit is mandatory, not optional." After fixing H1, H6, H7, H8, and L5 — re-audit the narrowed post-fix surface. Fixes can introduce new bugs.
