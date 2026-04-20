# Verified Facts Brief — Supervisor Agent v1.2.0 consilium

**Date:** 2026-04-20. **Project:** Claude Booster (personal Claude Code harness, Apache-2.0, sole author Dmitry).
**Purpose of this file:** shared brief for 5 consilium participants. Facts verified against code + web-reality on 2026-04-20.

---

## 1. Motivation

Dmitry's three daily irritations with Claude Code that v1.1.0 (phase machine + hard gates, shipped 2026-04-19) does NOT solve:

1. **Approval stops.** Claude asks confirmation on bash/edit where intent is obvious — user just presses Enter. Auto Mode helps partially, not fully.
2. **Semantic stops.** Claude halts and asks a clarifying question when the answer is obvious from context (violates 51%-rule from `~/.claude/rules/core.md`). Process physically waits for input instead of acting.
3. **Silent hangs.** Process doesn't move for N seconds with no visible question. No auto-detection, no nudging.

**Goal of v1.2.0:** remove human from the loop on routine confirmations. Human = escalation target, not keyboard.

## 2. Current repo state (greenfield for this feature)

- `templates/scripts/` — 18 hook/engine scripts (verified via `ls`): `phase_gate.py`, `require_task.py`, `require_evidence.py`, `preserve_plan_context.py`, `verify_gate.py`, `memory_*.py`, `rolling_memory.py`, `index_reports.py`, etc. **None** match `supervisor*`.
- `templates/settings.json.template` permissions.deny (lines 49–66, verified): `git push --force*`, `git push -f*`, `git reset --hard*`, `git clean -f*`, `git clean -d*`, `git branch -D*`, `rm -rf /*`, `rm -rf ~*`, `rm -rf $HOME*`, `kubectl delete*`, `docker system prune*`, `docker volume rm*`, `dd if=*`, `mkfs*`. **16 patterns total.** Supervisor deny-list MUST mirror verbatim.
- `templates/commands/` has only `phase.md`, `verify-after-edit.md`, `verify-flow.md`. `/supervise` slot free.
- License: Apache-2.0. License-compatible with MIT + Apache-2.0 upstream projects.

## 3. External dependencies (WebFetch-verified 2026-04-20)

### claude-agent-sdk (Python)

- PyPI package **exists**: name `claude-agent-sdk`, version **v0.1.63**, released **2026-04-18** (4 days ago). Development Status = Alpha (3).
- GitHub `anthropics/claude-agent-sdk-python`, ~6.5k★, active, Anthropic-maintained.
- Reads stream-JSON from `claude -p --output-format stream-json --verbose --include-partial-messages`. Each line = 1 JSON event.
- Stream-JSON event types: `message_start`, `content_block_start` (contains `tool_use` blocks!), `content_block_delta` (text_delta, input_json_delta), `content_block_stop`, `message_delta` (stop_reason, usage), `message_stop`.
- MCP servers work in headless via `--mcp-config` or `ClaudeAgentOptions(mcp_servers={...})`. No regression from TUI.
- System prompt injection: `--system-prompt` (replace) / `--append-system-prompt` / `--append-system-prompt-file` CLI flags, or `ClaudeAgentOptions(system_prompt=...)` in SDK.

### Two critical gaps in the SDK

- **Gap G1:** NO built-in "spawn worker + read stdin/stdout" abstraction. Supervisor must wire `subprocess.Popen` or `asyncio.create_subprocess_exec` itself.
- **Gap G2:** Stream-JSON has NO built-in "waiting for approval" or "stalled" signal. Supervisor must detect:
  - approval-question via **text heuristics** on `content_block_delta.text_delta` (grep for "Should I", "Can I", "Do you want", "approve", "Would you like me to", etc.);
  - silent-hang via **time-delta monitoring** between events (no event >N seconds while `stop_reason` has not fired).

### §3.5 Authentication — NO API key required (added 2026-04-20)

Three auth paths for `claude-agent-sdk` (it wraps `claude` CLI — inherits auth):

1. **OAuth token from Max/Pro subscription (RECOMMENDED).** `claude setup-token` → long-lived `CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-...` env var. Subprocess charges against subscription quota, not API billing. Official Anthropic path for headless-on-subscription.
2. **Inherited session.** If user already logged in via `claude login` in terminal, subprocess reuses `~/.claude/.credentials.json`. Zero extra config.
3. **API key** (`ANTHROPIC_API_KEY`) — fallback, per-token billing.

**Critical constraint:** Max/Pro subscription has rolling 5-hour usage windows. Supervisor (Haiku) + worker (Opus/Sonnet) = **same account** → quota burns 2× faster. At 10 sessions/day × ~400 tok/decision × 30 decisions ≈ 120k Haiku tok/day. Circuit-breaker on token-budget becomes **mandatory**, not optional.

**Implication for v1.2.0:**
- `install.py` must detect `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` on install, print `claude setup-token` instruction if neither, never fail install.
- README must document 3 auth paths, default to OAuth/subscription.
- Circuit-breaker (50k tok/session, 500k tok/day per Ops-agent proposal) = ship-blocker, not nice-to-have.

## 4. Prior art (WebFetch-verified 2026-04-20)

| # | Repo | License | ★ | Key pattern | Take | Drop |
|---|---|---|---|---|---|---|
| 1 | `Jedward23/Tmux-Orchestrator` | MIT | 1.7k | 3-level hierarchy, `send-claude-message.sh` async, 30-min git-commit as heartbeat | Async scheduling pattern; hierarchy idea for future multi-worker | tmux capture-pane regex (our supervisor reads stream-JSON, not TUI) |
| 2 | `awslabs/cli-agent-orchestrator` | Apache-2.0 | 487 | ENV state tracking `CAO_TERMINAL_ID` → IDLE/PROCESSING/COMPLETED/ERROR, message queue on readiness, role-based gating with `--auto-approve`/`--yolo` override | State-tracking via ENV or state-file; role-based approval policy | AWS-specific CLI provider integrations |
| 3 | `Dicklesworthstone/claude_code_agent_farm` | MIT+Rider | 785 | Heartbeat file, adaptive timeout (3× median completion-time, bounded 30–600s), state-JSON export `.claude_agent_farm_state.json`, atomic backups + file-locking | Adaptive timeout formula; state-JSON export for observability | Atomic-backups + file-locking (only needed with concurrent writers — MVP has single worker) |

## 5. Proposed architecture candidates (from tech-spec 2026-04-20)

- **Path A — Agent SDK Supervisor (recommended MVP).** Python script via `claude-agent-sdk` spawns Claude Code as subprocess headless (`-p`), reads stream-JSON, writes stdin. Supervisor itself is a Claude Haiku 4.5 with restricted tools (Read, TaskGet, `inject_user_message`, `kill_worker`, `escalate`) and a dedicated prompt: *"You are watchdog. Goal = X. Do not write code. Answer approvals per whitelist. Escalate non-obvious only."*
- **Path B — Tmux Watchdog.** Bash + `tmux capture-pane` + heuristics. No new Claude process; observes existing pane. Injects `tmux send-keys "yes" Enter` on whitelist match.
- **Path C — Hybrid.** A as default for scripted tasks, B as extra layer for interactive sessions. Common log `~/.claude/logs/supervisor_decisions.jsonl`.

## 6. Proposed whitelist v0 (to be challenged)

Supervisor auto-approves:
- Read-only tools: Read, Glob, Grep, WebSearch, WebFetch.
- Read-only git: `git status`, `git diff`, `git log`, `git show`, `git branch`.
- Tests without side effects: `npm test`, `pytest`, `cargo test`.
- `curl GET` to public URLs (domain whitelist).
- Bash inside `/tmp/`, `$HOME/scratch/`, sandbox dirs.
- TaskCreate, TaskUpdate (but NOT `status=completed` — that path owned by v1.1.0 `require_evidence`).

Supervisor MUST escalate:
- Any pattern in v1.1.0 permissions.deny (§2 above).
- Edit on production-config files (mirror list).
- Any prompt mentioning "production", "prod", "live".
- Consilium/audit requests (human opinion is the point).
- Any approval prompt NOT explicitly whitelisted (safe default).

## 7. Four questions the consilium must decide

- **Q1.** Supervisor as **headless subprocess (Path A)** vs **MCP server exposing approval-tool to worker** — which is more maintainable long-term?
- **Q2.** Is the whitelist in §6 complete, or does it have traps? (e.g., `pytest -x` with side effects; `git log` in a malicious repo with pager hooks; `curl GET` leaking auth; etc.)
- **Q3.** How to detect **semantic-stop** (worker stopped without explicit question)? heartbeat file? idle on stream-JSON gap? text heuristic on last content_block? adaptive timeout (Dicklesworthstone pattern)?
- **Q4.** Supervisor **stateful** (remembers past escalations, bounds re-ask loops per session) or **stateless** (every approval = fresh context, smaller memory footprint)?

## 8. Hard constraints on the answer

- Supervisor LLM = Haiku 4.5 (10–15× cheaper than Opus for MVP). `max_thinking_tokens=1000`.
- Deny-list non-negotiable — mirror v1.1.0 settings verbatim.
- MVP scope: ~200–300 LOC Python + 1 prompt file + 1 slash command. No multi-worker orchestration today.
- Output plugs into existing Booster: `templates/scripts/supervisor.py`, `templates/prompts/supervisor_v1.md`, `templates/commands/supervise.md`, `install.py::BOOSTER_VERSION = "1.2.0"`.
- Must be installable on same platform matrix as current Booster (macOS ARM/Intel, Ubuntu/Debian/Fedora/Arch/Alpine, WSL2).

## 9. What you (consilium agent) must deliver

For each of Q1–Q4 above:
1. **Position** — one recommended answer.
2. **Why** — one paragraph, grounded in facts above (§1–§8), not generic wisdom.
3. **KPI** — one measurable metric that tells whether the decision was correct in 30–60 days.
4. **Biggest risk** — one concrete failure mode the other agents should stress-test.

No code. No "it depends — let me enumerate". Commit to a position. If genuinely undecidable, SAY SO and describe the data Dmitry would need to decide.
