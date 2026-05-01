---
description: Inspect or control the delegate_gate — budget=1 per delegation window (hard-enforced, not a guideline)
argument-hint: status | reset | off | on
---

# /delegate — delegation-budget gate

`delegate_gate.py` is a PreToolUse hook that **physically blocks** the Lead
(main Claude session) from performing more than `CLAUDE_BOOSTER_DELEGATE_BUDGET`
(default 1) direct "action" tool calls (`Bash` / `Edit` / `Write` /
`NotebookEdit`) between delegation events (`Agent` / `TaskCreate` /
supervisor-spawn). Reads (`Read` / `Grep` / `Glob` / `WebSearch` / `WebFetch`)
are unlimited — the Lead can recon freely to build agent briefs.

## Invocations

```bash
# Status: show current counter + budget + mode:
cat <repo>/.claude/.delegate_counter 2>/dev/null || echo 0
cat <repo>/.claude/.delegate_mode 2>/dev/null || echo "on (default)"

# Reset counter to 0 (as if you just delegated):
rm -f <repo>/.claude/.delegate_counter

# Disable gate in THIS repo (still enforced everywhere else):
echo off > <repo>/.claude/.delegate_mode

# Re-enable:
rm -f <repo>/.claude/.delegate_mode

# One-off escape (don't want to toggle off globally):
CLAUDE_BOOSTER_SKIP_DELEGATE_GATE=1 <your-usual-command>
```

Dispatched as:

```bash
case "$ARGUMENTS" in
  status|"")
    echo "counter: $(cat .claude/.delegate_counter 2>/dev/null || echo 0)"
    echo "mode:    $(cat .claude/.delegate_mode 2>/dev/null || echo 'on (default)')"
    echo "budget:  ${CLAUDE_BOOSTER_DELEGATE_BUDGET:-1}"
    ;;
  reset)
    rm -f .claude/.delegate_counter
    echo "counter reset to 0"
    ;;
  off)
    mkdir -p .claude
    echo off > .claude/.delegate_mode
    echo "delegate_gate disabled for this repo"
    ;;
  on)
    rm -f .claude/.delegate_mode
    echo "delegate_gate re-enabled for this repo (default)"
    ;;
  *)
    echo "usage: /delegate [status|reset|off|on]"
    exit 2 ;;
esac
```

## Why this exists

Soft rules in `pipeline.md` (*"You are the Lead. Orchestrate agents, do
not write code directly."*) get interpreted as *"can do tool calls too as
part of orchestration"* and the Lead ends up doing 20 direct Bash/Edit
calls inline. Field logs 2026-04-21 had multiple sessions where the Lead
fell back to inline work after `/lead` failed. Physical hook = no
interpretation room.

## Behaviour on block

When the budget is exceeded, the Lead sees (stderr):

```
delegate_gate: direct-action budget exhausted (2/1 used on 'Bash',
counter resets on Agent/TaskCreate/supervisor-spawn).
The Lead orchestrates; delegate via Agent(type=Explore|Plan|general-purpose)
or `/supervise <task>` (→ python3 <repo>/.claude/scripts/supervisor/supervisor.py <prompt>).
```

The tool call fails with exit 2. Lead must spawn an Agent or `/lead`
worker to reset the counter, then can do another direct action.

## Allowlist (free, always)

Paths matching `/docs/`, `/reports/`, `/audits/`, `/tests/`, `/.claude/`,
`*.md`, `*.txt`, `README*`, `CLAUDE.md`, `/scratch/`, `/tmp/`, `*.log`
don't count as actions — Lead can freely write handovers, reports,
audit docs, and meta-files.
