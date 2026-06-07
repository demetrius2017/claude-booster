---
name: "booster-command"
description: "Run Claude Booster command protocols in Codex. Use when Dmitry invokes or asks to install/run Booster commands such as start, handover, consilium, audit, architecture, go, debt, phase, update, delegate, lead, verify-flow, verify-after-edit, audit-trace, or hackathon."
---

# Booster Command Runner

This skill is the Codex compatibility layer for Claude Booster command specs.

Use it when the user invokes a Booster command by name, for example:

- `start`
- `handover`
- `consilium <topic>`
- `audit <topic>`
- `architecture [--update]`
- `go <artifact contract>`
- `debt <mode>`
- `$consilium <topic>` or `/prompts:consilium <topic>`

## Source Of Truth

Load the original command spec before executing. Resolve `<command>` from the
alias skill name, prompt name, or the first argument after `$booster-command`.

Search in this order:

1. `~/.claude/commands/<command>.md`
2. `references/commands/<command>.md` relative to this installed skill
3. `<repo-root>/templates/commands/<command>.md`
4. `<repo-root>/.claude/commands/<command>.md`

If the command spec is missing, say which paths were checked and stop.

## Codex Adapters

Execute the command behavior, not the literal Claude Code tool names.

- `Read` / `Grep` / `Glob` / `Bash` mean Codex file reads, `rg`, and shell tools.
- `EnterPlanMode` / `ExitPlanMode` mean use Codex planning behavior or `update_plan`.
- `TaskCreate` / `TaskUpdate` mean use `update_plan` and concise progress updates.
- Claude `Agent` / `Explore` / `general-purpose` means Codex subagents when the
  command explicitly asks for independent agents. The command invocation itself
  is explicit permission to use those subagents. Evidence is the spawned agent
  ids or names plus their final messages. If no Codex subagent tool is available,
  state that full independent-agent parity is unavailable and run only a local
  second pass; do not label that fallback as a full Booster multi-agent result.
- Claude model names (`haiku`, `sonnet`, `opus`) are guidance only. Use Codex's
  available subagent defaults unless a model can be pinned safely.
- PAL/GPT external review: use PAL if the MCP tools exist. If not, spawn a
  separate Codex review subagent when subagents are available and label it
  clearly as "Codex second opinion", not as PAL/GPT. If neither PAL nor
  subagents are available, mark the external-review step as unavailable with the
  missing tool evidence.
- Claude session JSONL paths under `~/.claude/projects/...` become the newest
  relevant Codex session JSONL under `~/.codex/sessions/...` when preparing a
  Codex handover. If a Claude session is relevant, mention both.
- Claude hooks are not assumed to be active in Codex. Preserve the evidence
  discipline in assistant output even when no hook enforces it.

## Execution Rules

- Always perform RECON against current code/config before reports or opinions.
- For `consilium`, `audit`, and `architecture`, build a Verified Facts Brief
  before spawning subagents.
- Save generated reports to the same repo paths the original command specifies,
  usually `reports/`.
- Do not invent top-level Codex slash commands. If bare `/consilium` is
  intercepted by the UI, use `/prompts:consilium` or `$consilium`.
- Keep outputs concise but include concrete evidence for verification steps.
