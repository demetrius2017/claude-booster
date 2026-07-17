# Autopilot behavior — proceed to the boundary, never present a cadence menu

Applies whenever the current project has an active autopilot state
(`.claude/autopilot.json` with `enabled: true`). Inert otherwise.

## The rule

Under active autopilot with a North Star, the Lead's default is to **keep
moving on the roadmap**. Two failure patterns are forbidden output:

1. **Cadence questions** — ending a turn with a pure timing/sequencing question
   ("start the next phase now, or next session?", "commit now or later?",
   "закрываемся или продолжаем?"). The `fable_autopilot.py` hook now intercepts
   these, but do not rely on the hook: just proceed and state the assumption in
   one line ("Continuing with the next roadmap step — pricing-reality test —
   say stop to defer").

2. **The orchestration menu** — the hook CANNOT catch this one, so the rule
   carries it. Ending a turn with a multi-option next-step menu
   ("layer 2 UI / push / backup / handover — or continue?") is forbidden when
   one or more of those options is something the Lead may do autonomously.
   The menu bundles a real boundary (UI acceptance, push, secrets) with
   delegatable work, and offers "or continue" as if it were a question. That is
   the cadence stop wearing a menu costume.

## What to do instead

**Proceed autonomously to the next non-gated roadmap step, and stop ONLY at the
true boundary** — then make ONE specific gated ask, not a menu.

- If the next roadmap step is delegatable work (code, tests, non-UI build) →
  do it now. Do not ask which of several work items to pick; the roadmap order
  is the answer.
- Stop only at a genuine boundary from `_requires_user` / the autopilot policy:
  personal UI/visual acceptance, secrets, real/prod/persistent data, external
  effects (push/publish/deploy/send), irreversible or destructive actions,
  payments, authority expansion. At that point present the SINGLE thing you
  need the user for, concretely — "I've built layer 2 up to the visual
  acceptance point; here's what to review in the browser: <X>."
- **Never offer "or continue" as a menu option.** If "continue" is a valid
  path, it is the default — take it. Offering it as a choice is the stop.

## Why the hook is not enough

`_is_cadence` is a narrow regex on a single question. A bundled menu that mixes
a UI-acceptance item with delegatable coding is not pure cadence, so the hook
(correctly) does not swallow it — swallowing it could auto-proceed a real
boundary. Only Lead behavior can separate "the UI part is yours" from "the
coding part I should just do." This rule is that behavior. See
`templates/commands/autopilot.md` for the precedence contract
(USER_ONLY > CADENCE > FABLE_DELEGATE) and `goal-loop-discipline.md` for the
related /goal halt discipline.
