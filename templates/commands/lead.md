---
description: Run a supervised worker session (Tier 0/1/2 policy + quota + silence detection) or inspect its state
argument-hint: <free-form prompt> | sessions | status --session ID | decisions --session ID
---

# Lead — Claude Booster Supervisor Agent v1.2.0

Spawns a second Claude worker under deterministic policy gating, quota
admission control, and adaptive silence detection. The worker's tool
invocations are evaluated against `policy.py` (Tier 0/1/2 + deny-list);
escalations optionally go to a Haiku gatekeeper; every decision is
persisted to `~/.claude/rolling_memory.db`.

**One supervisor per CLI invocation. One worker per supervisor.**
Not a slash command that runs inside an existing conversation — this
is a session-level control that spawns its own `claude` subprocess.

## Invocations

```bash
# Model routing: pass --model claude-sonnet-4-6 for coding tasks (default).
# Use --model claude-opus-4-7 only for architecture / deep debugging.
# Simplest: free-form prompt, no quotes needed, no explicit `run`:
python3 ~/.claude/scripts/supervisor/supervisor.py --model claude-sonnet-4-6 fix the bug in foo.py

# Bare invocation — prints help + summary of all known sessions:
python3 ~/.claude/scripts/supervisor/supervisor.py

# Explicit subcommands (also valid):
python3 ~/.claude/scripts/supervisor/supervisor.py run --model claude-sonnet-4-6 fix the bug
python3 ~/.claude/scripts/supervisor/supervisor.py run --cwd /other/repo do thing
python3 ~/.claude/scripts/supervisor/supervisor.py status --session <id>
python3 ~/.claude/scripts/supervisor/supervisor.py decisions --session <id> --limit 20
python3 ~/.claude/scripts/supervisor/supervisor.py sessions [--limit N] [--json]
```

**Actual execution of this slash command:**

```bash
python3 ~/.claude/scripts/supervisor/supervisor.py $ARGUMENTS
```

`$ARGUMENTS` goes through untouched. If first token is a known
subcommand (`run`/`status`/`decisions`/`sessions`) it's used as-is;
otherwise everything is treated as the prompt under implicit `run`.

**Do NOT query `~/.claude/rolling_memory.db` directly** with ad-hoc
sqlite3 — the schema uses `started_at` / `worker_tokens` /
`circuit_state` (not `ts` / `used_tokens` / `state`). Prefer
`sessions`, `status`, `decisions` above.

Actual execution of this slash command:

```bash
python3 ~/.claude/scripts/supervisor/supervisor.py $ARGUMENTS
```

## What it checks

## Policy defaults (permissive blacklist as of v1.2.0)

Worker is **trusted by default** — supervisor only blocks explicitly-dangerous
patterns. Old whitelist behaviour still available via `paranoid_mode: true` in
`<repo>/.claude/supervisor.yaml`.

| Tool | Default outcome (permissive) | Under `paranoid_mode: true` |
|---|---|---|
| `Read` / `Grep` / `Glob` under `project_dir` or `/tmp/booster-*` | approve (Tier 0) | approve (Tier 0) |
| Read against `.env`, `id_rsa*`, `credentials*`, `/.aws/`, `/.ssh/`, `/.git/config` | **deny** (hard) | **deny** (hard) |
| `git status/log/diff/show/branch/rev-parse` | approve, scrub-wrapped (core.pager=cat + core.fsmonitor= + diff.external= + protocol.version=2) | same |
| `curl` GET | approve, hardened flags auto-injected | same |
| `curl` POST / `-d` / `--data-binary` | escalate → permissive approves, paranoid cancels | escalate → deny+cancel |
| `Bash` not in deny-list (`ls`, `psql`, `echo`, `python3 -c`, `docker ps`, etc.) | **approve Tier 1** — the v1.2.0 pivot | escalate → deny+cancel |
| `pytest` / `npm test` / `cargo test` | **approve Tier 1** (no config needed) | escalate unless in `tier1_tools` |
| `npm install` / `pip install` / `cargo build` | **approve Tier 2** (permissive trusts) | escalate unless `tier2_trusted_repo: true` |
| `Edit` / `Write` / `NotebookEdit` under `project_dir`, not in deny-paths | **approve Tier 1** | escalate → deferred to require_task / phase_gate |
| Deny-list Bash patterns (`git push --force`, `rm -rf /`, `kubectl delete`, `dd`, `mkfs`, etc.) | **deny** always | **deny** always |
| Same (tool, args) approved ≥3 times in 5 min | escalate (loop-guard) | same |

## Configuration

Per-project config in `<repo>/.claude/supervisor.yaml`:

```yaml
# Permissive blacklist is the default since v1.2.0 — uncomment to tighten:
# paranoid_mode: true
tier1_tools:
  - pytest
tier2_trusted_repo: false
estimated_tokens: 10000
max_continuations: 5       # how many times to re-spawn on CLI max_turns
autonomy_directive: true   # tell worker "act, don't ask A/B" via system-prompt
```

`paranoid_mode: true` restores the v1.2.0-original whitelist behaviour:
everything not explicitly on the Tier 0 allowlist escalates, and
without a Haiku escalator wired that means deny+cancel. Use for
credential-rich projects or CI-only runs where trust is low.

`max_continuations` (default 5) caps the `--resume <cli_session_id>`
chain. Set to 0 to disable auto-continuation (one-shot mode). Set
higher for very long research tasks; each continuation is still gated
by the quota circuit-breaker so a runaway loop can't blow the budget.

`autonomy_directive` (default `true`) injects an AUTONOMY_DIRECTIVE
system prompt into the worker spawn: "work autonomously, pick a path
using reversibility + scope + risk, don't ask the user A/B questions,
don't narrate plans asking for approval, 51%-rule on ambiguity". Set
to `false` if you want the worker to behave like an un-directed
interactive session (useful for pair-programming probes where you
want the worker to ask questions). Worker also gets
`--permission-mode auto` in either case so permission prompts don't
stall the subprocess.

## Persistence

Decisions land in `supervisor_decisions` (session_id, ts, tool, args_digest,
decision, tier, rationale, approved_by); quota snapshots in
`supervisor_quota` (5-hour rolling window, 15% supervisor reserve,
CLOSED → HALF_OPEN at 50% usage → OPEN at 85%). Both tables live in
`~/.claude/rolling_memory.db`.

## Limitations in v1.2.0

- No Haiku escalator wired yet — escalate decisions currently land as
  text in `supervisor_decisions.rationale`; an LLM gatekeeper lives in
  `prompts/supervisor_v1.md` and is invoked by the caller that owns
  API credentials.
- One worker per supervisor — multi-worker session pooling is Session 5.
- End-to-end red-team against the real `claude-agent-sdk` binary is
  gated on SDK install; current tests exercise the full chain via
  `FakeProc`.
