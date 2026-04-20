---
description: Run a supervised worker session (Tier 0/1/2 policy + quota + silence detection) or inspect its state
argument-hint: run "<prompt>" [--cwd DIR] | status --session ID | decisions --session ID [--limit N]
---

# Supervise — Claude Booster Supervisor Agent v1.2.0

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
# Run a one-shot supervised session:
python3 ~/.claude/scripts/supervisor/supervisor.py run "$ARGUMENTS"

# Status snapshot of a session's quota:
python3 ~/.claude/scripts/supervisor/supervisor.py status --session <id>

# Recent decisions for a session:
python3 ~/.claude/scripts/supervisor/supervisor.py decisions --session <id> --limit 20
```

Actual execution of this slash command:

```bash
python3 ~/.claude/scripts/supervisor/supervisor.py $ARGUMENTS
```

## What it checks

| Tool | Default outcome |
|---|---|
| `Read` / `Grep` / `Glob` under `project_dir` or `/tmp/booster-*` | approve (Tier 0) |
| Read against `.env`, `id_rsa*`, `credentials*`, `/.aws/`, `/.ssh/`, `/.git/config` | **deny** (hard) |
| `git status/log/diff/show/branch/rev-parse` | approve, wrapped with core.pager=cat + core.fsmonitor= + diff.external= + protocol.version=2 |
| `curl` GET | approve (Tier 0), hardened: --no-netrc --max-redirs 0 --fail, Cookie/Authorization stripped |
| `curl` POST / `-d` / `--data-binary` | **deny** |
| `pytest` / `npm test` / `cargo test` | escalate → approved only if in `tier1_tools` (repo `.claude/supervisor.yaml` or `/supervise tier1 pytest`) |
| `npm install` / `pip install` / `cargo build` | escalate → approved only if `tier2_trusted_repo: true` in repo config |
| Deny-list Bash patterns (`git push --force`, `rm -rf /`, `kubectl delete`, etc.) | **deny** always |
| Same (tool, args) approved ≥3 times in 5 min | escalate (loop-guard) |

## Configuration

Per-project config in `<repo>/.claude/supervisor.yaml`:

```yaml
tier1_tools:
  - pytest
tier2_trusted_repo: false
estimated_tokens: 10000
```

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
