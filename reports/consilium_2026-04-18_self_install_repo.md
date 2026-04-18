---
date: 2026-04-18
type: consilium
topic: self_install_repo
scope: global
category: claude-booster
preserve: true
agents: 4
models: [opus-4.7, opus-4.7, opus-4.7, gpt-5.4]
---

# Consilium — Self-Installing Claude Booster Repo

## Task Context

Package `~/.claude/` (rules, hooks, scripts, settings.json, commands, agents) as a git-cloneable repo that installs itself via `python install.py` on any OS. Claude Code itself must be able to run the installer on a fresh machine.

Goal: one codebase → reproducible Claude Booster deployment on macOS, Linux, (WSL), Windows.

## RECON — Verified Facts (actual code state, not memory)

| Fact | Source | Implication |
|------|--------|-------------|
| `Path.home() / "Projects"` hardcoded | `rolling_memory.py:1172`, `index_reports.py:63` | Breaks on any non-macOS layout |
| `osascript` + AppleScript | `clipboard_image.sh`, `on_stop.sh` | macOS-only, no Linux/Windows equivalent |
| `stat -f %m` (BSD syntax) | `on_stop.sh`, `on_task_completed.sh` | Fails silently on GNU Linux (`stat -c %Y`) |
| `jq` CLI dependency | 4 shell hooks | Not installed by default anywhere |
| `SCHEMA_VERSION = 5` | `rolling_memory.py:97` | 3 migrations since v2 (not v4 as earlier assumed) |
| `pyyaml` dep | `index_reports.py:52` | Non-stdlib; installer must handle |
| 13 Python scripts + 7 shell scripts, ~5000 LOC | `scripts/` wc -l | Non-trivial surface |
| `history.jsonl` contains Mikrotik password, VLESS UUIDs, ssh creds | agent 2 grep | **CRITICAL — MUST NOT ship** |
| `.credentials.json` = Claude Code OAuth | `rolling_memory.py:842` reference | **MUST NOT touch** |
| `settings.json.additionalDirectories` contains user project paths | `settings.json:48-55` | OPSEC leak — strip in template |

## Agent Positions

| Agent | Bio | Key Position | KPI |
|-------|-----|--------------|-----|
| **A1 Cross-Platform** (Opus 4.7) | OS portability engineer | Strategy (C): Python-first hooks, `.sh` only for mac-clipboard. `${CLAUDE_HOME}`+`${PYTHON}` install-time substitution. `CLAUDE_PROJECTS_ROOT` env + probe list. | 1 codepath, 95% coverage, mac+linux+WSL |
| **A2 Security/OPSEC** (Opus 4.7) | Secret/attack-surface auditor | HOLD until `git log -S` forensics on packaged repo. 25+ `.gitignore` entries. Refuse `skipAutoPermissionPrompt:true` default. Canary rotated per install. | Zero secrets in git history |
| **A3 Installer UX/SRE** (Opus 4.7) | Ops-grade installer designer | Pure-stdlib Python, 10-step flow: preflight→snapshot→plan→confirm→atomic-write→merge→migrate→verify→manifest. `booster.lock` SHA-256 idempotency. `--dry-run` required. `_booster` namespace merge. Defer Windows + uninstall to v2. | Second run = 0 writes; Ctrl+C safe |
| **A4 GPT-5.4** (external, via PAL) | Challenger/blind-spot hunter | Native Windows **unsupported**, WSL best-effort (not claimed). **No new YAML dep** unless unavoidable. Canary: stable per install, rotate on reset only. **DB migration is release-blocking** — installer MUST version-check. Flagged: file locking, secret redaction in diff, ownership boundary precision. | Fail-closed on unknown DB schema |

## Consensus Decisions

### ✅ Unanimous — lock in

1. **Python-first installer** (pure stdlib, no pip at install time). Hooks rewritten to Python except macOS clipboard (AppleScript API-bound).
2. **Atomic writes** — tmp-in-same-dir + fsync + os.replace() for every file.
3. **Namespaced `settings.json` merge** — `_booster` tag on our entries; user's `permissions.allow`, `additionalDirectories`, `enabledPlugins`, `env`, `mcpServers` survive untouched.
4. **Hard-exclude from packaging:** `history.jsonl`, `rolling_memory.db*`, `.credentials.json`, `projects/`, `plugins/`, `cache/`, `sessions/`, `session-env/`, `file-history/`, `paste-cache/`, `image-cache/`, `backups/`, `chrome/`, `ide/`, `debug/`, `logs/`, `plans/`, `downloads/`, `scheduled-tasks/`.
5. **`--dry-run` required in v1** — difflib unified diff, no writes, secret-redacted.
6. **`.booster-manifest.json`** — SHA-256 per installed file + version + installed_at for idempotency, rollback, uninstall.
7. **Rollback tarball** before any write: `~/.claude/backups/booster_install_<UTC>.tar.gz`.

### 🎯 Disagreements resolved by GPT

| Question | Agents split | GPT verdict | Final |
|----------|--------------|-------------|-------|
| Native Windows scope | A1: WSL-included; A3: defer | **Native Win unsupported v1; WSL best-effort, not claimed** | **Defer native Win. WSL works-if-works, docs say "unsupported"** |
| YAML dependency | A1/A3: stdlib; A4: no new dep | **No new dep — only `index_reports.py` uses yaml post-install, handle via `pip install pyyaml` at first-run, not install-time** | Installer is stdlib-only. `pyyaml` becomes runtime dep (like any pip user-install). Installer writes `requirements.txt`. |
| Canary rotation | A2: per-install; A1/A3: stable | **Stable per install, rotate only on `install.py --reset`** | Canary stays stable across runs; fresh clone → fresh token; explicit `--reset` flag to rotate |
| DB migration in v1 | A3: yes; A2: silent | **Release-blocking — installer MUST version-check, migrate known versions, fail-closed on unknown** | v1 MUST ship DB version check + known migrations (v3→v4→v5). Unknown = refuse with explicit error. |

### 🕵️ Blind spots GPT caught that agents missed

1. **File locking / concurrent runs** — if user double-clicks installer or runs from 2 shells, atomic writes are not enough. Add `fcntl.flock()` advisory lock on `~/.claude/.booster-install.lock` for entire install duration.
2. **Secret redaction in `--dry-run` output** — diff of `settings.json` could leak `mcpServers` auth tokens. Implement `_redact_settings()` before printing any diff.
3. **Ownership boundary precision** — "namespace merge" is ambiguous. Concrete rule: installer owns top-level key `_booster` + any hook entry tagged `"source":"booster@<ver>"`. Everything else is user-owned; installer never deletes foreign keys.
4. **Idempotency as acceptance criterion** — not just a feature. Test: `install.py && install.py --dry-run` must show zero planned changes.
5. **Documented test matrix** — install against 5 states (fresh, booster-older, booster-same, non-booster with settings.json, malformed settings.json) before shipping.

### ❌ Rejected Alternatives

| Alternative | Why rejected |
|-------------|--------------|
| Bash-based installer | Windows/WSL incompatible; zero parsing of JSON/YAML without external tools |
| Homebrew/apt package | Fragments by OS; each needs separate maintainer; `~/.claude/` is per-user so system packaging is wrong layer |
| Docker image | User runs Claude Code on host, not container; `~/.claude/` bind-mount defeats the purpose |
| npm package | Python is already the script runtime; adding npm doubles deps |
| Keep `.sh` hooks as-is + require WSL on Windows | Adds support burden for shim layer; `.sh` hooks have macOS-only `osascript`/`stat -f` anyway |
| Ship `rolling_memory.db` pre-seeded | User data; consilium rows are per-project; each user should bootstrap own DB via `index_reports.py` |

## Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| User loses existing `~/.claude/settings.json` custom hooks | MED | HIGH | Namespace merge (`_booster` tag) + snapshot tarball + refuse-on-conflict for untagged hooks |
| Installer overwrites user's `rolling_memory.db` during "migration" and corrupts it | LOW | **CRITICAL** | Mandatory backup via SQLite `.backup` API before any schema write; transactional migration; refuse on unknown schema |
| Secret leak: user pushes repo before scrubbing `~/.claude/history.jsonl` references | LOW | **CRITICAL** | `.gitignore` blocks at repo root + pre-commit hook scans `git diff --cached` for VPN-config, credential, and `ssh\x70ass -p` patterns (strings deliberately broken here to avoid self-matching) |
| Installer succeeds but hooks don't fire (wrong path in `settings.json`) | MED | MED | Post-install `verify` step runs hook scripts with `--self-test` flag; exits with error if any hook fails to exec |
| Python version mismatch (script hardcoded `python3`, user has `python3.8` only via alias) | MED | LOW | Installer captures `sys.executable` and writes absolute path into `${PYTHON}` substitution; no reliance on `python3` being in PATH |
| User on network drive (OneDrive/Dropbox/SMB) — SQLite WAL corruption | LOW | HIGH | Preflight check: detect `~/.claude/` inside known sync dirs; warn + offer `journal_mode=DELETE` |
| Concurrent install runs | LOW | HIGH | `fcntl.flock()` on lock file; second runner waits or exits |
| `PortableYAML` issue (`pyyaml` not installed when `index_reports.py` runs) | MED | LOW | Installer writes `requirements.txt` + post-install hint: `pip install -r ~/.claude/requirements.txt` (or auto-install into `~/.claude/_vendor/` if `--with-deps`) |
| Windows user follows README and installer fails halfway | HIGH | MED | Preflight `platform.system() == "Windows"` → explicit error "not supported in v1; use WSL2" with link to Microsoft Learn. Early abort, no partial state |

## Implementation Plan (v1 MVP)

### Scope (1 week, 1 person)

```
claude-booster/
├── install.py                     # stdlib-only, ~600 LOC
├── uninstall.py                   # v1.5, deferred
├── booster.lock                   # SHA-256 of every template file
├── requirements.txt               # pyyaml for post-install
├── .gitignore                     # 25+ excludes (see §.gitignore)
├── .githooks/pre-commit           # scan for known secret patterns
├── README.md                      # quickstart + support matrix
├── templates/
│   ├── rules/                     # 10 .md (copied as-is from ~/.claude/rules/)
│   ├── scripts/
│   │   ├── *.py                   # 13 Python scripts, portable
│   │   ├── hooks/                 # NEW — Python versions of .sh hooks
│   │   │   ├── on_session_start.py
│   │   │   ├── on_stop.py
│   │   │   ├── on_task_completed.py
│   │   │   ├── on_teammate_idle.py
│   │   │   └── clipboard_image.py  # dispatches to mac-sh / linux-xclip / win-Pillow
│   │   └── platform/
│   │       └── clipboard_image_mac.sh  # only .sh file, only on macOS
│   ├── commands/                  # 2 slash commands
│   ├── agents/                    # 5 agent protocol files
│   └── settings.json.template     # ${CLAUDE_HOME}, ${PYTHON} placeholders
├── schema/
│   └── rolling_memory_v5.sql      # empty DB bootstrap
├── migrations/                    # DB schema migrations
│   ├── v3_to_v4.sql
│   └── v4_to_v5.sql
└── tests/
    ├── test_install_fresh.py       # clean ~/.claude/
    ├── test_install_existing.py    # pre-existing settings.json
    ├── test_idempotency.py         # install twice = no writes second time
    ├── test_rollback.py            # interrupt mid-install
    ├── test_dry_run.py             # diff-only, no writes
    └── test_settings_merge.py      # user hooks preserved
```

### Install Flow (10 steps, matching A3 + GPT additions)

```
[0] preflight       python>=3.8, writable $HOME, platform check,
                    OneDrive/Dropbox detect, refuse Windows → exit 10-13
[0.5] lock          fcntl.flock(~/.claude/.booster-install.lock) — GPT addition
[1] detect state    classify: FRESH | BOOSTER_OLD | NON_BOOSTER | BOOSTER_SAME
[2] snapshot        tar czf ~/.claude/backups/booster_install_<UTC>.tar.gz
                    settings.json rules/ scripts/ commands/ agents/
                    (EXCLUDES history.jsonl, projects/, rolling_memory.db, ...)
[3] plan            hash-compare; build write-list; redact secrets for output
[4] confirm         unless --yes or --dry-run, prompt y/N
[5] write           atomic per-file (tmp+fsync+rename), chmod from manifest
[6] merge settings  load + patch _booster namespace + jsonschema validate +
                    atomic write (see §settings merge)
[7] migrate DB      if rolling_memory.db exists:
                      PRAGMA user_version check
                      backup via SQLite .backup API
                      apply known migrations in transaction
                      fail-closed if version > 5 (downgrade) or unknown
[8] verify          re-hash installed files vs booster.lock
                    run each hook script with --self-test
                    run verify_gate.py --self-test
[9] record          write ~/.claude/.booster-manifest.json + rotate canary ONLY if --reset

[FAIL step ≥5]      restore from [2] tarball; exit 3x + step code; release lock
```

### `settings.json` Merge Contract (GPT-precisioned)

```python
# installer owns:
#   - top-level key "_booster" (our metadata)
#   - any entry in hooks.* tagged {"source": "booster@<ver>"}
# installer NEVER touches:
#   - permissions.*, additionalDirectories, enabledPlugins, env, mcpServers
#   - hook entries without "source" tag (= user-owned)
#   - any top-level key we don't recognize (forward-compat with future Claude Code)

def merge(user_settings, booster_settings, version):
    result = deepcopy(user_settings)
    # strip old booster entries
    for hook_type, handlers in result.get("hooks", {}).items():
        result["hooks"][hook_type] = [
            h for h in handlers
            if h.get("source", "").startswith("booster@") is False
        ]
    # append new booster entries, tagged
    for hook_type, handlers in booster_settings.get("hooks", {}).items():
        for h in handlers:
            h["source"] = f"booster@{version}"
            result.setdefault("hooks", {}).setdefault(hook_type, []).append(h)
    # metadata
    result["_booster"] = {"version": version, "installed_at": now_iso()}
    return result
```

### DB Migration Policy (release-blocking per GPT)

```
install.py detects rolling_memory.db:
  v < 3  → migration not supported; error "DB predates Booster packaging; bootstrap fresh"
  v == 3 → apply migrations/v3_to_v4.sql + v4_to_v5.sql in ONE transaction
  v == 4 → apply migrations/v4_to_v5.sql in transaction
  v == 5 → no-op (current)
  v > 5  → refuse (downgrade attempt); error "DB newer than installer; upgrade Booster first"
  
All paths: mandatory SQLite .backup() before any SQL execution.
Backup path: ~/.claude/backups/rolling_memory_pre_migration_<UTC>.db
```

### Pre-commit Secret Scanner (A2 + GPT)

`.githooks/pre-commit`:
```bash
#!/usr/bin/env python3
# scans staged changes for known secret patterns before commit
import re, subprocess, sys

PATTERNS = [
    (r'v' + 'less://', 'VPN config URL'),
    (r'M' + 'i77755', 'known credential fragment'),
    (r's' + 'shpass -p', 'SSH credential in CLI'),
    (r'[A-Za-z0-9+/]{32,}=', 'possible base64 secret'),
    (r's' + 'k-[a-zA-Z0-9]{20,}', 'OpenAI-like API key'),
    (r'/Users/[a-z]+/Projects/', 'user-specific path'),
]
# NOTE: string literals deliberately concatenated so this report does not
# self-match in the very scanner it describes.
diff = subprocess.check_output(['git', 'diff', '--cached']).decode()
hits = [(p, desc) for p, desc in PATTERNS if re.search(p, diff)]
if hits:
    for p, desc in hits:
        print(f"REFUSE: {desc} matches pattern {p!r}", file=sys.stderr)
    sys.exit(1)
```

### Test Matrix (GPT requirement)

| State | Test | Expected |
|-------|------|----------|
| Fresh `~/.claude/` | `install.py --yes` | Creates all dirs/files, exit 0 |
| Fresh + `--dry-run` | | Prints plan, exits 0, no writes |
| Existing Booster v0.9 → v1.0 | `install.py --yes` | Backup taken, files updated, DB migrated v4→v5 |
| Existing non-Booster w/ user hooks | `install.py --yes` | User hooks preserved, ours appended |
| Second run (idempotent) | `install.py --yes` | `nothing to do`, exit 0, no writes |
| Malformed `settings.json` | `install.py --yes` | Refuse with jsonschema error, no writes |
| Windows native | `install.py` | Exit 13 with explicit "use WSL2" |
| Ctrl+C during step 5 | | Rollback from tarball, exit 130 |
| Concurrent run | 2 processes | Second exits "lock held by PID N" |
| DB schema v2 | | Refuse "predates Booster packaging" |
| DB schema v7 (future) | | Refuse "newer than installer" |

## Final Consensus — Go/No-Go

**GO** with the plan above, **provided**:

1. Pre-commit secret scanner is the FIRST commit (protects all subsequent).
2. `.gitignore` with 25+ excludes landed before first `git add`.
3. Windows is explicit `platform.system() == "Windows"` exit 13 in v1 — no half-support.
4. DB migration tests pass all 4 version paths before v1 ship.
5. Dry-run output shows secret-redacted diff on `settings.json`.

**Deferred to v2:**
- Windows native support (requires fcntl→msvcrt, hooks via `cmd.exe`, case-insensitive FS handling — 3-5 days more work)
- `uninstall.py` (clean revert via manifest)
- Interactive settings conflict resolver
- `booster doctor` diagnostic command
- Auto-upgrade (`install.py --upgrade`)

**Release-blocking checklist:**
- [ ] All 11 test matrix rows green
- [ ] `git log --all -S <VPN-scheme>` on packaged repo → 0 hits (substitute the actual scheme name)
- [ ] `git log --all -S <credential-fragment>` → 0 hits
- [ ] `git log --all -S <ssh-cli-tool>` → 0 hits
- [ ] Pre-commit hook blocks test commit containing fake VPN-config string
- [ ] Fresh macOS VM + fresh Ubuntu 22.04 VM both install successfully

## Recommendations

### Immediate (before any code)
1. Create repo `.gitignore` with full 25+ exclude list from A2.
2. Install pre-commit secret scanner.
3. Freeze decision matrix above as `decisions.md` in repo root.

### Implementation order (5 days)
- **Day 1**: repo scaffold, `.gitignore`, pre-commit, copy templates/ from `~/.claude/` with scrub (remove `additionalDirectories`, redact `settings.json`, drop `.db`/`.bak`/`history.jsonl`).
- **Day 2**: `install.py` steps 0-5 (preflight, lock, detect, snapshot, plan, atomic-write).
- **Day 3**: steps 6-9 (settings merge, DB migrate, verify, manifest).
- **Day 4**: tests — all 11 matrix rows.
- **Day 5**: README, smoke test on fresh Linux VM + fresh macOS VM, first `git push`.

### Success metrics (v1 done)
- Fresh Linux clone → `python install.py --yes` → Claude Code session starts with rules loaded (`check_rules_loaded.py` exits 0).
- Existing macOS setup (Dmitry) → `install.py --yes` → zero user hooks lost, DB intact, rolling_memory queries work.
- `git log -S` audits clean.
- 11/11 tests green in CI.

## Links

- Related: `reports/audit_2026-04-17_agent_context_dysfunction.md` (rules auto-load canary design)
- Related: `reports/consilium_2026-04-17_broker_parity_architecture.md` (format reference)
- Supersedes initial plan in session message above (§"Что предлагаю упаковать")

