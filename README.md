# Claude Booster

Self-installing Claude Code configuration: rules, Python hook scripts, slash commands, agent protocols, and a safely-merged `settings.json`. One repo → reproducible Claude Code deployment on macOS and Linux.

## Quickstart

```bash
git clone https://github.com/<your-org>/claude-booster
cd claude-booster
python3 install.py --dry-run        # preview what would change
python3 install.py                  # interactive install
python3 install.py --yes --name "Your Name" --email "you@example.com"
```

Supported: Linux, macOS. Native Windows is refused (use WSL2).

## What gets installed

Under `~/.claude/`:

| Path | Content |
|------|---------|
| `rules/*.md` | 9 rule files (anti-loop, tool-strategy, pipeline, commands, deploy, institutional, error-taxonomy, frontend-debug, _canary) |
| `scripts/*.py` | 12 Python hook scripts — memory engine (`rolling_memory.py`), session hooks, verify-gate, telemetry, review-age checker |
| `commands/*.md` | `/verify-after-edit`, `/verify-flow` slash commands |
| `agents/*.md`, `*.json` | Agent protocols (lifecycle, ownership, worktree rules) |
| `settings.json` | Hooks wired to Claude Code; **merged** with any existing config |
| `.booster-manifest.json` | Installer metadata (SHA-256 per file, version) |
| `.booster-config.json` | Your git author identity (name, email) |
| `backups/booster_install_*.tar.gz` | Rollback tarball before any mutation |

## Safety contract

The installer is **conservative by default**:

- **NEVER touched**: `rolling_memory.db`, `history.jsonl`, `.credentials.json`, `projects/`, `plugins/`, `cache/`, `sessions/`, `file-history/`, `logs/`, `paste-cache/`, `image-cache/`, `chrome/`, `ide/`, `debug/`, `plans/`, `downloads/`, `scheduled-tasks/`, `backups/`, `session-env/`.
- **Atomic writes**: every file is written via tmp + `fsync` + `os.replace`. No partial state possible.
- **User-modified files preserved**: if your existing `rules/*.md` or scripts differ from the shipped template AND weren't written by a prior Booster install, they're preserved. Pass `--force` to overwrite.
- **Backup before any write**: `~/.claude/backups/booster_install_<UTC>.tar.gz`. Restore with `tar xzf ~/claude_backup_*.tar.gz -C ~/`.
- **`settings.json` merged by namespace**: installer owns only entries tagged `"source": "booster@<version>"` + the top-level `_booster` key. Your `permissions.allow`, `additionalDirectories`, `enabledPlugins`, `env`, `mcpServers` (incl. auth tokens) are preserved verbatim.
- **Secrets redacted in `--dry-run` output**: diff shows `***REDACTED***` for any key matching `token|key|secret|password`.
- **Interrupt-safe**: Ctrl+C triggers rollback from the backup tarball, exits 130.

## CLI flags

```
--dry-run        Preview changes. No writes.
--yes            Skip confirmation prompt (non-interactive).
--force          Overwrite user-modified files.
--name NAME      Git author name (substituted into rule templates).
--email EMAIL    Git author email.
--version        Print version and exit.
```

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | success / dry-run OK |
| 10 | Python < 3.8 |
| 11 | `~/.claude/` not writable |
| 12 | Downgrade attempt (manifest newer than installer) |
| 13 | Native Windows (unsupported) |
| 20 | Backup failed |
| 30 | Write failed (rolled back) |
| 40 | `settings.json` merge failed (rolled back) |
| 130 | User interrupted (rolled back) |

## Idempotency

Running `install.py` twice = zero writes the second time. Files are compared by SHA-256 against the shipped templates; unchanged files are skipped. `--dry-run` after a successful install shows an empty plan.

## Customization at install time

`{{GIT_AUTHOR_NAME}}` and `{{GIT_AUTHOR_EMAIL}}` placeholders in rule templates are replaced at install time with the values you pass via `--name/--email` (or prompt, or read from `git config --global`).

Hook commands in `settings.json` are pinned to absolute paths: `${CLAUDE_HOME}` → your `~/.claude/`, `${PYTHON}` → `sys.executable`. No runtime shell-var resolution.

## What's NOT included

- Your `rolling_memory.db` (per-user memory, bootstraps empty on first use).
- Your consilium/audit reports (those live in each project's `reports/`).
- Per-project `~/.claude/projects/*/memory/` markdown (per-project, per-user).
- `pyyaml`: only `scripts/index_reports.py` uses it. Install with `pip install -r requirements.txt` if you use `/start` cross-project indexing.

## Project Layout (this repo)

```
claude-booster/
├── install.py                # stdlib-only installer (~600 LOC)
├── requirements.txt          # pyyaml (runtime dep for index_reports.py)
├── .gitignore                # excludes all per-user runtime data
├── templates/
│   ├── rules/                # 9 .md files
│   ├── scripts/              # 12 .py files
│   ├── commands/             # 2 slash commands
│   ├── agents/               # 5 protocol files + 2 JSON schemas
│   └── settings.json.template
├── reports/                  # consilium, audit, handover history
└── README.md
```

## Design decisions

See `reports/consilium_2026-04-18_self_install_repo.md` for the architectural audit (4 agents + GPT-5.4), risk matrix, and rejected alternatives.

Key tradeoffs:
- **Python-stdlib only** for the installer. No pip at install time.
- **Namespaced `settings.json` merge** via `source: "booster@<ver>"` tags — not deep merge.
- **DB migration punted**: `rolling_memory.py` auto-initializes an empty v5 DB on first call.
- **Windows deferred to v2**: `fcntl`, case-sensitivity, hook shell dispatch, and MAX_PATH all need separate handling.

## License

MIT.
