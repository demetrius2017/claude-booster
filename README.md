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
- **Backup before any write**: staged in `$TMPDIR`, finalized to `~/.claude/backups/booster_install_<UTC>.tar.gz` after a successful install. Restore with:
  ```bash
  tar xzf ~/.claude/backups/booster_install_*.tar.gz -C ~/
  ```
  Selective restore (e.g. only rules):
  ```bash
  tar xzf ~/.claude/backups/booster_install_*.tar.gz -C ~/ .claude/rules
  ```
  If an install failed mid-flight and the final copy never ran, the backup is still at `$TMPDIR/booster_install_<UTC>.tar.gz` (on macOS: `/var/folders/.../T/`; on Linux: `/tmp/`).
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
| 13 | Native Windows / Cygwin / MSYS2 / MinGW / WSL1 |
| 14 | Sandboxed Claude Code (Snap / Flatpak) |
| 15 | `~/.claude/` is on a network filesystem (NFS / CIFS / SMB / sshfs) |
| 16 | Python sqlite3 lacks FTS5 support |
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

## Known Limitations

**Supported:**
- macOS (Apple Silicon + Intel) with Homebrew Python 3.8+
- Ubuntu / Debian / Fedora / Arch / Alpine with system or apt/dnf/pacman Python 3.8+
- WSL2 — with the caveat in scenario 2 below

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

## License

MIT.
