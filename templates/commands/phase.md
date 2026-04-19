---
description: Show or set the current workflow phase (RECON/PLAN/IMPLEMENT/AUDIT/VERIFY/MERGE)
argument-hint: [get | set <PHASE> | list]
---

# Phase — Lead-Orchestrator workflow state

Run the phase CLI with the user's arguments. No argument = show current phase.

```bash
if [ -z "$ARGUMENTS" ]; then
  python3 ~/.claude/scripts/phase.py get
else
  python3 ~/.claude/scripts/phase.py $ARGUMENTS
fi
```

## Phases

| Phase | Rule |
|---|---|
| `RECON` | read-only exploration (Read/Grep/Glob/WebSearch); no Edit/Write |
| `PLAN` | design + TaskCreate + consilium if uncertainty >30%; no code edits |
| `IMPLEMENT` | Edit/Write allowed; run tests after each change |
| `AUDIT` | code review + PAL second opinion; no new code |
| `VERIFY` | real curl / pytest / Chrome DevTools — collect evidence |
| `MERGE` | git push after user acceptance; post-merge curl/console check |

## Usage

- `/phase` — show current
- `/phase set PLAN` — advance to PLAN
- `/phase list` — show all phases with rules

## Enforcement

- `phase_gate.py` PreToolUse hook blocks Edit/Write outside IMPLEMENT (unless file is under docs/reports/tests/.claude/*.md)
- `phase_prompt_inject.py` UserPromptSubmit hook injects `[phase: X]` before every user message
- Transitions logged to `<project>/.claude/phase_transitions.log`
- Escape: `CLAUDE_BOOSTER_SKIP_PHASE_GATE=1`
