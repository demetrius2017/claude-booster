# Handover — 2026-06-13 (Claude_Booster)

## Summary

Single urgent task this session: **accelerated the Fable 5 → Codex routing revert from 2026-06-22 to today (2026-06-13)**.

Trigger changed under our feet. We had a `scheduled_changes` mechanism armed for 2026-06-22 (built in the 2026-06-12 session) to revert `coding`/`hard` from `anthropic:fable` back to `codex-cli:gpt-5.5` when Fable's free-on-Max window ended. But today Dmitry reported Fable 5 was **blocked (US government action)** before that window closed — the switch had to happen now.

**Mechanism used (not a hand-hack):** Read `_apply_scheduled_changes()` in `model_balancer.py` first. A scheduled entry applies when `today >= effective_date` and `applied_on` is null, and `decide()` re-checks due entries even when today's decision already exists (line 650). So the correct move was to move both entries' `effective_date` `2026-06-22 → 2026-06-13` in the runtime JSON and run `decide()` — the same one-shot, time-travel-tested path we'd built for the 22nd, just fired earlier. No routing hand-edit.

**Result (verified live):**
- `coding`: `anthropic:fable` → `codex-cli:gpt-5.5` ✅
- `hard`: `anthropic:fable` → `codex-cli:gpt-5.5` ✅
- `applied_on: 2026-06-13` stamped on both entries — idempotent, a repeat `decide()` won't re-fire.
- **Codex gpt-5.5 smoke test: returned `CODEX_OK`** (23,933 tokens) — confirms the new sole coder is reachable and responsive, important given the trigger was an external block.

**Unchanged by design:** `lead`=`anthropic:claude-opus-4-8` (orchestrator stays on Opus); `high_blast_radius`=`anthropic:claude-sonnet-4-6` (auth/security/migrations/financial route through Claude Agent so `dep_guard`/`financial_dml_guard`/`verify_gate` PreToolUse hooks fire — Codex subprocess is opaque to them).

No `.py` change was needed (data-only edit to runtime JSON). The repo template `templates/scripts/model_balancer.py` is untouched and stays in sync. The `model: "fable"` opt-in escape hatch is now moot while Fable is blocked.

## Goal + KPI

- **North Star:** Claude Booster — make Claude Code (and Codex) remember, learn, and audit itself across sessions, with one canonical install.
- **Current milestone:** Fable 5 endgame — keep delegation routing on the cheapest-correct provider as the Fable availability picture shifts. This session: react to the unplanned early block.
- **KPI (this session, met):** `coding`/`hard` routing flipped to flat-fee `codex-cli:gpt-5.5` and verified live (routing query + Codex reachability smoke); projected extra model spend stays $0 (Codex flat-fee), with zero window of Fable-billed delegation since Fable is blocked anyway.

## Tools used

- `Read` — `_apply_scheduled_changes()` / `decide()` internals in `model_balancer.py` before touching anything
- `grep` — locate the scheduled-changes mechanism
- `Edit` — runtime `~/.claude/model_balancer.json` (effective_date 06-22 → 06-13 + accelerated notes)
- `python3 model_balancer.py decide` + `get coding`/`get hard` — apply + verify routing
- `codex_worker.sh gpt-5.5` — reachability smoke (`CODEX_OK`)
- `Edit` — memory `feedback_codex_over_opus_routing.md` (accelerated-trigger note)

## Problems / Solutions

- **Trigger moved up 9 days (external block, not planned promo-end).** Solved by repointing the existing `scheduled_changes` `effective_date` to today rather than hand-editing `routing` — kept us on the tested code path.
- **Session-ref snippet failed (underscore→dash gotcha).** The `/handover` snippet's `sed 's|/|-|g'` converts slashes but not the `_` in `Claude_Booster` → CC project dir is `-Users-...-Claude-Booster`. Resolved the path manually. (Same gotcha flagged in handover 2026-06-12 — still unfixed in the snippet.)
- **No in-repo runtime change to verify.** The routing change lives in `~/.claude/model_balancer.json`, outside this git repo; the only committed file is this handover (`reports/`, allowlisted) → verify-gate `status='na'` is the honest classification. The routing flip was nonetheless verified live (routing query + Codex smoke).

## Outstanding Debts

| # | Debt | Priority | Origin |
|---|------|----------|--------|
| 1 | Codex-bridge `install.py` integration — now 5th carry-over | high | handover 2026-06-11 |
| 2 | Unpin `coding`/`hard` from `_PINNED_CATEGORIES` once Codex retry/re-spawn telemetry capture lands (`model_metrics.num_turns` flat 1.0) | medium | handover 2026-06-10 |
| 3 | `weekly_tokens_cap` not configured — `claude_max_tracker` falls back to stale snapshot for `weekly_max_pct` (86%) | low | handover 2026-06-12 |
| 4 | Fix the `/handover` session-ref snippet to handle `_`→`-` in project names (recurring manual fix) | low | this session |
| 5 | If/when the Fable block lifts: decide whether to re-enable Fable opt-in or stay on Codex; `scheduled_changes` entries are now spent (`applied_on` stamped) | low | this session |

## Required reading

- `reports/handover_2026-06-13_handover.md` — this file
- `~/.claude/model_balancer.json` — `coding`/`hard` now `codex-cli:gpt-5.5`, both `scheduled_changes` entries `applied_on: 2026-06-13`. Do NOT hand-edit routing; the revert already fired.
- `~/.claude/scripts/model_balancer.py` — `_apply_scheduled_changes()` semantics (applies when `today >= effective_date` & `applied_on` null; `decide()` re-checks due entries even on a same-day decision).
- `memory/feedback_codex_over_opus_routing.md` — full routing history incl. the 2026-06-13 accelerated-revert note.

## Session reference

Session UUID: `198833c6-9ce3-4a62-a02e-10cee6974e37`
JSONL: `/Users/dmitrijnazarov/.claude/projects/-Users-dmitrijnazarov-Projects-Claude-Booster/198833c6-9ce3-4a62-a02e-10cee6974e37.jsonl`

This JSONL can be grepped during RECON to see what was tried and where it got stuck this session (e.g. the `_apply_scheduled_changes` read, the routing verification, the Codex smoke).

## First step tomorrow

Confirm the accelerated revert is still in effect and Codex is reachable (one block):

```bash
python3 -c "import json,pathlib; d=json.loads((pathlib.Path.home()/'.claude'/'model_balancer.json').read_text()); print('coding/hard:', d['routing']['coding'], d['routing']['hard']); print('scheduled:', [(e['category'], e['effective_date'], e['applied_on']) for e in d['scheduled_changes']])"
# expect: coding/hard both codex-cli:gpt-5.5 ; both scheduled entries applied_on=2026-06-13
```

Then resume **debt #1: Codex-bridge `install.py` integration** (now 5th carry-over — highest priority).
