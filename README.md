# Claude Booster

**Stop re-teaching Claude Code the same things every morning.**

Claude Code out of the box has no memory across sessions, no institutional learning, no cross-project knowledge transfer. By week three of daily use, you notice:

- You re-explain the stack, the conventions, the failure modes — every session.
- Claude reimplements a helper you already have, because it didn't grep first.
- Same clarifying questions, day after day. ("npm or pnpm?" — you answered yesterday.)
- A hook silently stopped firing and you discovered it 3 weeks later.
- Every new project starts at zero. Hard-won lessons from the old one don't transfer.

Claude Booster turns those sessions into a compounding asset. One `python install.py` on any Mac or Linux box and your Claude Code starts **remembering, learning, and auditing itself**.


**Kill date если нет положительного результата:** 2026-05-22.

---

## Three quality innovations

Claude Booster ships three mechanisms that address the three failure modes of LLM agents working on multi-session projects:

### 1. Paired Worker+Verifier — kills in-session self-evaluation bias

When Claude delegates a coding task, it spawns **two agents in parallel**: a Worker that implements the change and an independent Verifier that writes an executable acceptance test — without seeing the Worker's prompt or approach. The Lead runs the test and reads the exit code. PASS/FAIL is the test's verdict, never the Lead's judgment of the Worker's code.

**Why this matters:** Single-agent workflows suffer from self-evaluation bias — the same model that wrote the code reviews it and "confidently praises even mediocre work" (Anthropic). The Verifier breaks this loop by testing observable behavior, not implementation details.

**In practice:** Every implementation task in this session used paired verification. The Verifier caught a real bug (awk range pattern in handover format validation) that the Worker missed — exit code 1, classified as V-failure, test fixed, re-run → exit 0. This is the mechanism working as designed.

See `~/.claude/rules/paired-verification.md` for the full protocol: Artifact Contract, W/V/A/E failure classification, test legitimacy standard, skip criteria.

### 2. Temporal-causal 3D memory — kills cross-session stuck loops

Standard memory stores facts. Claude Booster's memory stores **causal chains**: what was tried → what happened → what was concluded → what's still open. The stuck-loop detector hashes normalized topic keywords across handovers and fires when the same problem reappears 3+ times without a `verify_gate=pass` resolution.

**Why this matters:** Without causality, each session re-discovers the same problem, proposes the same fix, and fails the same way — across days or weeks. The 3D structure (time × topic × outcome) lets `/start` detect this pattern and force a reframe (Q1–Q4 questions) before the session repeats the loop.

**In practice:** `rolling_memory.py start-context --stuck-check` surfaces candidates with hash, appearance count, and reframe questions. The session must answer Q1–Q4 or explicitly supersede the topic. Silently re-listing a stuck topic is blocked.

See `~/.claude/scripts/rolling_memory.py` for the hash algorithm and `~/.claude/rules/commands.md` (now `/start` command) for the stuck-loop discipline.

### 3. Smart model routing — right model for the right task

Claude Booster doesn't run every agent on the same model. The Lead routes each delegate to the right tier:

| Tier | Model | When |
|------|-------|------|
| Trivial | Haiku 4.5 | Grep, file lookup, path search — instant, lightweight |
| Coding | Sonnet 4.6 | Workers and Verifiers writing code, tests, configs (≥20 lines) |
| Medium | Sonnet 4.6 | Research, single-file review, routine audits |
| Hard | Opus 4.7 | Architecture, security review, consilium, deep debugging |

The **Lead** (orchestrator) stays on **Opus 4.7** — strongest model for synthesis, routing, and judgment. Optionally, with `/fast` toggle, the Lead runs on **Opus 4.6 fast output** (~2.5x faster tokens).

A typical paired task spawns 2 agents (Worker + Verifier) on Sonnet and 1 Explore agent on Haiku — all in parallel. The Lead orchestrates on Opus. Total wall-clock: 60–90 seconds for what would take 3–5 minutes with everything on one model.

**On Claude Max:** model routing (Haiku/Sonnet/Opus delegation) works out of the box within the subscription. **Fast mode is NOT included in the Max subscription** — it is billed as extra usage at $30/$150 per MTok from the first token, even if you have remaining plan usage. Enable with `/fast` only when speed justifies the cost.

**On API / pay-per-token plans:** model routing still works and actually *saves* money (Haiku and Sonnet are significantly cheaper than Opus). But you're paying per token, so budget accordingly. To disable routing and use a single model, remove the `[CRITICAL] Model routing` section from `~/.claude/rules/tool-strategy.md`.

---

## What's new in v1.3.0 — Command architecture + Supervisor UX

**Three problems this release solves:**

1. **`/supervise` naming conflict.** A third-party plugin intercepted the `/supervise` command prefix. Renamed to `/lead` — same supervisor engine, no collision. All rules, README, and delegate references updated.

2. **Long-prompt crash.** The supervisor passed prompts as CLI arguments (`args += [prompt]`), which broke on prompts >100KB with "chunk is longer than limit". Fix: prompt is now written to a tempfile and fed via stdin to the `claude` subprocess.

3. **No model routing for supervised workers.** `supervisor.py` had no `--model` flag — model was only configurable via `CLAUDE_BOOSTER_MODEL` env var. Now: `/lead --model claude-sonnet-4-6 "your task"` works directly.

**Additional changes:**

- **Coding/implementation tier** in `tool-strategy.md` — Worker agents that write code route to `model: "sonnet"` by default. For `/lead`, use `--model claude-sonnet-4-6` explicitly.
- **Command files extracted** from `rules/commands.md` — `/start`, `/handover`, `/consilium` are now standalone files in `commands/`. Saves ~3000 tokens per session (command instructions load on-demand, not always-on).
- **Template sync** — `templates/rules/tool-strategy.md` now matches the installed version (model routing section was previously missing).

### `/lead` command

```bash
# Same as old /supervise, new name:
/lead fix the bug in foo.py

# With explicit model:
/lead --model claude-sonnet-4-6 implement the feature from spec.md

# Inspect state:
/lead sessions
/lead status --session <id>
/lead decisions --session <id>
```

---

## What's new in v1.2.0 — Supervisor Agent

**The problem this release solves.** v1.1.0 gave Claude a phase state machine and hook-enforced gates. That's good for one worker. But once you hand a long-running task to Claude and step away for coffee, there's no second opinion watching what the worker actually does: did it try a `git push --force` at 2am? Did it silently retry the same failing tool call 40 times in a loop? Did it hit a `/.env` Read the policy should have caught? Stock Claude Code answers all three with "check the transcript tomorrow."

**What v1.2.0 changes.** The supervisor is a second Python process that spawns a `claude -p --output-format stream-json` worker, observes every tool invocation on the worker's stream, and **kills the worker the instant a policy violation or quota breach occurs**. Three layers:

1. **Permissive-blacklist policy engine** (`policy.py`). Worker is **trusted by default** — Bash/Edit/Write/Read/Grep/Glob all approve unless they hit one of 13 hard-deny Bash regexes (`git push --force`, `rm -rf /`, `kubectl delete`, `dd`, `mkfs`, …) or one of 11 hard-deny path substrings (`.env`, `id_rsa`, `/.aws/`, `/.ssh/`, `/.git/config`, …). Set `paranoid_mode: true` in `<repo>/.claude/supervisor.yaml` to flip the default back to whitelist-only for high-trust-boundary projects.
2. **Adaptive silence detector** (`detector.py`). `clamp(3 × median_event_gap, 20s, 180s)` with a 60s post-start grace. A hung or deadlocked worker gets cancelled automatically — no infinite stall, no infinite spend.
3. **Quota admission control** (`quota.py`). 15% supervisor reserve carved out of the 5-hour Max/Pro window. Circuit-breaker `CLOSED → HALF_OPEN (≥50% usage) → OPEN (≥85%)`. Pre-spawn admission check refuses workers that would blow the session cap.
4. **Auto-continuation on `max_turns`**. The Claude CLI has an internal turn limit (~25 turns per `-p` invocation). On long multi-file research or multi-step fixes, vanilla `claude -p` just stops mid-task with `error_max_turns`. The supervisor catches that, re-spawns the worker with `--resume <cli_session_id>` and a "continue where you left off" prompt, and stitches the chain together transparently — up to `max_continuations` (default 5) per session. Users write one prompt; the supervisor handles the chaining. Policy + quota + silence detection stay session-wide. Circuit-breaker still trips if cumulative spend blows the 5-hour budget.
5. **Autonomy directive + auto permission mode**. Every worker is spawned with `--permission-mode auto` (so permission prompts don't stall the subprocess) AND an `--append-system-prompt` that reads: *"Work fully autonomously. Do NOT ask the user clarifying questions or offer A/B choices. If you face a decision, pick the best path using reversibility + scope + risk and proceed."* This is the directive that converts `/lead` from "another Claude session that falls back to admin mode" into actual autonomous execution. Disable per-repo via `autonomy_directive: false` in supervisor.yaml if you want the worker to behave like a vanilla interactive session.

Enforcement is **authoritative, not advisory**: any `deny`, `escalate-without-escalator`, `loop-guard`, `blocked-by-quota` verdict triggers `SIGINT → SIGTERM → SIGKILL` on the worker and records the decision to `rolling_memory.db.supervisor_decisions` before the worker can finish the tool call.

### `/lead` command

```bash
# One-shot supervised worker session (prompt + cancel-on-violation).
# No `run` keyword, no quote marks needed — everything after the script
# path is the worker prompt:
python3 ~/.claude/scripts/supervisor/supervisor.py fix the bug in foo.py

# Recent decisions for a session:
python3 ~/.claude/scripts/supervisor/supervisor.py decisions --session <id> --limit 20

# Quota snapshot (5h window, circuit state, tokens spent):
python3 ~/.claude/scripts/supervisor/supervisor.py status --session <id>
```

Also available as `/lead <args>` from inside a Claude Code session — the slash command wraps the same CLI, and `install.py` auto-allowlists the bash pattern so you don't hit the auto-mode classifier on first try.

### Per-project config

`<repo>/.claude/supervisor.yaml` (parsed by a minimal flat-YAML reader; pre-materialise to `supervisor.json` if you need anything fancy):

```yaml
tier1_tools:
  - pytest
tier2_trusted_repo: false
estimated_tokens: 10000
```

### What lands in `rolling_memory.db`

Two new tables, both with `CHECK` constraints at the DB level:

| Table | What it holds |
|---|---|
| `supervisor_decisions` | One row per observed tool-use: `session_id`, `ts`, `tool`, `args_digest` (SHA-256 for loop detection), `decision ∈ {approve,escalate,deny}`, `tier`, `rationale`, `approved_by ∈ {regex,haiku,dmitry}` |
| `supervisor_quota` | One row per session: `session_id`, `started_at`, `window_end`, `supervisor_tokens`, `worker_tokens`, `circuit_state`, `updated_at` — survives supervisor restart |

### What's still v1.2.0-MVP (call-out for honesty)

- **No Haiku escalator wired by default** — `policy.escalate` + no escalator = default-deny + cancel. If you want Haiku-as-gatekeeper, implement the `HaikuEscalator` Protocol in `supervisor.py` and inject at `Supervisor(...)` construction; the system prompt already lives at `~/.claude/scripts/supervisor/prompts/supervisor_v1.md` (contract: JSON-only reply with `{"decision":"approve|deny", "rationale":"..."}`).
- **One worker per supervisor** — multi-worker session pooling is Session 5+.
- **End-to-end red-team against the real `claude-agent-sdk` worker binary is documented but not automated** — the 92-test unit/integration suite exercises the full chain via `FakeProc`, and Session 4's live smoke (hi → completed; `use bash ...` → cancelled + deny-decision persisted) proved the enforcement loop works against the real binary. A CI-pinned matrix across RT1–RT5 is the next roadmap item, not a ship-gate.

---

## What's new in v1.1.0 — Lead-Orchestrator workflow enforcement

**The problem this release solves.** v1.0 gave Claude *instructions* on how to work as a lead orchestrator: RECON first, plan second, verify before closing, never push unverified code. Those instructions are in `pipeline.md` and Claude reads them every session. It still skipped steps — because instructions without teeth decay into theater the moment a task gets urgent. "I'll just edit this one file" becomes a habit, plans never get written, tasks close without anyone running a single `curl`.

**What v1.1.0 changes.** The workflow is now enforced by the harness, not by Claude's memory. A six-phase state machine (`RECON → PLAN → IMPLEMENT → AUDIT → VERIFY → MERGE`) lives in `<project>/.claude/.phase`, visible in every prompt, and `PreToolUse` / `TaskCompleted` / `PreCompact` hooks **physically refuse** tool calls that violate the current phase:

- Try to `Edit` a `.py` file in `RECON`? Blocked with a message telling Claude to advance the phase first.
- Try to close a `TaskUpdate(status=completed)` without a `curl`, `pytest`, `SELECT ... N rows`, or DevTools inspection in the transcript? Blocked.
- Auto-compaction tries to fire mid-plan and summarize away the architecture discussion? Blocked.
- `git push --force`, `rm -rf /`, `kubectl delete`, `dd`, `mkfs`? Refused even with `bypassPermissions`.

Plus three Claude-4.7-specific env defaults that push back on the [effort-downgrade controversy](https://www.theregister.com/2026/04/13/claude_outage_quality_complaints/) shipped in Opus 4.6: `effortLevel: high`, `MAX_THINKING_TOKENS=12000`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80`.

Full lever map:

| Lever | Behaviour |
|---|---|
| `/phase` slash command + per-project `.claude/.phase` file | Six phases: `RECON → PLAN → IMPLEMENT → AUDIT → VERIFY → MERGE`. Transitions logged to `phase_transitions.log`. |
| `phase_gate.py` PreToolUse hook | Blocks `Edit`/`Write`/`NotebookEdit` on source code unless phase = `IMPLEMENT`. Docs / reports / tests / `*.md` still editable in any phase. |
| `phase_prompt_inject.py` UserPromptSubmit hook | Injects `[phase: X] <rule>` into every user prompt so Claude always sees the current gate. |
| `require_task.py` PreToolUse hook | Blocks code edits without an active `TaskCreate` — enforces plan-first discipline. |
| `require_evidence.py` TaskCompleted hook | Refuses to close a task without `curl`/`pytest`/`SELECT ... N rows`/DevTools output in recent transcript. Bypass via `docs:`/`chore:` task prefix. |
| `preserve_plan_context.py` PreCompact hook | Blocks auto-compaction while phase = `PLAN` so architectural discussion isn't summarized mid-design. |
| `permissions.deny` hardening | `git push --force`, `git reset --hard`, `rm -rf /`, `kubectl delete`, `docker system prune`, `dd`, `mkfs` refused even in `bypassPermissions` mode. |
| `effortLevel: high` + `MAX_THINKING_TOKENS=12000` | Counters the Claude 4.6→4.7 "effort downgrade" that shipped with medium-default adaptive thinking. |
| `ANTHROPIC_DEFAULT_OPUS_MODEL=claude-opus-4-7` | Pins Opus 4.7; session doesn't silently fall back to 4.6. |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80` | Compaction triggers at 80 % instead of the default ~95 % — planning context isn't lost at the edge. |

Escape hatches for legitimate exceptions: `CLAUDE_BOOSTER_SKIP_{TASK,PHASE,EVIDENCE,COMPACT}_GATE=1`.

---

## Before / After

| Daily scenario | Stock Claude Code | With Claude Booster |
|---|---|---|
| **New session starts** | Reads `CLAUDE.md`, asks what changed since yesterday | `/start` auto-loads last session's decisions + relevant prior consiliums/audits — scoped to the current project, biased by category |
| **Finished a hard debugging session** | Wisdom evaporates when you close the laptop | `/handover` captures decisions, Goal+KPI, Required reading list, and session transcript reference. Next session reads exactly what it must before touching code |
| **Moving to a new project** | Zero context carry-over | FTS5 cross-project search surfaces relevant lessons from every other project you've worked on |
| **"Which approach do you want?"** | Claude asks, you tie-break, lose a round-trip | **51% Rule**: Claude acts on best guess, states the assumption in one line, you course-correct only if wrong |
| **Hook silently broken** | Discovered 3 weeks later when something "feels weird" | `check_rules_loaded.py` canary + `telemetry_agent_health.py` surface 5 anti-theater signals every `/start` |
| **Architectural decision** | Lost in terminal scrollback | `consilium` spawns 3–5 bio-specific agents + GPT via PAL MCP, auto-saves to `reports/`, auto-indexed for retrieval |
| **"Did I run the tests?"** | Honor system | `verify_gate.py` PreToolUse hook blocks handover commits without an evidence JSON block |
| **Hand-off between sessions** | "read the chat log" | Structured `handover` with Goal+KPI (north star + measurable KPI), Required reading (files the next session must read before acting), Session reference (JSONL transcript path for RECON), verify-gate evidence |
| **Next session starts blind on goal** | North star and KPI only exist in your head | `## Goal + KPI` section in every handover — north star + current milestone + KPI, carried forward or updated each session |
| **Next session reads wrong files first** | No mandatory context list | `## Required reading` section — bulleted list of files with reasons; `/start` reads them before anything else |
| **"What did we actually try last time?"** | Buried in terminal history, gone by morning | `## Session reference` in handover — UUID + JSONL path; grep the transcript during RECON to understand what failed and why |
| **`CLAUDE.md` bloated to 500 lines** | Everything loaded on every prompt | 11 scoped rules — `paths:` filtering, description-gated loading, always-on kept minimal |
| **Claude re-implements existing code** | No recon-before-code rule | `core.md` enforces Grep-first; auto-consilium fires on high-risk edits |
| **Same bug class hits you 3 times** | Fix → forget → repeat | Error-taxonomy classifier promotes recurring patterns into `institutional.md` as permanent rules |
| **Agent writes code, Lead says "looks good"** | Self-evaluation bias — Lead authored the brief, naturally sees the result as matching | Paired Worker+Verifier: independent acceptance test with executable exit code. Lead runs the test, doesn't read the code to judge |
| **Same bug resurfaces every 3 sessions** | No causal memory — each session re-discovers and re-proposes the same fix | Temporal-causal 3D memory: stuck-loop detector hashes topics across handovers, forces reframe (Q1–Q4) when pattern detected |
| **Every agent runs on Opus, session takes 10 min** | No model routing — all delegates inherit the Lead's expensive model | 4-tier routing: Haiku for lookups, Sonnet for coding, Opus only for architecture. 2-4x faster, 3-5x cheaper per delegation |

---

## Pain → Fix map

| Pain | Root cause | Booster fix |
|------|-----------|-------------|
| Claude forgets everything between sessions | No persistent memory layer | `rolling_memory.db` (SQLite + FTS5), ~1900-LOC memory engine, SessionStart hook injects relevant context under a token budget |
| Every project starts at zero | No cross-project knowledge transfer | `/start` pulls cross-project consilium/audit rows, category-biased ORDER BY, topic-driven FTS5 search |
| Clarifying-question spam | No confidence threshold | `core.md` 51% Rule — act on best guess, state assumption in one line |
| `CLAUDE.md` monolith | One big file loaded always | 11 scoped files in `~/.claude/rules/` — frontmatter `paths:` or `description:` gating |
| Decisions lost | No structured save | `consilium` / `audit` / `handover` protocol, auto-indexed for retrieval |
| Hooks broken silently | No self-check | `check_rules_loaded.py` canary + 5-signal agent-health telemetry |
| "Fake evidence" in commits | No verification gate | `verify_gate.py` PreToolUse hook — blocks handover commits without real curl/SQL/HTTP evidence markers |
| Session ends, notes scattered | No handover contract | `/handover` auto-collects git log + roadmap delta; requires Goal+KPI, Required reading, Session reference — structured report that next session can act on, not just read |
| Next session drifts off the goal | KPI only lives in the current session | `## Goal + KPI` in handover is persistent — copy-forward each session, update only when milestone changes; goal survives context resets |
| Post-mortem impossible: "what did we try?" | Session transcript unreachable | `## Session reference` links the JSONL transcript; RECON agent can grep it for tried approaches, failure modes, rejected alternatives |
| Personal install breaks on new machine | Manual copy of `~/.claude/` | `install.py` — one command, atomic, idempotent, safe by default |
| Worker loops on a failing tool call at 2am, burns quota | No watchdog | v1.2.0 Supervisor Agent — `policy.py` + `detector.py` + `quota.py`, SIGINT-ladder-cancels worker on deny / silence / quota breach |
| Agent self-evaluates its own work | Same model writes and reviews — bias | Paired Worker+Verifier: independent executable acceptance test, exit code = verdict |
| Same problem loops across sessions | No causal chains in memory | Temporal-causal 3D memory + stuck-loop detector, hash-based recurrence detection |
| Slow agents burn Opus budget | All delegates on Opus 4.7 | 4-tier model routing (Haiku/Sonnet/Opus) + `/fast` mode for coding agents |

---

## 60-second quickstart

```bash
git clone https://github.com/demetrius2017/claude-booster
cd claude-booster
python3 install.py --dry-run                                   # preview every change
python3 install.py --yes --name "Your Name" --email "you@example.com"
```

That's it. Your next Claude Code session reads the new `~/.claude/rules/`, the memory engine boots, hooks wire themselves in. Zero config files to edit by hand.

To try the v1.2.0 supervisor on a real worker:
```bash
python3 ~/.claude/scripts/supervisor/supervisor.py your prompt here
```
or from inside a Claude Code session: `/lead your prompt here` (no `run`, no quotes needed). Decisions land in `~/.claude/rolling_memory.db` (`supervisor_decisions` + `supervisor_quota` tables), stderr in `~/.claude/logs/supervisor/worker_*.stderr.log`.

**Prerequisite for `/lead`**: the `claude` CLI must be on PATH. The installer warns if it isn't, but the rest of Booster (memory, phase machine, rules, `/start`/`/handover`/`/consilium`) works without it.

**Staying up-to-date.** `install.py` records the source repo's `repo_path` / `git_sha` / `git_branch` into the manifest. A SessionStart hook (`check_booster_update.py`) runs on every Claude Code session start: it `git fetch`es the booster repo and, if origin is ahead, injects an `additionalContext` notice telling Claude "N commits behind, run `cd <repo> && python3 install.py --yes` to update". For fully-autonomous updates, export `CLAUDE_BOOSTER_AUTO_UPDATE=1` — the hook runs the installer itself and reports the outcome. Offline / no git / tar-extracted install = silent no-op.

Supported: **macOS (Apple Silicon + Intel) · Ubuntu · Debian · Fedora · Arch · Alpine · WSL2**. Native Windows, WSL1, Snap/Flatpak-sandboxed Claude Code, and `~/.claude/` on a network filesystem are **refused at preflight with actionable errors** — no silent misinstalls.

---

## What you actually get

Under `~/.claude/`:

| Path | Content |
|------|---------|
| `rules/*.md` | 11 rule files — anti-loop, tool strategy, pipeline phases, deploy procedures, frontend debug pipeline, institutional knowledge, error taxonomy, canary for rule-load detection, communication-style ("professor" tone), quality/Three-Nos, paired-verification |
| `scripts/*.py` | 19 Python hook scripts — memory engine + session hooks (`rolling_memory.py`, `memory_session_start.py`/`_end.py`/`_post_tool.py`), evidence gates (`verify_gate.py`, `require_evidence.py`), phase machine (`phase.py`, `phase_gate.py`, `phase_prompt_inject.py`, `preserve_plan_context.py`), plan-first enforcer (`require_task.py`), approval-baseline counter (`approval_counter.py`), observability (`telemetry_agent_health.py`, `check_rules_loaded.py`, `check_review_ages.py`), infra (`index_reports.py`, `backup_rolling_memory.py`, `add_frontmatter.py`, `instructions_loaded_log.py`) |
| `scripts/supervisor/` | v1.2.0 Supervisor Agent — 8 modules (`supervisor.py` CLI + orchestration, `policy.py` Tier 0/1/2 engine, `quota.py` admission + circuit-breaker, `detector.py` adaptive-silence FSM, `stream_json_adapter.py` Path A runtime, `persistence.py` sqlite writers, `runtime.py` transport Protocol, `schema.sql`) + `prompts/supervisor_v1.md` Haiku escalation contract |
| `commands/*.md` | 9 slash commands: `/start`, `/handover`, `/consilium`, `/lead`, `/update`, `/phase`, `/delegate`, `/verify-after-edit`, `/verify-flow` |
| `agents/*.md`, `*.json` | Agent team protocols — lifecycle, ownership schema, worktree safety, readiness gates, roadmap convention |
| `settings.json` | Hooks wired to Claude Code, **merged** into any existing config |
| `.booster-manifest.json` | Installer metadata — SHA-256 per file, version, for idempotency and selective rollback |
| `.booster-config.json` | Your git author identity (used for rule-template substitution) |
| `backups/booster_install_*.tar.gz` | Rollback tarball captured before any mutation |

### Slash commands

All commands are on-demand — their instructions load only when you invoke them, saving ~3000 tokens per session compared to the pre-v1.3.0 monolithic approach.

| Command | What it does |
|---------|-------------|
| `/start` | Initialize a session: read README, last handover, knowledge base (FTS5 cross-project search), telemetry, canary check, stuck-loop detection. Ends with `EnterPlanMode`. |
| `/handover` | End-of-session report: auto-collects git log, saves structured report with Goal+KPI, Required reading, Session reference, verify-gate evidence block. |
| `/consilium` | Multi-agent debate: RECON first (code, not reports), spawn 3–5 bio-specific agents + GPT via PAL MCP, synthesize positions, save to `reports/`. Also handles `/audit`. |
| `/lead` | Supervised worker: spawns a `claude -p` subprocess under policy gating (Tier 0/1/2 deny-list), quota circuit-breaker, adaptive silence detection. Replaces old `/supervise`. |
| `/update` | Mid-session auto-update: `git pull --ff-only` + `install.py --yes`. Rules and commands hot-reload immediately. Dirty tree = abort. |
| `/phase` | Show or set workflow phase (`RECON → PLAN → IMPLEMENT → AUDIT → VERIFY → MERGE`). |
| `/delegate` | Inspect the delegate-gate budget (Lead must delegate, not do inline work). |
| `/verify-after-edit` | Post-edit UI verification via Chrome DevTools. |
| `/verify-flow` | End-to-end UI flow verification. |

### Speed & model routing

See [Three quality innovations → Smart model routing](#3-smart-model-routing--fast-agents-without-extra-cost-on-max) above for the full breakdown. Quick reference:

| Tier | Model | Use case |
|------|-------|----------|
| Trivial | Haiku 4.5 | Grep, file lookup, path search, simple regex |
| Coding / Medium | Sonnet 4.6 | Code generation, research, test writing, reviews |
| Hard | Opus 4.7 | Architecture, security review, deep debugging, consilium |

For supervised workers (`/lead`), pass `--model` explicitly:
```bash
/lead --model claude-sonnet-4-6 implement the feature from spec.md
```

**Claude Max:** model routing (Haiku/Sonnet/Opus) is included in the subscription. **Fast mode is extra usage — $30/$150 MTok, billed separately.** Enable with `/fast` when needed. **API plans:** model routing saves money (cheaper models for delegates), but budget total token spend.

---

## Safety contract

The installer is **conservative by default**. It explicitly protects:

- **NEVER touched**: `rolling_memory.db` (your memory), `history.jsonl`, `.credentials.json` (Claude Code OAuth), `projects/`, `plugins/`, `cache/`, `sessions/`, `file-history/`, `logs/`, `paste-cache/`, `image-cache/`, `chrome/`, `ide/`, `debug/`, `plans/`, `downloads/`, `scheduled-tasks/`, `backups/`, `session-env/`.
- **Atomic writes**: every file via tmp + `fsync` (+ `F_FULLFSYNC` on Darwin) + `os.replace`. No partial state possible.
- **User-modified files preserved**: if your existing `rules/*.md` or scripts differ from the shipped template AND weren't written by a prior Booster install, they are preserved. Pass `--force` to overwrite.
- **Backup before any write**: staged in `$TMPDIR`, finalized to `~/.claude/backups/booster_install_<UTC>.tar.gz` after a successful install. Restore with:
  ```bash
  tar xzf ~/.claude/backups/booster_install_*.tar.gz -C ~/
  ```
  Selective restore (e.g. only rules):
  ```bash
  tar xzf ~/.claude/backups/booster_install_*.tar.gz -C ~/ .claude/rules
  ```
  If an install failed mid-flight and the final copy never ran, the backup is still at `$TMPDIR/booster_install_<UTC>.tar.gz` (macOS: `/var/folders/.../T/`; Linux: `/tmp/`).
- **`settings.json` merged by namespace**: installer owns only entries tagged `"source": "booster@<version>"` + the top-level `_booster` key. Your `permissions.allow`, `additionalDirectories`, `enabledPlugins`, `env`, `mcpServers` (incl. auth tokens) are preserved verbatim.
- **Secrets redacted in `--dry-run`**: diff shows `***REDACTED***` for any key matching `token|key|secret|password`.
- **Interrupt-safe**: Ctrl+C triggers rollback from the backup tarball, exits 130.

---

## CLI

```
python3 install.py [flags]

--dry-run        Preview changes. No writes.
--yes            Skip confirmation prompt (non-interactive).
--force          Overwrite user-modified files.
--name NAME      Git author name (substituted into rule templates).
--email EMAIL    Git author email.
--version        Print version and exit.
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | success / dry-run OK |
| 10 | Python < 3.8 |
| 11 | `~/.claude/` not writable |
| 12 | Downgrade attempt (manifest newer than installer) |
| 13 | Native Windows / Cygwin / MSYS2 / MinGW / WSL1 |
| 14 | Sandboxed Claude Code (Snap / Flatpak) |
| 15 | `~/.claude/` is on a network filesystem (NFS / CIFS / SMB / sshfs) |
| 16 | Python sqlite3 lacks FTS5 support |
| 20 | Backup failed |
| 30 | Write failed (rolled back) |
| 40 | `settings.json` merge failed (rolled back) |
| 130 | User interrupted (rolled back) |

---

## How it actually works

**Memory engine.** `rolling_memory.py` is a SQLite + FTS5 store with a typed schema (`directive`, `feedback`, `project_context`, `consilium`, `audit`, `error_lesson`, ...), preserve flags, per-project scope, and age-based consolidation. The `SessionStart` hook injects a token-budgeted slice of relevant rows into the conversation. `/start` surfaces cross-project rows via FTS5 with category-biased ranking.

**Rule loading.** Claude Code auto-loads `~/.claude/rules/*.md`. Each file has frontmatter: `paths:` globs for conditional loading (e.g. `*.tsx` files load `frontend-debug.md` only), `description:` for gated loading, or no gate for always-on. Result: 10× less bloat than a monolithic `CLAUDE.md`.

**Session lifecycle.**
- **SessionStart** hook: budgeted memory injection.
- **UserPromptSubmit** hook: clipboard image detection + shortcuts.
- **PreToolUse** on Bash: `verify_gate.py` scans the last 200 transcript lines for an evidence JSON block before allowing `git commit` on handover files.
- **PostToolUse**: batches events into `memory_batch_<session>.jsonl` for the session-end extractor.
- **Stop**: 3-question smart extraction + error-lesson classification (11-slug taxonomy) → promotes recurring patterns into `institutional.md`.

**Auto-consilium.** `core.md` defines HIGH risk as "change hits 2+ of: production data, auth/security, infrastructure, multi-service, financial logic, irreversible side effects". When triggered, Claude spawns 3-5 bio-specific agents (architect, security, devops, product, ...) + GPT via PAL MCP, synthesizes positions, saves to `reports/consilium_*.md`. Index picks it up.

**Verify-gate.** PreToolUse-blocks handover commits unless the last 200 lines contain `{"verified": {"status": "pass"|"na", "evidence": [...]}}`. Accepts markers: `curl`, `psql`, `sqlite3`, `HTTP/`, `docker`, `kubectl`, `DevTools`, `pytest`, `exit=<N>`. Rejects fake-evidence patterns: `localhost`, `|| true`, `curl -s` without `--fail`.

---

## Idempotency

Running `install.py` twice = zero writes the second time. Files are compared post-substitution against SHA-256 of what the installer *would* write. `--dry-run` after a successful install shows an empty plan.

---

## Customization at install time

`{{GIT_AUTHOR_NAME}}` and `{{GIT_AUTHOR_EMAIL}}` placeholders in rule templates are replaced at install time with the values you pass via `--name/--email` (or prompt, or read from `git config --global`).

Hook commands in `settings.json` are pinned to absolute paths: `${CLAUDE_HOME}` → your `~/.claude/`, `${PYTHON}` → `shutil.which("python3")` (stable through Homebrew / apt / pyenv version changes). No runtime shell-var resolution, no broken hooks after `brew upgrade python`.

---

## What's NOT shipped (on purpose)

- Your `rolling_memory.db` — per-user, bootstraps empty on first use.
- Your consilium/audit reports — those live in each project's `reports/`.
- Per-project `~/.claude/projects/*/memory/` markdown — per-project, per-user.
- `pyyaml` — only `scripts/index_reports.py` uses it. `pip install -r requirements.txt` if you use `/start` cross-project indexing.

---

## Project layout

```
claude-booster/
├── install.py                # stdlib-only installer (~900 LOC)
├── requirements.txt          # pyyaml (runtime dep for index_reports.py)
├── .gitignore                # excludes all per-user runtime data
├── templates/
│   ├── rules/                # 10 .md files
│   ├── scripts/              # 12 .py files
│   ├── commands/             # 2 slash commands
│   ├── agents/               # 5 protocol files + 2 JSON schemas
│   └── settings.json.template
├── docs/
│   ├── audit_fix_validation.md
│   └── audit_secrets_scan.md
└── README.md
```

---

## Design decisions

Key tradeoffs:

- **Python-stdlib only** for the installer. No pip at install time.
- **Namespaced `settings.json` merge** via `source: "booster@<ver>"` tags — not deep merge. User's hooks, MCP servers with auth tokens, and permission lists survive untouched.
- **DB migration punted**: `rolling_memory.py` auto-initializes an empty v5 DB on first call. Migration across Booster versions on the same machine is deferred to v2.
- **Windows deferred to v2**: `fcntl`, case-sensitivity, cmd-dispatched hooks, JSON backslash escaping, and MAX_PATH all need separate handling.
- **Audit trail**: `docs/audit_fix_validation.md` and `docs/audit_secrets_scan.md` document the 2 independent reviews this release went through.

---

## Known caveats

**Supported:**
- macOS (Apple Silicon + Intel) with Homebrew Python 3.8+
- Ubuntu / Debian / Fedora / Arch / Alpine with system or apt/dnf/pacman Python 3.8+
- WSL2 — with the Desktop caveat below

**Refused at preflight (with actionable error):**
- Native Windows, Cygwin, MSYS2, MinGW (exit 13) — use WSL2
- WSL1 (exit 13) — drvfs corrupts SQLite WAL; upgrade via `wsl --set-version <distro> 2`
- Snap / Flatpak sandboxed Claude Code (exit 14) — app-HOME differs from `$HOME`
- `~/.claude/` on NFS / CIFS / SMB / sshfs / 9p (exit 15) — SQLite WAL forbidden
- Python sqlite3 without FTS5 (exit 16) — install Homebrew/apt/dnf Python

**Known caveats (not blocked; user must understand):**

1. **WSL2 + Claude Code Desktop on Windows host**: Desktop reads `%USERPROFILE%\.claude` on Windows, NOT the WSL home. Install on the side where Claude Code actually runs. Installer warns at preflight.
2. **`brew upgrade python`**: the resolved `python3` path from `shutil.which()` survives minor upgrades (Homebrew keeps a stable symlink). If you switch Python major versions or uninstall the symlinked version, re-run `install.py --yes`.
3. **NixOS**: `/usr/bin/env python3` is used via PATH — a `nixos-rebuild switch` that drops your Python derivation will break hooks; re-run install.
4. **Intel → Apple Silicon Mac migration**: paths differ (`/usr/local/bin/python3` vs `/opt/homebrew/bin/python3`); re-run install after migration.
5. **Devcontainers**: `~/.claude/` is wiped on rebuild unless mounted as a volume. Add `source=~/.claude,target=/root/.claude,type=bind` to `devcontainer.json`.
6. **External drive unmount mid-install**: the backup is staged in `$TMPDIR` (local tmpfs), so rollback still works even if `~/.claude/` lives on a drive that disappears.
7. **FileVault + power-loss**: on macOS we additionally call `F_FULLFSYNC` for each atomic write (platter flush, not just OS buffer) — reduces but does not eliminate the corruption window.

**Recently fixed:**

- **`/start` no longer triggers a zsh `nomatch` cascade in projects without `roadmap.html`/`roadmap.md`** (2026-04-25). On macOS Claude Code defaults to zsh, where `nomatch` is on: a glob like `ls roadmap.* 2>/dev/null` aborts at parse time **before** the redirect applies, so `2>/dev/null` cannot suppress it. Compounding this, the Claude Code harness cancels **every sibling tool call** in a parallel-tool-call block when any one exits non-zero — so one stray glob in `/start` recon could void the rules-canary, telemetry, tag-hygiene, and rolling-memory probes in a single shot. Fix: `templates/rules/commands.md` now instructs Claude to use the `Read` tool for `roadmap.{html,md}` existence probes (clean per-tool error, no sibling cancellation), and `templates/rules/core.md` ships a new `# [CRITICAL] Shell hygiene` section with `(N)` qualifier + explicit-enumeration patterns for unavoidable Bash globs. New installs pick this up automatically; existing installs need `python3 install.py --yes` to re-apply.
- **`delegate_gate` no longer treats `$HOME` as a project root** (2026-04-25). Previously, launching Claude from the home directory (or any non-project dir) caused `project_root_from()` to match `~/.claude/` (the global config dir) as a project marker. The delegate-budget counter was then written to `~/.claude/.delegate_counter` and shared across every non-project session, so the very first `Bash`/`Edit` call could be blocked with "budget exhausted (2/1)". Fix: `_gate_common.project_root_from()` now excludes the `~/.claude/` marker when the candidate path equals `Path.home()` (a real `.git/` at HOME is still respected); `delegate_gate.main()` adds a defense-in-depth early-exit when `root == Path.home()`, logging `decision=allow / reason="no project context"`.

**Out of scope (v2):**
- Native Windows support (requires `fcntl`→`msvcrt`, cmd-dispatched hooks, case-insensitive FS handling, `\\?\` long paths).
- `uninstall.py` (use manifest to selectively revert).
- Interactive `settings.json` conflict resolver.
- `booster doctor` diagnostic command.

---

## License

MIT.
