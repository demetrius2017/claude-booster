# Consilium: require_task gate friction — block vs advisory

**Date:** 2026-05-14
**Topic:** Should require_task.py remain a blocking PreToolUse gate or become an advisory?
**Agents:** Tooling Engineer (gpt-5.5), DX Engineer (gpt-5.5), Systems Architect (gpt-5.5)
**GPT External:** Unavailable (PAL MCP not connected)

## Task Context

`require_task.py` blocks Edit/Write on source files unless a TaskCreate was found in the last 1200 transcript lines. This creates 2-3 extra round-trips per editing session (ToolSearch for deferred TaskCreate schema, TaskCreate call, retry the blocked Edit). Two other gates already fire on the same tools:
- `phase_gate.py` — blocks Edit outside IMPLEMENT phase
- `delegate_gate.py` — limits Lead to 1 direct action, resets budget on TaskCreate

## Verified Facts (from RECON)

- require_task: Edit/Write/NotebookEdit matcher, regex `"name"\s*:\s*"TaskCreate"` in last 1200 transcript lines
- Bypasses: `[no-task]` marker, `SKIP_TASK_GATE=1` env, docs/reports/.claude/*.md allowlist
- No subagent auto-skip (unlike dep_guard, delegate_gate)
- delegate_gate resets counter on TaskCreate (delegation signal)
- CC bug #16598 blocks `updatedInput` — hooks cannot modify tool params
- Hooks CAN: exit 0/2, emit additionalContext, run subprocess fire-and-forget

## Agent Positions

| Agent | Position | Key Insight | Recommendation |
|-------|----------|-------------|----------------|
| Tooling Engineer | Ceremony, not safety | "Hard gates should enforce properties meaningful AND machine-verifiable. require_task no longer meets that bar" | F: advisory + telemetry |
| DX Engineer | Blocking = friction without proportional value | "Deprecate as blocking gate; inject additionalContext advisory; log misses" | B+F: advisory + telemetry |
| Systems Architect | Redundant — correlated check, not independent layer | "Checks same transcript artifact delegate_gate already consumes. Correlated hard gate, not separate safety layer" | E or fold into delegate_gate |

## Gate Interaction Matrix

| Failure mode | Primary catcher | require_task adds value? |
|---|---|---|
| Edit outside IMPLEMENT | phase_gate | No |
| Lead edits inline instead of delegating | delegate_gate | No |
| Budget reset on TaskCreate | delegate_gate | No |
| First Edit with no TaskCreate in transcript | require_task | Weak yes — proves token exists, not that plan is relevant |

## Decision: F — Advisory mode + telemetry

**Consensus: 3/3 agents recommend removing the hard block.**

Changes:
1. `require_task.py` changes exit 2 → exit 0 + stdout `additionalContext` JSON when TaskCreate absent
2. Advisory message: "No TaskCreate found. For substantive edits, create/update a task; for trivial edits add [no-task]."
3. Log advisory misses to `~/.claude/logs/require_task_advisory.jsonl` (file, tool, phase, session_id)
4. phase_gate + delegate_gate remain hard gates — they cover all real failure modes

## Rejected Alternatives

| Option | Reason rejected |
|--------|----------------|
| A. updatedInput auto-inject | CC bug #16598 — technically impossible |
| C. subprocess TaskCreate | Hidden side effects, debugging nightmare, boundary violation |
| D. Lazy-load schema at SessionStart | Partial fix only — removes ToolSearch but keeps retry loop |
| E. Full removal | Loses advisory signal with no replacement; advisory mode is cheap to keep |
| B. Pure advisory (no telemetry) | Loses auditability; adding JSONL logging is trivial |

## Risks

- **Advisory fatigue:** Claude may learn to ignore the advisory. Mitigated by: the advisory only fires once per editing session (first Edit without TaskCreate), not on every Edit.
- **Regression in plan discipline:** If Lead starts editing without any planning signal. Mitigated by: phase_gate already ensures IMPLEMENT phase, delegate_gate limits to 1 action. The planning signal comes from the PLAN → IMPLEMENT phase transition, not from TaskCreate.

## Implementation Recommendations

- Change `require_task.py::main()` to emit JSON stdout `{"additionalContext": "..."}` and exit 0 instead of exit 2
- Add JSONL append to `~/.claude/logs/require_task_advisory.jsonl` with `{ts, session_id, file_path, tool, phase}`
- Keep all existing bypass logic (env, [no-task], allowlist) — they now suppress the advisory instead of bypassing a block
- Update template in `templates/scripts/require_task.py` to match
