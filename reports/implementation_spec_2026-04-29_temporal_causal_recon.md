---
name: "Implementation Spec 2026-04-29 — Temporal-causal recon + stuck-loop detector v1"
description: >
  Formal implementation spec for the consilium 2026-04-29 decision. Translates
  the 5-agent consensus into file-by-file, function-by-function deliverables
  with acceptance criteria, smoke tests, and rollback. Carries across
  sessions — any agent picking this up should be able to ship without
  re-running the consilium.
type: implementation_spec
scope: global
preserve: true
parent_consilium: reports/consilium_2026-04-29_temporal_causal_recon_pivot.md
---

# Implementation Spec — Temporal-causal recon + stuck-loop detector v1

> **Parent decision:** `reports/consilium_2026-04-29_temporal_causal_recon_pivot.md`.
> All design tradeoffs and rejected alternatives live there. This document is
> implementation-only; do not re-litigate design decisions here.

## 1. Goal

Two complementary mechanisms, shipped together this week:

- **M1 — Stuck-loop detector** with a label-drift-resilient hash. Surfaces
  `STUCK LOOP CANDIDATE` advisory at /start when the same problem-hash
  recurs ≥3× across last 5 handovers without a `verify_gate=pass` resolution.
- **M2 — Premise/Tried/Result/Open timeline block** rendered in `/start`
  before the existing flat consilium/audit list. Replaces "list of titles"
  with a causal narrative for the current topic.

**Non-goals (explicitly deferred to v2):** schema migration, LLM-extracted
problem_hash, hard /start block, PreToolUse gate on EnterPlanMode.

## 2. File-level deliverables

### D1 — `~/.claude/scripts/stuck_loop_key.py` (NEW, ≤80 LOC, stdlib)

Pure deterministic function. No I/O, no side effects, no external deps.

**Contract:**
```python
def make_stuck_loop_key(
    text: str,
    context_anchors: tuple[str, ...] = (),
) -> dict:
    """Return {'hash': str, 'tokens': list[str], 'canonical': str}.

    Algorithm:
      1. lowercase, unicodedata.normalize('NFKC')
      2. strip markdown fences (```...``` blocks), inline code (`...`)
      3. strip URLs, ISO timestamps, UUIDs, git SHAs (40-hex), long ints
      4. tokenize against r'[a-z][a-z0-9_./:-]{2,}'
      5. drop STOPWORDS (constant module-level set, ~40 words)
      6. unique sorted; cap at 32 tokens
      7. prepend context_anchors (sorted, deduped) into canonical material
      8. sha1 of '\\n'.join(canonical_tokens)
      9. return {'hash': sha1[:16], 'tokens': tokens, 'canonical': '\\n'.join(...)}
    """
```

**STOPWORDS set (do not blindly expand — every addition is a recall risk):**
```
the and or but if then else when while for from into onto upon over under
this that these those with without about which what whose where why how
have has had been being should could would shall will must may might can
need needs needed try tries tried trying issue problem fix fixing fixed
working work works result results question questions
```

**Critical: do NOT drop:** `false, positive, negative, true, gate, verify,
auth, timeout, reset, block, blocked, broken, fail, failed, failing, error,
crash, leak, race, deadlock, rollback, revert`.

**Note** (after Wave 1 ship): `regression` IS in STOPWORDS by deliberate choice —
analysis showed it functions as a frame-word ("X regression in Y") rather than
problem-signal, and label drift in real handovers does not consistently carry it
across all variants of the same topic. Verified: with `regression` stripped, the
4 LABEL_DRIFT_VERIFY_GATE variants collapse to one hash AND the 5
DIFFERENT_TOPICS (incl. 2 near-collision verify_gate variants) all produce
distinct hashes. See `~/.claude/scripts/stuck_loop_key.py` STOPWORDS comment block.

**Tests** (`~/.claude/scripts/tests/test_stuck_loop_key.py`):

Required fixtures — must all collapse to **one** hash (CORRECTED 2026-04-29 after
A1 first-cut over-stripped STOPWORDS to force collapse on too-divergent variants;
real-world label drift over 11 days does not lose 5 of 6 core tokens):

```python
LABEL_DRIFT_VERIFY_GATE = [
    "verify_gate v1.5 newest-block-wins false positive — fix the FP",
    "newest-block-wins false positive regression in verify_gate (audit follow-up)",
    "verify-gate hardening: handle the v1.5 false positive on newest-block-wins",
    "audit follow-up: verify_gate false positive on newest block, v1.5",
]
# All share core tokens: verify, gate, false, positive, newest, block
# All four must produce the same hash (label-drift resilience)
```

Required fixtures — must NOT collapse (includes near-collision verify_gate
topics that share `verify+gate` but differ on FP-specific tokens — these
must hash to DIFFERENT values, forcing the algorithm to retain signal words):

```python
DIFFERENT_TOPICS = [
    "verify_gate v1.5 newest-block-wins false positive",     # the loop
    "rolling_memory consolidate: Haiku timeouts on long content",
    "supervisor quota: circuit breaker stuck OPEN after restart",
    "verify_gate v2 cache invalidation across sessions",     # near-collision (shares verify+gate)
    "verify_gate latency hot path optimization",             # near-collision (shares verify+gate)
]
# All 5 pairwise hashes must differ — proves the hash retains FP-specific signal
# (false, positive, newest, block) and is not just a verify+gate collapse.
```

**Acceptance for token retention** (added after A1 first cut):
```python
def test_signal_tokens_survive_filtering():
    result = make_stuck_loop_key(LABEL_DRIFT_VERIFY_GATE[0])
    expected_signal = {"verify", "gate", "false", "positive", "newest", "block"}
    assert expected_signal.issubset(set(result["tokens"])), \
        f"Signal tokens stripped: missing {expected_signal - set(result['tokens'])}"
```

### D2 — `~/.claude/scripts/telemetry_agent_health.py` (MODIFY, +50 LOC)

Add 6th signal `Stuck-loop`:

```python
def _stuck_loop_signal(handover_files: list[Path]) -> tuple[str, bool]:
    """Read last 5 handovers, extract First-step body (Russian/English),
    hash via make_stuck_loop_key, return (prose, is_warn).
    Warn when same hash appears ≥3× in window AND no verify_gate=pass
    evidence references that hash."""
```

Output line goes between "Stale citations" and "Session cadence":
```
Stuck-loop signal: <hash> appears 3/5 last handovers; no verify_gate=pass match (target=0) ⚠
                   OR
Stuck-loop signal: 0 recurring hashes in 5 last handovers ✓
```

JSON mode (`--json`) adds `"stuck_loop": {"hash": ..., "count": ..., "warn": bool, "tokens": [...]}`
so downstream scripts can parse without prose-grep.

### D3 — `~/.claude/scripts/rolling_memory.py start-context` (MODIFY, +120 LOC)

**D3a — `--stuck-check` flag:** runs `_stuck_loop_signal()` over project
handover'ы; if warn → emit prose block:
```
=== STUCK LOOP CANDIDATE ===
Hash: <16hex>
Tokens: verify_gate, false, positive, newest, block, wins, audit, v1.5, ...
Appearances: 4 of last 5 handovers (since 2026-04-18)
No verify_gate=pass evidence references this hash.

Reframe questions Claude MUST answer in plan:
  Q1: Is this still the right problem? Symptom?
  Q2: Is the root upstream? What did each fix attempt assume about layer N-1?
  Q3: What did the original framing miss? Read the FIRST handover that
      surfaced this hash; name the assumption that is now invalidated.
  Q4: Is the acceptance criterion still meaningful?

If answers don't justify continuing — tag [SUPERSEDED by <id>] in rolling_memory.
```

**D3b — `build_topic_timeline()`:**

```python
def build_topic_timeline(
    conn,
    seed_text: str,           # latest handover First-step body
    git_paths: list[str],     # from `git status --porcelain`
    limit: int = 6,
) -> tuple[list[dict], str | None]:
    """Returns (timeline_rows, stale_flag_msg).

    Algorithm:
      1. Extract topic_keywords from seed_text + path basenames via regex
         r'[a-z][a-z0-9_./-]{3,}', minus STOPWORDS, top-5 by frequency
      2. FTS5 query: agent_memory_fts MATCH '"<kw1>" OR "<kw2>" ...'
         WHERE memory_type IN ('consilium','audit') AND active=1
         ORDER BY (CASE status WHEN 'superseded' THEN 2 WHEN 'under_review' THEN 1 ELSE 0 END),
                  created_at ASC
         LIMIT 12
      3. Recency floor: union with last 4 consilium/audit rows from last 21d
      4. Dedupe by id, sort by created_at ASC, prune to limit (oldest 2 + newest 4)
      5. Stale flag: if len ≥ 5 AND span_days ≥ 14 AND ≥3 rows status != 'superseded'
    """
```

**D3c — Render block, inserted BEFORE existing flat list in `start-context` output:**

```
=== TOPIC TIMELINE: "<top-3-keywords>" ===
Premise (2026-MM-DD): <title — first row>
Tried   (2026-MM-DD): <title — middle row>
Result  (2026-MM-DD): <title — middle row>
Current (2026-MM-DD): <title — last row>
OPEN: <derived from last row's status; if any row has status='under_review', surface it>
⚠ STALE-TOPIC if flag set
```

### D4 — `~/.claude/rules/commands.md /start` step 2 (MODIFY, +12 LOC)

Append after existing step 2 bullets:

```
   - **Stuck-loop discipline**: when `start-context --stuck-check` emits
     `=== STUCK LOOP CANDIDATE ===`, before EnterPlanMode you MUST answer
     reframe-questions Q1–Q4 inline (one paragraph each). If answers
     justify continuing — proceed; if not — soft-delete the topic via
     `rolling_memory.py forget --id <N>` AND drop it from handover
     Next-step. Silently re-listing the topic without answering Q1–Q4
     means the next /start re-fires the block.
```

### D5 — `~/.claude/scripts/stuck_loop_review.py` (NEW, ≤60 LOC, stdlib)

Standalone script. CLI:
```bash
python3 stuck_loop_review.py --window 14d
```

Reads `~/.claude/logs/telemetry_agent_health.jsonl` (assumes exists; if
not, prints "(no telemetry log yet)"); aggregates `stuck_loop` warnings;
prints:
```
=== STUCK-LOOP DETECTOR REVIEW (window: 14d) ===
Detector fires:        N
Distinct hashes:       M
Top-fired hash:        <16hex> (K times) — tokens: verify_gate, false, positive, ...
Manual annotation:     (review handovers and annotate confirmed/dismissed)

Promotion criteria (from consilium 2026-04-29):
  ✓/✗ ≥2 confirmed loops detected
  ✓/✗ ≥1 missed case (manual)
  ✓/✗ collision rate ≤20%
```

### D6 — Tests directory bootstrap

```
~/.claude/scripts/tests/__init__.py
~/.claude/scripts/tests/test_stuck_loop_key.py    # see D1
~/.claude/scripts/tests/test_topic_timeline.py    # SQLite in-memory DB, 6 fake rows, assert timeline ordering
```

## 3. Acceptance criteria

A1. `pytest ~/.claude/scripts/tests/` exit=0 on full suite.
A2. `make_stuck_loop_key()` collapses 4 verify_gate label-drift fixtures to one hash.
A3. `make_stuck_loop_key()` keeps 3 distinct topic fixtures distinct.
A4. `telemetry_agent_health.py` exit=0; prints 6th `Stuck-loop signal:` line; verify_gate-class repetition triggers `⚠`.
A5. `rolling_memory.py start-context --stuck-check --scope <repo>` exit=0; renders TOPIC TIMELINE block + STUCK LOOP CANDIDATE block on Claude_Booster repo (verify_gate is real-world fixture).
A6. /start token budget ≤5.5K total (baseline ~5K + ≤500 for new blocks).
A7. `--json` mode adds `stuck_loop` and `topic_timeline` keys; existing keys backwards-compatible.
A8. No schema migration; existing 161 active rows untouched.
A9. Rollback: `git checkout HEAD~1 -- <files>` restores pre-change behavior in <2 min for each file.

## 4. Smoke test (single command)

```bash
cd ~/Projects/Claude_Booster && \
  python3 -m pytest ~/.claude/scripts/tests/ -q && \
  python3 ~/.claude/scripts/telemetry_agent_health.py && \
  python3 ~/.claude/scripts/rolling_memory.py start-context \
    --scope $(pwd) --stuck-check && \
  echo "=== smoke OK ==="
```

Pass = all four exit=0 and "smoke OK" prints.

## 5. Verify-gate evidence template (для handover commit)

```json
{"verified": {"status": "pass", "evidence": [
  "pytest ~/.claude/scripts/tests/ collected N tests, M passed in K.Ks (exit=0); test_stuck_loop_key.py:test_label_drift_verify_gate asserts 4 inputs → 1 hash, test_label_drift_distinct_topics asserts 3 inputs → 3 hashes",
  "python3 ~/.claude/scripts/telemetry_agent_health.py exit=0; Stuck-loop signal line present in stdout; verify_gate hash count visible",
  "python3 ~/.claude/scripts/rolling_memory.py start-context --stuck-check --scope <repo> exit=0; STUCK LOOP CANDIDATE block rendered for verify_gate hash; TOPIC TIMELINE block rendered with ≥3 dated rows",
  "wc -c on /start composite output ≤5500 chars (baseline 5000 + delta 300-400)",
  "rollback verified: git checkout HEAD~1 -- ~/.claude/scripts/* restores prior telemetry output (no Stuck-loop line); restored in <2 minutes"
], "reason_na": null}}
```

## 6. Risks / edge cases (карта для имплементора)

| # | Risk | Detection | Fix |
|---|---|---|---|
| R1 | STOPWORDS over-aggressive — drops important domain term | Test fixture: domain-specific 4-fixture-collapse must hold | Move term out of STOPWORDS |
| R2 | First-step regex misses Russian "Первый шаг" header | Smoke on real-world handovers (есть mix RU/EN) | Add Russian header pattern in regex |
| R3 | FTS5 query with hyphen tokens (e.g. "newest-block-wins") returns 0 — known FTS5 quirk per handover_2026-04-29 | Test with hyphenated keyword | Pre-process keywords: replace `-` with space inside FTS5 query |
| R4 | Token budget overflow when 6 long titles | wc -c in smoke | Truncate titles to 80 chars in render |
| R5 | git status returns 100+ paths in noisy repo | Limit basename extraction to top-10 by frequency | Done at algorithm level |
| R6 | `~/.claude/logs/telemetry_agent_health.jsonl` doesn't exist yet (D5 stub) | D5 prints "(no telemetry log yet)" | Add log emission to telemetry script in same PR |
| R7 | **Known limitation observed during Wave 2 ship (2026-04-29):** Booster handover First-step bodies are often pure bash code blocks. After spec-§D1 step 2 (strip code fences), canonical material is empty and tokens=[]. All bash-only First-steps hash to the same empty-canonical hash → detector fires ⚠ on a real "code-only handoff" pattern but loses verify_gate-FP signal (which was buried as a `#` comment inside bash). | Visible immediately: A2/A3 outputs print `tokens=[(code-only / empty canonical; low semantic confidence)]` next to the hash (UX label landed in fix-wave P1-8) | **v1.1 patch:** extend body extraction beyond First-step to include "Next step" / "Open items" / "Problems" sections (prose lives there). Defer until 2026-05-13 review or first manual-annotation cycle, whichever earlier. PAL also recommends `warn = (count >= 3 AND no_gate_pass AND bool(tokens))` so empty-token hashes don't trigger warnings unless the same hash appears with at least one non-empty token signature in the window. |
| R8 | **PAL second-pass deferrals (2026-04-29):** `has_pass_for_hash()` block-regex `\{[^{}]{0,3000}\}` correctly enforces flat-block same-block requirement, but FAILS on nested JSON evidence like `{"verified": {"status": "pass", "stuck_hash": "..."}}`. Current Booster verify-gate template emits flat blocks, so this is acceptable today, but a future template change to nested form would silently break clearance. | Add a unit test for nested-JSON evidence; document the flat-only contract in the verify-gate template comment. | **v1.1 patch:** swap the regex matcher for a small JSON tokenizer that walks `{...}` blocks at any depth, OR explicitly mandate flat blocks in the verify-gate template via a CI check. |
| R9 | **PAL second-pass deferrals — minor:** (a) `rolling_memory.py:1161` import path is `Path.home() / ".claude/scripts"` instead of `Path(__file__).resolve().parent` — brittle if script is run from a checkout or temp dir with a different `CLAUDE_HOME`. (b) `stuck_loop_review.py:106` event-dedup key uses `project_name` (basename), not project path — two projects with the same basename can fold into one logical event. (c) `telemetry_agent_health.py:210` handover sort uses raw `p.stat().st_mtime` without try/except — race-prone if a handover is rotated between glob and stat. (d) `stuck_loop_review.py:132` "most-frequent token signature" comment is inaccurate — `set` iteration is arbitrary, not most-frequent; should use `Counter`. | Each is local, low-frequency, and not user-visible at /start cadence. Tests would catch a regression if any of them surface. | **v1.1 patch:** address as a single 30-LOC cleanup commit at next review window (2026-05-13 or first audit-mode firing, whichever earlier). |
| R10 | **Window-size mismatch between telemetry and rolling_memory** (observed at MERGE-phase smoke 2026-04-29): telemetry's `_stuck_loop_signal` window inherits from telemetry's overall N (10 handovers default for cadence/evidence/N-A signals), rolling_memory's `_stuck_loop_signal` reads "last 5 handovers" per spec §D2. On the same data, telemetry counts the bash-only-body hash <3 times in 10-window → returns None, while rolling_memory counts ≥3 in 5-window → fires `STUCK LOOP CANDIDATE`. C1+C2 (consistency mini-patch) eliminated body-extraction and gate-clearance drift — both now read identical text and use shared helpers. The remaining sensitivity divergence is a window-size disagreement, not a bug in either detector. | Both tools individually correct per their own spec; user sees one ⚠ and one ✓ on borderline counts. Honest framing: telemetry is more conservative (wider window dilutes count); rolling_memory is more eager (tighter window concentrates). | **v1.1 patch:** harmonize windows — either both = 5 (eager) or both = 10 (conservative). Defer the eager/conservative choice to the 2-week review (2026-05-13) once we have real fire data. ~3-LOC change in either file. |

## 7. Out-of-scope (explicit, do NOT add)

- ALTER TABLE for topic_key, parent_id, problem_hash
- LLM/Haiku call inside detector
- Hard PreToolUse block on EnterPlanMode
- Soft-delete cascade or rolling_memory.db schema changes
- New MCP server / new external dep
- Rewrite of existing /start, /handover, /consilium, /audit protocols beyond §D4

## 8. Promotion to v2 — review at 2 weeks (calendar mark 2026-05-13) and 4 weeks (2026-05-27)

Decision in `stuck_loop_review.py` output. Promote if ≥1:
1. Detector caught ≥2-3 confirmed loops (Dmitry annotates)
2. Detector missed a high-value loop (manual report → fixture add)
3. Collision rate >20%

Promotion targets (in priority order, from parent consilium):
- (P1) ALTER TABLE add `problem_hash` column + LLM extractor at write
- (P2) Hard /start block on stuck-hash with 4 reframe-questions
- (P3) Causal Re-evaluation Block as part of phase_gate RECON→PLAN

## 9. Cross-session handoff

Any agent picking this up reads:
1. This file (implementation spec, design-frozen)
2. `reports/consilium_2026-04-29_temporal_causal_recon_pivot.md` (only if challenging a design decision; otherwise skip)
3. Latest handover with `name:` matching "stuck-loop" or "temporal-causal" (progress state)

Then runs §4 smoke; gaps become next session's first step. Do NOT re-derive
design from scratch — every "but what about Y?" question is answered in the
parent consilium §3-§5.
