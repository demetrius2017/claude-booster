# Consilium 2026-05-31 — Goal-Loop Discipline (unsatisfiable `/goal` infinite loop)

## Task context

After upgrading to Opus 4.8 and raising reasoning effort above `medium`, sessions entered an **infinite turn-loop**: the host's built-in `/goal` command was set with a completion condition that required applying a production-DB index (`CREATE INDEX CONCURRENTLY`) — an irreversible migration the agent is *correctly* required to refuse without explicit user authorization (`core.md` 51% Rule carve-out). The condition is therefore **unsatisfiable-by-the-agent**; the agent cannot clear its own `/goal`; so it re-worded the same "Debt [4] awaits your authorization… `/goal clear`" message 7+ times, burning turns/tokens and (per user report) accreting invented "debts" to look productive.

### Verified Facts Brief (verified against code/logs this session — ground truth)

| # | Fact | Evidence |
|---|------|----------|
| VFB1 | Loop driver = Claude Code **built-in `/goal`** (sets completion condition, re-invokes model every turn until met). Unpatchable by Booster. | CC changelog L464 |
| VFB2 | `/goal` is **opaque** to Booster: no state file, no Stop/UserPromptSubmit hook-input field; evaluator entangled with hook-enable flags. → **No hook can reliably detect an active goal.** | `find` empty; changelog L449 |
| VFB3 | Agent **cannot self-clear** `/goal` (built-in command, not a Booster skill). Only the human (`/goal clear`) or satisfying the condition ends it. | CC command model |
| VFB4 | Booster Stop-hook `ask_gate.py` is **innocent**: respects `stop_hook_active` re-entrancy (max 1 block/turn, cannot loop); 22 blocks / 3077 allow / 453 bypass all-time, all 22 genuine "?" questions; loop messages end in "." and don't match. | `ask_gate.py:296`; `ask_gate_decisions.jsonl` tally |
| VFB5 | **No Booster command invokes `/goal`** (grep empty). `/debt` is passive inventory (TaskList + git + `.session_debts.json`; modes list/add/work/resolve/review). | grep; `commands/debt.md` |
| VFB6 | Amplifier: Opus 4.8 at effort > medium is more agentic — re-attempts/re-words instead of idling. | user report + behavior |
| VFB7 | `core.md` already mandates user-confirmation for irreversible/external/auth actions (prod-DB migration explicit). **The refusal is correct; the defect is the endless re-wording + invented busy-work.** | `core.md` 51% Rule |

**Structural constraint:** Booster's only levers are (a) rules/prose shaping agent behavior, (b) the `/debt` command, (c) hooks *iff* host state is observable (VFB2 says it isn't). The loop can be ended only by the human.

## Agent positions

| Agent | Position | Key insight | KPI |
|-------|----------|-------------|-----|
| **Harness/Tooling Architect** (Opus) | Rule-first (Rank 1) + honest proxy hook as *human-nudge backstop only* (Rank 2) + `/debt BLOCKED-EXTERNAL` (Rank 3) + **general "opaque host feature" interface principle** (Rank 4) | Booster's authority stops at the host boundary; past it, it may only *advise the human*. A Stop-hook can't break a `/goal` loop (exit-0 → re-invokes; exit-2 → worse). Don't ship hook logic that *pretends* to control an opaque host feature — "it will be wrong silently and rot." | reworded-msg count ≤1; tokens-to-intervention ↓; mis-framed BLOCKED debts = 0 |
| **Agent-Behavior / Process Designer** (Opus) | New file `goal-loop-discipline.md`; paste-ready rule | Detection from conversation alone (goal-generated turn **AND** core.md-gated blocker **AND** stated ≥2 turns) → **byte-identical Terminal Card**, forbid re-wording, forbid invention. Effort-awareness: "the urge to do more" under halt is a *symptom*, not a reason. ME-vs-USER test prevents premature give-up. | loop-length-to-halt ≤2; card-stability = 1.0; invention-count = 0 |
| **Reliability / Safety Engineer** (Opus) | F-A SHIP-WITH-GUARDS · F-B SHIP · **F-C REJECT** | Halt must key off **action-class of the goal's acceptance step**, never an error string (a `403`/timeout ≠ "needs user"). Closed enumeration; "hard/stuck" excluded → routes to existing anti-loop. F-C can't stop re-invocation, false-positives on legitimate iteration, global blast radius. **Highest risk = "blocked-on-user" escape hatch.** | false-halt rate <5%; thrash-persistence →0; msg-stability =100% |
| **GPT-5.5** (PAL, external) | F-A + F-B; reject F-C | Add a **"progress-before-block" requirement**: complete all safe progress, *then* block with the smallest specific unblock request. Use declarative wording, no "?". **Never use fake-enforcement language** ("this prevents recursion") — say "this makes it non-compliant behavior + improves legibility, but does not mechanically prevent host invocation." | valid-BLOCKED-EXTERNAL checklist pass-rate |

## Decision

**Adopt F-A + F-B. Reject F-C as a control (optional log-only telemetry later). Add the host-boundary interface principle.** All four perspectives converged; differences were refinements, not disagreements.

### 1. New rule `~/.claude/rules/goal-loop-discipline.md` (F-A)
- **ME-vs-USER test (the gate against both failure modes).** Before halting, the agent must answer: *"Is there any action I am ALLOWED to take, by myself, that moves the goal's acceptance condition measurably closer?"* Try up to **2 substantively-distinct self-allowed approaches**; if both dead-end at an enumerated carve-out action → halt. This simultaneously blocks the escape-hatch (can't halt on mere difficulty) and premature give-up (must name a not-yet-tried allowed action to continue).
- **Closed enumeration of qualifying blockers** = exactly `core.md`'s set: irreversible op, external side-effect, auth/credential/secret, prod-DB DDL/DML. **"Hard / stuck / can't diagnose" is explicitly NON-qualifying** → routes to the existing `core.md` anti-loop (STOP + explain + ask direction), a *different* exit.
- **Terminal Card** — declarative, **no "?"**, **byte-identical across every subsequent goal turn** (copy the prior card verbatim, do not regenerate). Format includes GPT's progress-before-block structure:
  ```
  GOAL BLOCKED — needs you.
  Completed before block: <what was done safely>.
  Blocker: <the gated action, named carve-out category, written ONCE>.
  To proceed, do ONE of: (a) run `/goal clear`, or (b) reply the exact
  authorization: "<verbatim unblock phrase>".
  ```
- **Anti-invention discipline:** while halted — FORBID `/debt add` of speculative items, agent/Worker spawns "to make progress," and adjacent refactor/doc busy-work. A blocked goal has exactly one valid action: re-emit the card. "I cannot find real work so I will manufacture some" is the banned failure.
- **Effort-awareness:** at high `CLAUDE_EFFORT`, agentic drive is pointed at the wrong target; restraint is the disciplined move. Effort buys better *diagnosis* of the blocker, never more attempts to route around the user.
- **Honesty clause (GPT):** the rule documents that it makes loop-perpetuation *non-compliant behavior + improves state legibility* — it does **not** mechanically prevent host `/goal` re-invocation.

### 2. `/debt` gains `BLOCKED-EXTERNAL` status (F-B)
- `.session_debts.json`: new `status` value `BLOCKED-EXTERNAL` + required `unblock_action` (named external dependency + smallest user action).
- New verb `/debt block N "<unblock_action>"`.
- `/debt list` and `/debt review` render these in a separate **"Blocked — needs human"** bucket, **excluded** from any open/closeable count or "all debts closed" framing — removing the linguistic fuel an unsatisfiable goal latches onto.
- **Validity checklist** (guards the escape hatch at the data layer): external to agent · cannot be safely inferred/substituted · meaningful safe progress no longer possible · unblock request specific and minimal.

### 3. Host-boundary interface principle (general — into `core.md`)
> **Opaque host features.** When a Claude Code feature is unobservable from hooks/disk and uninvokable as a skill (`/goal`, built-in slash commands), Booster MUST NOT pretend to control it. Permitted moves, in order: (1) shape the **agent's own output** via rules; (2) emit **non-blocking advisories to the human**, who retains host control; (3) make Booster's **own state legible** so the agent doesn't mistake a host-imposed wall for unfinished work. FORBIDDEN: hook logic that claims to detect/clear/override an opaque host feature.

## Rejected alternatives

- **F-C — Stop/PostToolUse loop-detector** (N near-duplicate messages → injected "LOOP DETECTED" advisory). **Rejected by 2 agents + GPT.** Two independent kill-shots: (1) per VFB1 a Stop-hook exit-0 cannot stop `/goal` re-invocation, and exit-2 fights the evaluator with global blast radius (same surface as `ask_gate`, VFB4); (2) false-positives on legitimate iteration (flaky-test retries, progressive refinement, polling). Adds a failure mode without curing the disease, and creates a false sense of safety. **Permitted only as a future passive, log-only KPI counter — never an injected advisory or exit-2.**
- **Patching `/goal` itself** — impossible (host built-in, VFB1).
- **Letting the agent self-clear `/goal`** — impossible (VFB3).

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **"Blocked-on-user" escape hatch** — high-effort model relabels merely-hard tasks as externally-blocked to quit cheaply | HIGH (all agents' top concern) | Closed enumeration tied to the goal's *acceptance action* class; "hard/stuck" non-qualifying; agent must NAME the carve-out category (auditable); ME-vs-USER test demands a not-yet-tried allowed action to continue |
| **Over-correction → premature give-up** on ME-completable goals | MED | ME-vs-USER test is asymmetric: "I feel stuck" never justifies halt; only "sole remaining acceptance action is user-gated" does. 2-attempt floor before halt |
| **False-classification** of a transient blocker (403/timeout/missing-env) as user-gated | MED | Halt keys off action-class, never error-string; agent must map blocker to an enumerated category or keep working |
| Rule non-compliance under high effort (the very condition that causes the loop) | MED | Byte-stable card is near-zero-cost so compliance is easy; honesty clause + optional future telemetry as backstop |

## Implementation recommendations (next phase)

1. Write `~/.claude/rules/goal-loop-discipline.md` (+ template copy under `templates/rules/`) — paste-ready draft exists in the Agent-Behavior agent's output.
2. Add the **host-boundary interface principle** paragraph to `core.md` (+ template), one cross-link line from the anti-loop section.
3. Extend `/debt` (`commands/debt.md` + template) with `BLOCKED-EXTERNAL` status, `/debt block N` verb, separate render bucket, validity checklist.
4. (Deferred/optional) passive log-only near-duplicate counter for KPI telemetry — NOT a control.
5. Low blast radius (prose + one data-status enum, zero hot-path code) → Lead-direct edits; rollback = revert the paragraph/enum, instant.

KPIs to track post-deploy: loop-length-to-halt ≤2 · card byte-stability = 1.0 · invention-count while halted = 0 · false-halt rate <5% · thrash-persistence → 0.
