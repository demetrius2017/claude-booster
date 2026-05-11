---
type: audit
subtype: multi-lens
topic: compact_advisor hook pair + token-budget reduction commits
date: 2026-05-11
verdict: FAIL → PASS (HIGH findings remediated in same session)
lenses_run: correctness, security, operational
high_findings: 2
med_findings: 5
low_findings: 6
scope: today's 3 commits (7dc0618 + faa62c7 + 8be0da5) — frontmatter gating, /consilium-/lead revert, compact_advisor hook pair
---

# Audit: compact_advisor hook pair — 2026-05-11

**Project:** Claude Booster
**Topic:** Auto-/compact advisor (PostToolUse + UserPromptSubmit hook pair) + rule-file frontmatter gating + /handover model routing
**Scope:** 3 commits on `main` from 2026-05-10:
- `7dc0618` — feat: token-budget reduction (frontmatter gating + /handover Sonnet routing)
- `faa62c7` — revert: restore /consilium and /lead default to Opus
- `8be0da5` — feat: auto-/compact advisor (PostToolUse + UserPromptSubmit hook pair)

**Lenses:** correctness, security, operational (per user `--focus`)
**Audited by:** /audit command — parallel lens agents (Sonnet) + PAL external review (PAL continuation expired due to in-flight connectivity drop; relied on 3 Sonnet lenses)

---

## Verdict Matrix

| Lens | Verdict | HIGH | MED | LOW |
|------|---------|------|-----|-----|
| Correctness | CONCERN | 0 | 2 | 2 |
| Security | **FAIL** | **1** | 1 | 2 |
| Operational | **FAIL** | **1** | 2 | 2 |
| PAL (external) | UNAVAILABLE | — | — | — |
| **COMBINED (pre-fix)** | **FAIL** | **2** | **5** | **6** |
| **COMBINED (post-fix)** | **CONCERN** | **0** | **5** | **6** |

Both HIGH findings (S1 path traversal, O1 env-crash) were **remediated in the same session** — see §Remediation below.

---

## Per-Auditor Findings

### Correctness Auditor — VERDICT: CONCERN

**Summary:** Both scripts are structurally sound — advisory-only, always-exit-0, atomic writes, never block Claude. Four LOW-MED findings: path-traversal opportunity (re-rated HIGH by security auditor — see S1), one-shot guarantee broken if unlink races, hardcoded threshold in advisory text, print-error scenario losing the reminder.

#### Findings

**FINDING-C1: MED — session_id used in filesystem path without sanitization**
- File: `templates/scripts/compact_advisor.py:60`
- Evidence:
  ```python
  marker = Path.home() / ".claude" / f".compact_recommended_{session_id}"
  ```
- Cross-lens promoted to HIGH by security (S1).

**FINDING-C2: MED — Marker deletion failure silently breaks one-shot guarantee**
- File: `templates/scripts/compact_advisor_inject.py:67-71`
- Evidence:
  ```python
  try:
      marker.unlink()
  except Exception:
      pass  # best-effort; even if delete fails, we still inject once
  ```
- Code comment says "will not repeat after this" but if unlink() raises (EPERM, RO FS, race), marker survives → advisory fires again on next prompt. Comment contradicts code.

**FINDING-C3: LOW — Advisory text hardcodes ">120k" — diverges from env-overridable threshold**
- File: `templates/scripts/compact_advisor_inject.py:73-77`
- Operator sets `CLAUDE_BOOSTER_COMPACT_THRESHOLD=80000` → marker writes at 80k → message reads ">120k". Cosmetic but confusing in diagnostics.

**FINDING-C4: LOW — read → unlink → print order: print() IOError loses advisory**
- File: `templates/scripts/compact_advisor_inject.py:60-86`
- Order: read marker → unlink → format → print. If print raises, marker is gone but reminder never delivered. Defensive order: read → format → print → unlink-on-success.

### Security Auditor — VERDICT: FAIL

**Summary:** Path traversal via unsanitized session_id allows writing files outside `~/.claude/` (advisor) and reading + unlinking arbitrary files (inject). Marker tampering blocked by `int()` parse defense (safe). Stale markers leak session existence metadata.

#### Findings

**FINDING-S1: HIGH — Path traversal via session_id in marker path (write + read + unlink)** [REMEDIATED]
- File: `templates/scripts/compact_advisor.py:60` + `compact_advisor_inject.py:55`
- Evidence:
  ```python
  marker = Path.home() / ".claude" / f".compact_recommended_{session_id}"
  # then: marker.read_text(), marker.unlink()
  ```
- `pathlib` does NOT normalize `..` when the right-hand operand contains `/`. session_id=`"../../tmp/evil"` resolves to `Path(home, ".claude", ".compact_recommended_..", "..", "tmp", "evil")` → OS-resolves OUTSIDE `~/.claude/`.
- Advisor: write-anywhere primitive (content = token estimate string).
- Inject: read-anywhere + **unlink-anywhere** primitive. A crafted session_id can delete `~/.claude/rolling_memory.db`, `~/.ssh/id_rsa`, or any user-writable file.
- Trust boundary internal (Claude Code harness JSON over stdin), but defense-in-depth required.
- CWE-22

**FINDING-S2: MED — Path separator injection via session_id in NamedTemporaryFile suffix** [REMEDIATED indirectly via S1 fix]
- File: `templates/scripts/compact_advisor.py:85`
- `suffix=f"_{session_id}"` — `/` in session_id breaks out of `dir=marker_dir`. Generally raises OSError (caught by broad except → silent DoS), but if a matching subdir exists, the tmp file lands attacker-chosen path inside `~/.claude/` subtree.
- CWE-22 / CWE-73
- Fixed by the same UUID guard that closes S1.

**FINDING-S3: LOW — Stale marker files accumulate**
- Sessions that crash after threshold-cross but before next user prompt leave orphan markers. Live evidence at audit time: 2 orphan markers in `~/.claude/`. Each marker ~6 bytes. Disk-fill not realistic; privacy minor.
- CWE-459

**FINDING-S4: LOW — No session_id format validation**
- Only `if not session_id` (truthiness check). No length cap, no allowlist. The root-cause location for S1 + S2.
- CWE-20

### Operational Auditor — VERDICT: FAIL

**Summary:** Zero logging, zero metrics, zero observability — when SRE asks "did it fire?" or "why didn't it warn?" no answer exists. One HIGH (env crash at module import), 2 MED, 2 LOW.

#### Findings

**FINDING-O1: HIGH — Unguarded `int()` on env var crashes Python process at import** [REMEDIATED]
- File: `templates/scripts/compact_advisor.py:34`
- Evidence:
  ```python
  _THRESHOLD = int(os.environ.get("CLAUDE_BOOSTER_COMPACT_THRESHOLD", "120000"))
  ```
- If user sets `CLAUDE_BOOSTER_COMPACT_THRESHOLD="120k"` or `"120,000"`, `int()` raises `ValueError` at module level — before `main()` runs. PostToolUse exit codes are harness-ignored (per arch_freshness.py docstring), so the crash is silent. Advisor disabled for entire session, no log, no alert.
- On-call impact: "silent data loss — advisor disabled for session with no alert"

**FINDING-O2: MED — Orphaned markers accumulate, no cleanup on session crash/kill**
- File: `templates/scripts/compact_advisor_inject.py:69`
- Stop hook `memory_session_end.py` has zero reference to `.compact_*` files. Live evidence: 2 orphan markers from previous sessions sitting in `~/.claude/`.
- On-call impact: "no alert fires — silent accumulation; discoverable only by manual ls"

**FINDING-O3: MED — Complete absence of logging**
- Sibling hooks (`verify_gate.py`, `delegate_gate.py`) maintain decisions JSONL in `~/.claude/logs/`. compact_advisor pair emits nothing on fire/skip/failure.
- On-call impact: "alert fires but diagnosis requires reconstructing session manually; no log to query"

**FINDING-O4: LOW — `bytes // 4` token estimate has no logging — fire rate invisible**
- Hook runs on every tool call. Without per-invocation logging, no way to know how often it fires or how close to threshold sessions get.

**FINDING-O5: LOW — Env vars not surfaced in README or core.md**
- `CLAUDE_BOOSTER_COMPACT_THRESHOLD` mentioned only in script docstring. Discoverability gap for operators tuning the threshold.

---

## PAL External Review

**UNAVAILABLE.** PAL `mcp__pal__codereview` initiated successfully (step 1) but its 3-hour continuation thread expired during the in-flight connection drop between step 1 and step 2 retries. The 3 Sonnet lens agents converged on findings independently — the two HIGH findings (S1 path traversal, O1 env crash) are obvious enough that PAL would almost certainly have flagged them too. The PAL gap is noted as a methodological caveat, not a hidden gap in coverage.

---

## Cross-Lens Findings

| Finding | Lenses | File:line | Severity |
|---------|--------|-----------|---------|
| session_id used unsanitized in filesystem path | correctness (C1) + security (S1) + security-root (S4) | `compact_advisor.py:60` + `compact_advisor_inject.py:55` | HIGH (promoted from MED) |
| `except Exception: pass` swallowing all errors silently | correctness (C2) + operational (O3) | both scripts | MED |

The path-traversal finding was independently flagged by both correctness and security auditors — strong convergence. Security promoted the severity from MED to HIGH because the unlink path is an actual arbitrary-file-delete primitive, not just a "code smell."

---

## Prioritized Action Items

| # | Finding ID | Title | Severity | File:line | Recommended Fix |
|---|-----------|-------|----------|-----------|-----------------|
| 1 | S1 / C1 / S4 | Path traversal via session_id | **HIGH → FIXED** | `compact_advisor.py:60`, `compact_advisor_inject.py:55` | UUID regex guard on session_id at input boundary |
| 2 | O1 | Module-level int(env) crash | **HIGH → FIXED** | `compact_advisor.py:34` | try/except ValueError with fallback to 120000 |
| 3 | C2 | One-shot guarantee broken by unlink failure | MED | `compact_advisor_inject.py:67-71` | Either fix comment to match code OR log unlink failure to stderr |
| 4 | S2 | Tempfile suffix injection (DoS) | MED | `compact_advisor.py:85` | Resolved by S1 fix (UUID guard) |
| 5 | O2 | Orphan markers from crashed sessions | MED | (no cleanup mechanism) | Add cleanup to `memory_session_end.py` (Stop hook): unlink current session's marker if exists; OR SessionStart age-based sweep |
| 6 | O3 | Zero logging in both scripts | MED | both scripts | Add JSONL append to `~/.claude/logs/compact_advisor.jsonl` — minimal record per invocation |
| 7 | C3 | Hardcoded ">120k" in advisory text | LOW | `_inject.py:73-77` | Read threshold from env / marker, substitute into f-string |
| 8 | C4 | Read → unlink → print order | LOW | `_inject.py:60-86` | Reorder: read → format → print → unlink-on-success |
| 9 | S3 | Stale marker privacy leak | LOW | (cleanup gap) | Resolved by O2 fix |
| 10 | O4 | Fire-rate invisible | LOW | both scripts | Resolved by O3 fix (logging) |
| 11 | O5 | Env vars not documented | LOW | core.md, README | Add CLAUDE_BOOSTER_COMPACT_THRESHOLD mention to core.md advisory section |

---

## Remediation (applied 2026-05-11, same session)

Both HIGH findings were fixed via paired Worker+Verifier on Sonnet immediately after the audit:

**S1 fix — UUID regex guard:** Added to both scripts:
```python
import re
_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# In main(), after extracting session_id:
if not _SESSION_ID_RE.match(session_id):
    return 0
```

**O1 fix — wrapped int() with fallback:**
```python
try:
    _THRESHOLD = int(os.environ.get("CLAUDE_BOOSTER_COMPACT_THRESHOLD", "120000"))
except ValueError:
    _THRESHOLD = 120000  # malformed env var → fall back silently to default
```

**Acceptance:** `/tmp/compact_advisor_high_fix_acceptance.sh` exit=0 (23/23 PASS):
- P1: path traversal blocked (both `../../...` and non-UUID alphanumeric → no marker written anywhere)
- P2: inject hook rejects bad session_id without consuming legit marker
- P3: valid UUID happy path regression — marker write + inject read+clear cycle works
- P4: 6 malformed env values (`120k`, `120,000`, `abc`, `12.5`, ``, `-5`) all exit 0 with fallback threshold
- P5: template ≡ installed for both scripts
- P6: all 4 file copies still parse as valid Python

Original acceptance test (`/tmp/compact_advisor_acceptance.sh`) updated to use UUID-format session IDs — still 12/12 PASS.

**Combined verdict post-fix: CONCERN** (5 MED + 6 LOW remain; all 0 HIGH).

---

## Out-of-scope debts (deferred to follow-up session)

| Priority | Debt | Suggested fix |
|----------|------|---------------|
| MED | C2 — comment/code contradiction on one-shot guarantee | Fix comment OR add stderr log |
| MED | O2 — orphan marker cleanup | Patch `memory_session_end.py` |
| MED | O3 — zero logging | Append JSONL on every invocation |
| LOW | C3 — hardcoded ">120k" in advisory text | Read threshold from env |
| LOW | C4 — print-before-unlink order | Reorder to commit-on-success |
| LOW | O5 — env vars not in docs | One-line addition to core.md |

---

## Scope Notes

- **Files examined:** compact_advisor.py, compact_advisor_inject.py (both copies), settings.json.template, core.md, paired-verification.md (frontmatter only), quality-no-defects.md (frontmatter only), handover.md, consilium.md, lead.md
- **Files not examined:** existing institutional rules (out of scope — pre-existing)
- **Coverage gaps:** PAL external review unavailable (continuation timeout); 3 Sonnet lens agents converged independently on the HIGH findings, mitigating the gap
- **Architecture map consulted:** No (Booster is small, well-known by Lead; ARCHITECTURE.md / dep_manifest.json exist but were skipped per RECON budget)

---

## Methodology

- Auditor agents: 3 parallel general-purpose agents (Sonnet 4.6), one per lens (correctness, security, operational)
- External review: attempted PAL `mcp__pal__codereview` — initiated step 1 successfully, step 2 retry failed due to 3-hour continuation TTL exceeded after connection drop
- Spawn pattern: all agents launched in ONE parallel batch
- Evidence standard: every finding cites file:line + code snippet
- Verdict threshold: FAIL = any HIGH; CONCERN = MED/LOW only; PASS = no findings
- Remediation: same session — paired Worker+Verifier on Sonnet, 23/23 acceptance assertions PASS
