---
description: "Commands: /start, /handover, /consilium, /audit. Git config, docstring policy, agent teams. Loaded when user invokes these commands or needs git/deploy config."
---

# [CRITICAL] Git Configuration
`user.name={{GIT_AUTHOR_NAME}}`, `email={{GIT_AUTHOR_EMAIL}}`
Vercel only deploys commits from this author. Without this config, deploy will fail.

# Docstring Policy (Python)
Every Python file — up-to-date module docstring: Purpose, Contract (inputs/outputs), CLI/Examples, Limitations, ENV/Files.

# Commands

## start
1. Read `README`, `roadmap.html` (or `.md`). For the latest `reports/handover_*.md`, read ONLY the `## Summary` and the "first step tomorrow" / "First step" section (Russian or English) — use `Read` with `offset`/`limit` narrow slices, not the whole file. If those sections cite a specific file needed for today's task, `Read` that too. Only read the full handover if you cannot locate the needed context from the two sections. (Saves ~5,000 tokens per /start — per `reports/audit_2026-04-18_startup_token_budget.md` R2.) If `docs/` or `doc/` folder exists — read key files (architecture, API, setup, conventions).
2. **[CRITICAL] Review existing knowledge base — cross-project, category-biased:**
   - Run: `python ~/.claude/scripts/rolling_memory.py start-context --scope "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"` — lists indexed consilium/audit rows. The git-toplevel resolution ensures the project category is correct even when Claude is launched from a subdirectory; the helper also walks ancestors to find the indexed project root, so a non-git project still resolves correctly. Lines marked `*` are this project's own; `-` are cross-project rows that may still be relevant. Each line has the source path — `Read` the ones relevant to the current task.
   - Topic-driven: same command + `--query "<keywords>"` — FTS5 search across the same corpus, current-project hits ranked first.
   - Extract from each read: decisions made, rejected alternatives, identified risks, recommendations.
   - Factor these decisions into the plan — do not revisit what was already justified unless context has changed.
   - **If `start-context` returns "(no consilium/audit reports indexed...)"** or "DB not initialized": run `python ~/.claude/scripts/index_reports.py` once, then retry. Indexer is idempotent.
   - **Tag hygiene check**: `python ~/.claude/scripts/check_review_ages.py` — surfaces `[UNDER REVIEW]` tags that have passed their `resolve by` date or are missing canonical structure. Exit 0 = clean; exit 1 = at least one OVERDUE/MALFORMED line on stdout. Any finding must be resolved (retag `[SUPERSEDED by ...]` with replacement rule, restore to `[ACTIVE]`, or delete) before the session's main task — overdue tags are the T2.3 supersession mechanism rotting, per scenario #4.
   - **Rules-load canary**: `python ~/.claude/scripts/check_rules_loaded.py` — echoes the expected canary token. **Cross-check**: search the current session's loaded instructions for the same token. If the file is OK on disk but the token is not visible in Claude's system-reminder / loaded-rules, the `~/.claude/rules/` auto-load mechanism is broken (likely hook/upgrade regression, scenario #7). Surface the mismatch to the user before proceeding with the session's main task.
   - **Agent-health telemetry**: `python ~/.claude/scripts/telemetry_agent_health.py` — prints 5 anti-theater signals (evidence density, N/A ratio, overdue [UNDER REVIEW] tags, stale citations, session cadence). Informational — always exits 0. `⚠` markers surface regressions that should be acknowledged in the planning step. Reference at least one signal value in handover if any is non-✓. JSON mode available: `--json` for hooks/scripts. Reconsider MCP 2026-06-18 if agent starts parsing prose or needs cross-session queries the script can't answer.
3. `EnterPlanMode` → summary report + action plan (informed by prior reports) → the user's approval → `ExitPlanMode`

## handover
Auto-collect: `git log --oneline --since="8 hours ago"` + `roadmap.html` (what moved to DONE).
Save `reports/handover_YYYY-MM-DD_HHMMSS.md`: summary, tools used, first step tomorrow (copy-paste command), problems/solutions. Update roadmap. Git add + commit + push.

**[CRITICAL] Verify-gate JSON block — required before `git add`/`git commit` of the handover file.**
Before running `git add reports/handover_*.md` or `git commit … reports/handover_*.md`, emit one of these as an assistant text block (the PreToolUse hook `verify_gate.py` scans the last 200 transcript lines for it):

```json
{"verified": {"status": "pass", "evidence": ["<strong-evidence-1>", "<strong-evidence-2>"], "reason_na": null}}
```

or, for docs-only sessions:

```json
{"verified": {"status": "na", "evidence": [], "reason_na": "<why no runtime change to verify>"}}
```

Strong evidence must include a recognised marker (`curl`, `wget`, `psql`, `sqlite3`, `SELECT`, `PRAGMA`, `HTTP/`, `docker`, `kubectl`, `DevTools`, `pytest`, `exit=<N>`) AND:
- for HTTP/curl/wget: a 1xx-5xx status code in the same entry;
- for SQL/DB: a rowcount or `N rows` marker.

Automatically rejected (fake-evidence patterns):
- `localhost` / `127.0.0.1` as target — must be a real staging/prod URL;
- `|| true` — swallows failures;
- `curl -s` without `--fail` / `-o` / `| tee` — suppresses both exit code and body.

`status='na'` is allowed only when `git diff --cached --name-only` touches exclusively allowlisted paths: `docs/`, `reports/`, `audits/`, `.claude/`, `tests/`, `*.md`, `*.txt`, `README*`. Any Python/TypeScript/Dockerfile/YAML change requires `status='pass'` with evidence.

Per-project control via `.claude/CLAUDE.md` YAML frontmatter key `verify_gate: enforcing|warn|off`:
- `enforcing` — hook blocks the commit (exit 2);
- `warn` — hook logs to stderr and `~/.claude/logs/verify_gate_decisions.jsonl` but does not block;
- `off` — hook is a no-op (default for projects without `deploy/` or `.github/workflows/`).

Decisions log (every fire): `~/.claude/logs/verify_gate_decisions.jsonl`. Review weekly — projects silently stuck on `off` are visible there.

## consilium / audit
1. **[CRITICAL] RECON before opinions — verify current state against code, not memory:**
   - Spawn Explore agents to read actual code/configs relevant to the topic (Grep for key functions, Read configs, check deploy state)
   - Cross-reference findings with reports/memory — flag discrepancies ("report says X, code shows Y")
   - Build a **Verified Facts Brief**: what exists now, what works, what doesn't — with file paths and evidence
   - Present brief to the user before proceeding. If facts contradict the premise — reframe the question
   - **Never brief consilium agents from reports alone. Reports decay. Code is truth.**
2. Spawn 3-5 agents with different Bios (architect, security, product, devops, data engineer — task-specific). **Each agent receives the Verified Facts Brief, not raw report excerpts.**
3. Each independently: analysis, KPIs, decision
4. **[MANDATORY] GPT as external expert:** use PAL MCP for independent opinion:
   - `mcp__pal__ask` — request GPT analysis/opinion on a specific question
   - `mcp__pal__thinkdeep` — deep GPT reasoning on architectural decisions
   - `mcp__pal__consensus` — Claude vs GPT debate for controversial decisions
   - `mcp__pal__second_opinion` — GPT second opinion on a finished Claude solution
   - `mcp__pal__codereview` — code review via GPT
5. Lead: synthesis + table "agent / position / key insight / KPI" (including GPT agent)
6. **[CRITICAL] Save results to file:**
   - Consilium → `reports/consilium_YYYY-MM-DD_<topic>.md`
   - Audit → `reports/audit_YYYY-MM-DD_<topic>.md`
   - Format: title, task context, agent positions (table), decision made, rejected alternatives with reasons, risks, implementation recommendations.
   - Git add + commit. These reports are the project's knowledge base, read during `start`.

# Agent Teams & Worktree
Rules: `~/.claude/agents/protocol.md` (ownership, gates, state).
Frontend tasks: UI acceptance from the user before merge. Use `/frontend-design` for UI tasks.

**Worktree Safety:** stay in worktree dir, integrate only via `git merge` / `gh pr create` / `git cherry-pick`. Before commit: `pwd` + `git branch --show-current`. Full: `~/.claude/agents/worktree_rules.md`.
