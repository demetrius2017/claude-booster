# Claude Booster

**Stop re-teaching Claude Code the same things every morning.**

Claude Code out of the box has no memory across sessions, no institutional learning, no cross-project knowledge transfer. By week three of daily use, you notice:

- You re-explain the stack, the conventions, the failure modes — every session.
- Claude reimplements a helper you already have, because it didn't grep first.
- Same clarifying questions, day after day. ("npm or pnpm?" — you answered yesterday.)
- A hook silently stopped firing and you discovered it 3 weeks later.
- Every new project starts at zero. Hard-won lessons from the old one don't transfer.

Claude Booster turns those sessions into a compounding asset. One `python install.py` on any Mac or Linux box and your Claude Code starts **remembering, learning, and auditing itself**.

---

## What's new in v1.1.0 — Lead-Orchestrator workflow enforcement

Phase machine and hard gates that make it **physically impossible** to skip planning or merge unverified code.

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
| **Finished a hard debugging session** | Wisdom evaporates when you close the laptop | `/handover` captures decisions + next-step command. Next session picks up exactly where you left off |
| **Moving to a new project** | Zero context carry-over | FTS5 cross-project search surfaces relevant lessons from every other project you've worked on |
| **"Which approach do you want?"** | Claude asks, you tie-break, lose a round-trip | **51% Rule**: Claude acts on best guess, states the assumption in one line, you course-correct only if wrong |
| **Hook silently broken** | Discovered 3 weeks later when something "feels weird" | `check_rules_loaded.py` canary + `telemetry_agent_health.py` surface 5 anti-theater signals every `/start` |
| **Architectural decision** | Lost in terminal scrollback | `consilium` spawns 3–5 bio-specific agents + GPT via PAL MCP, auto-saves to `reports/`, auto-indexed for retrieval |
| **"Did I run the tests?"** | Honor system | `verify_gate.py` PreToolUse hook blocks handover commits without an evidence JSON block |
| **Hand-off between sessions** | "read the chat log" | Structured `handover` protocol with verify-gate evidence + first-step-tomorrow command |
| **`CLAUDE.md` bloated to 500 lines** | Everything loaded on every prompt | 9 scoped rules — `paths:` filtering, description-gated loading, always-on kept minimal |
| **Claude re-implements existing code** | No recon-before-code rule | `core.md` enforces Grep-first; auto-consilium fires on high-risk edits |
| **Same bug class hits you 3 times** | Fix → forget → repeat | Error-taxonomy classifier promotes recurring patterns into `institutional.md` as permanent rules |

---

## Pain → Fix map

| Pain | Root cause | Booster fix |
|------|-----------|-------------|
| Claude forgets everything between sessions | No persistent memory layer | `rolling_memory.db` (SQLite + FTS5), ~1900-LOC memory engine, SessionStart hook injects relevant context under a token budget |
| Every project starts at zero | No cross-project knowledge transfer | `/start` pulls cross-project consilium/audit rows, category-biased ORDER BY, topic-driven FTS5 search |
| Clarifying-question spam | No confidence threshold | `core.md` 51% Rule — act on best guess, state assumption in one line |
| `CLAUDE.md` monolith | One big file loaded always | 9 scoped files in `~/.claude/rules/` — frontmatter `paths:` or `description:` gating |
| Decisions lost | No structured save | `consilium` / `audit` / `handover` protocol, auto-indexed for retrieval |
| Hooks broken silently | No self-check | `check_rules_loaded.py` canary + 5-signal agent-health telemetry |
| "Fake evidence" in commits | No verification gate | `verify_gate.py` PreToolUse hook — blocks handover commits without real curl/SQL/HTTP evidence markers |
| Session ends, notes scattered | No handover contract | `/handover` auto-collects git log + roadmap delta, formats structured report with evidence block |
| Personal install breaks on new machine | Manual copy of `~/.claude/` | `install.py` — one command, atomic, idempotent, safe by default |

---

## 60-second quickstart

```bash
git clone https://github.com/demetrius2017/claude-booster
cd claude-booster
python3 install.py --dry-run                                   # preview every change
python3 install.py --yes --name "Your Name" --email "you@example.com"
```

That's it. Your next Claude Code session reads the new `~/.claude/rules/`, the memory engine boots, hooks wire themselves in. Zero config files to edit by hand.

Supported: **macOS (Apple Silicon + Intel) · Ubuntu · Debian · Fedora · Arch · Alpine · WSL2**. Native Windows, WSL1, Snap/Flatpak-sandboxed Claude Code, and `~/.claude/` on a network filesystem are **refused at preflight with actionable errors** — no silent misinstalls.

---

## What you actually get

Under `~/.claude/`:

| Path | Content |
|------|---------|
| `rules/*.md` | 9 rule files — anti-loop, tool strategy, pipeline phases, `/start` + `/handover` + `/consilium` / `/audit` commands, deploy procedures, frontend debug pipeline, institutional knowledge, error taxonomy, canary for rule-load detection |
| `scripts/*.py` | 18 Python hook scripts — memory engine + session hooks (`rolling_memory.py`, `memory_session_start.py`/`_end.py`/`_post_tool.py`), evidence gates (`verify_gate.py`, `require_evidence.py`), phase machine (`phase.py`, `phase_gate.py`, `phase_prompt_inject.py`, `preserve_plan_context.py`), plan-first enforcer (`require_task.py`), observability (`telemetry_agent_health.py`, `check_rules_loaded.py`, `check_review_ages.py`), infra (`index_reports.py`, `backup_rolling_memory.py`, `add_frontmatter.py`, `instructions_loaded_log.py`) |
| `commands/*.md` | `/phase`, `/verify-after-edit`, `/verify-flow` slash commands |
| `agents/*.md`, `*.json` | Agent team protocols — lifecycle, ownership schema, worktree safety, readiness gates, roadmap convention |
| `settings.json` | Hooks wired to Claude Code, **merged** into any existing config |
| `.booster-manifest.json` | Installer metadata — SHA-256 per file, version, for idempotency and selective rollback |
| `.booster-config.json` | Your git author identity (used for rule-template substitution) |
| `backups/booster_install_*.tar.gz` | Rollback tarball captured before any mutation |

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
│   ├── rules/                # 9 .md files
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

**Out of scope (v2):**
- Native Windows support (requires `fcntl`→`msvcrt`, cmd-dispatched hooks, case-insensitive FS handling, `\\?\` long paths).
- `uninstall.py` (use manifest to selectively revert).
- Interactive `settings.json` conflict resolver.
- `booster doctor` diagnostic command.

---

## License

MIT.
