---
date: 2026-04-18
type: audit
topic: cross_platform_scenarios
scope: global
category: claude-booster
preserve: true
agents: 3
models: [opus-4.7, opus-4.7, opus-4.7]
---

# Audit — Cross-Platform Install Scenarios (Linux / Windows / macOS)

## Scope

Scenario-based audit of `install.py` v1.0.0 (commit `eca5d57`) against 30 real-world install contexts across 3 operating system families. 3 OS-expert agents (Claude Opus 4.7) independently traced concrete code paths.

## Verdict

**v1.0.0 is NOT safe to call "Linux + macOS supported" without 7 patches.** Current state:
- **macOS Homebrew happy-path:** works. (Dmitry's machine, smoke-tested.)
- **Ubuntu/Debian/Fedora standard installs:** works.
- **Silent failure modes:** 4 documented (Snap, NFS homes, WSL2-Desktop split, Homebrew Python upgrade).
- **Silent bypass:** 1 (Cygwin/MSYS2/MinGW misses Windows refusal).

Ship-blockers: 7. Acceptable v2 items: 5.

## Findings Matrix

### CRITICAL — ship-blockers (silent data loss / silent misinstall)

| # | File:line | Finding | Agents |
|---|-----------|---------|--------|
| C1 | `install.py:132` | `platform.system() == "Windows"` is equality; misses `"CYGWIN_NT-*"`, `"MSYS_NT-*"`, `"MINGW64_NT-*"`. User on MSYS2 Python bypasses refusal → install proceeds with mangled paths. | Windows |
| C2 | `install.py:131-137` + README | WSL2 + Claude Code Desktop on Windows host: Desktop reads `%USERPROFILE%\.claude`, installer writes to `/home/user/.claude` inside WSL → install "succeeds", hooks never fire. Undocumented. | Windows |
| C3 | `install.py:131-137` | WSL1 passes preflight. SQLite WAL is documented-broken on drvfs → `rolling_memory.db` corrupts on first write. | Windows |
| C4 | `install.py:58,127-158` | Snap-packaged Claude Code on Ubuntu 24.04 has AppArmor-confined HOME. `Path.home()/".claude"` isn't readable by Snap Claude → silent misinstall. | Linux |
| C5 | `install.py:151-153` | Preflight sync-marker regex only matches `onedrive\|dropbox\|google drive\|icloud`. Misses NFS/CIFS/SMB/sshfs. SQLite WAL corrupts on NFS (SQLite docs forbid). Enterprise laptops with corporate home mounts affected. | Linux |
| C6 | `install.py:61,238,251` | `BACKUP_DIR = CLAUDE_HOME / "backups"`. If `~/.claude/` is on an external drive that unmounts mid-install, rollback tarball is on the same failed volume → unrecoverable. | macOS |
| C7 | `README.md:39` | Restore command says `tar xzf ~/claude_backup_*.tar.gz -C ~/`. Actual path is `~/.claude/backups/booster_install_*.tar.gz`. User follows README during recovery → fails. | macOS |

### HIGH — fix before claiming production-ready

| # | File:line | Finding | Agents |
|---|-----------|---------|--------|
| H1 | `install.py:285` | `${PYTHON}` = `sys.executable` baked into `settings.json`. Breaks on: `brew upgrade python`, Intel→ARM Mac migration, NixOS rebuild, pyenv version swap, Python.app translocation. Hooks silently stop firing. All 3 agents flagged. | all |
| H2 | `install.py:319` | `merge_settings()` union-s `permissions.allow` only; ignores `permissions.ask` and `permissions.deny`. Booster's `rm` guards silently drop if user has no `ask` list. | Linux |
| H3 | `install.py:115` | `os.fsync()` on Darwin does NOT issue `F_FULLFSYNC`. Power-loss window between rename and platter flush → corrupt rename. SQLite docs specifically call this out. | macOS |
| H4 | preflight | No sqlite3 FTS5 capability check. Apple's bundled Python 3.9 may lack FTS5 → `rolling_memory.py` errors at runtime, not install time. | macOS |

### MED / LOW — known limitations, document in README

| # | File:line | Finding | Agents |
|---|-----------|---------|--------|
| M1 | `install.py:284` | `str(CLAUDE_HOME)` on Windows produces invalid JSON (`\U` in `\Users\` is not a valid JSON escape). Latent — Windows refused. Future Windows port will crash. | Windows |
| M2 | `install.py:518-531` | On Alpine/container with no `git` AND no `$USER` AND `--yes`: synthesizes `"claude-booster-user@users.noreply.github.com"`. Vercel deploys fail silently (institutional rule). Installer should warn loudly. | Linux |
| M3 | `install.py:285` | NixOS: `sys.executable` is nix-store path that changes on every `nixos-rebuild switch`. Partial case of H1; worth doc-warning. | Linux |
| M4 | docs | devcontainers wipe `~/.claude/` on rebuild. README doesn't mention volume-mount requirement. | Windows |
| M5 | `install.py:136` | Windows-refusal message has 7-space indent → line wrap breaks URL in PowerShell. Cosmetic. | Windows |

## Scenario Pass/Fail Table

| Scenario | OS | Outcome | Severity |
|----------|-----|---------|----------|
| Ubuntu 22.04 + apt py3.10 + fresh | Linux | PASS | — |
| Debian 12 + user config | Linux | PASS (minor: ask/deny lists not merged) | MED |
| Ubuntu 24.04 + Snap Claude Code | Linux | **SILENT MISINSTALL** | CRITICAL |
| Arch + Booster v0.9 | Linux | PASS (verbose msg) | LOW |
| Fedora 40 SELinux | Linux | PASS (runtime AVCs possible) | LOW |
| Alpine musl no-git | Linux | PASS (silent fake email) | MED |
| NixOS | Linux | PASS now, BREAKS on rebuild | HIGH |
| Home on NFS | Linux | **DB CORRUPTS at runtime** | CRITICAL |
| pyenv | Linux | PASS | LOW |
| Docker no-Projects | Linux | PASS (empty indexer) | LOW |
| Windows native PowerShell | Win | REFUSED exit 13 ✓ | — |
| WSL2 + Claude Code Desktop Win-host | Win | **SILENT MISINSTALL** | CRITICAL |
| WSL1 | Win | **DB CORRUPTS** | CRITICAL |
| Devcontainer | Win | PASS but ephemeral | MED |
| Cygwin/MSYS2/MinGW Python | Win | **BYPASS REFUSAL** | CRITICAL |
| `%USERPROFILE%` with spaces | Win | PASS | LOW |
| Symlink to `/mnt/c/` | Win | PASS slow | MED |
| macOS Homebrew happy-path | mac | PASS | — |
| macOS brew upgrade python | mac | **HOOKS SILENTLY STOP** | HIGH |
| Intel → ARM Mac migration | mac | **HOOKS SILENTLY STOP** | HIGH |
| `/usr/bin/python3` stub | mac | PASS (Xcode CLT prompt first) | LOW |
| Case-sensitive APFS | mac | PASS | — |
| External HOME unmount mid-install | mac | **UNRECOVERABLE** | CRITICAL |
| App-translocated Python.app | mac | HOOKS BREAK AFTER REBOOT | HIGH |
| Gatekeeper / quarantine | mac | PASS (no exec of .py) | — |
| FileVault + power loss | mac | RARE corruption window | LOW (installer), MED (runtime DB) |

## Concrete Patch Plan

### Phase 1 — CRITICAL fixes (~90 LOC, 45 min)

**P1.1 Cygwin/MSYS/MinGW refusal** (install.py:131-137, +3 LOC)
```python
sysname = platform.system()
if sysname == "Windows" or sysname.startswith(("CYGWIN", "MSYS", "MINGW")):
    fail(13, "Native Windows / Cygwin / MSYS2 / MinGW unsupported. Use WSL2.")
```

**P1.2 WSL detection + WSL1 refusal + WSL2 Desktop warning** (install.py:137+, +15 LOC)
```python
if sysname == "Linux":
    try:
        rel = Path("/proc/sys/kernel/osrelease").read_text().lower()
        if "microsoft" in rel:
            if "wsl2" not in rel:
                fail(13, "WSL1 detected — SQLite WAL corrupts on drvfs. Upgrade: wsl --set-version <distro> 2")
            log(
                "WSL2 detected. If Claude Code runs on the Windows host "
                "(not inside WSL), it reads %USERPROFILE%\\.claude and will "
                "not see this install. Install from the side where Claude "
                "Code actually runs.",
                "WARN",
            )
    except OSError:
        pass
```

**P1.3 Snap/Flatpak detection** (install.py:127+, +8 LOC)
```python
if os.environ.get("SNAP") or os.environ.get("FLATPAK_ID"):
    fail(14,
        "Claude Code appears sandboxed (Snap/Flatpak). Its HOME differs "
        "from $HOME. Install Claude Code via deb/rpm/dmg instead, or run "
        "install.py from inside the sandbox.",
    )
```

**P1.4 NFS/CIFS/SMB preflight** (install.py:151+, +20 LOC)
```python
def _detect_network_fs(path: Path) -> str | None:
    """Return fs type if path is on a network filesystem, else None."""
    try:
        if sys.platform == "linux":
            mounts = Path("/proc/mounts").read_text().splitlines()
            best = ("", "")
            for line in mounts:
                parts = line.split()
                if len(parts) >= 3 and str(path).startswith(parts[1]):
                    if len(parts[1]) > len(best[0]):
                        best = (parts[1], parts[2])
            if best[1] in {"nfs", "nfs4", "cifs", "smbfs", "smb3", "fuse.sshfs"}:
                return best[1]
        # macOS: /usr/sbin/diskutil info or statfs; lower priority, skip v1
    except Exception:
        pass
    return None

# in preflight():
fs = _detect_network_fs(CLAUDE_HOME)
if fs:
    fail(15, f"~/.claude/ is on {fs} (network filesystem). SQLite WAL "
             "corrupts on network FS. Move ~/.claude/ to a local disk.")
```

**P1.5 Backup to $TMPDIR** (install.py:61,211+, +8 LOC)
```python
# Stage backup in tmpfs, move to CLAUDE_HOME/backups only on full success.
def make_backup() -> Path:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp_tarball = Path(tempfile.gettempdir()) / f"booster_install_{stamp}.tar.gz"
    # ... (existing logic, writing to tmp_tarball)
    return tmp_tarball

def finalize_backup(tmp_tarball: Path) -> Path:
    """Called after successful install. Copies tmp → backups dir."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    final = BACKUP_DIR / tmp_tarball.name
    shutil.copy2(tmp_tarball, final)
    tmp_tarball.unlink(missing_ok=True)
    return final
```

**P1.6 README restore command correction** (README.md, +2 LOC)
```markdown
**Restore:**
tar xzf ~/.claude/backups/booster_install_*.tar.gz -C ~/

**Selective restore** (e.g. only rules):
tar xzf ~/.claude/backups/booster_install_*.tar.gz -C ~/ .claude/rules
```

**P1.7 Test matrix additions** (tests/, +3 scenarios)
Add scenarios: `SNAP=1` env, `/proc/mounts` with nfs entry, WSL1 `uname -r`.

### Phase 2 — HIGH fixes (~40 LOC, 25 min)

**P2.1 `${PYTHON}` stable resolution** (install.py:285)
```python
# Prefer unversioned 'python3' on PATH (Homebrew symlink, apt symlink,
# pyenv shim) over sys.executable (versioned Homebrew Cellar, nix-store).
import shutil
stable = shutil.which("python3") or sys.executable
raw = raw.replace("${PYTHON}", stable)
```
Preserves happy-path; eliminates H1 for 95% of users.

**P2.2 Merge permissions.ask + .deny** (install.py:319)
```python
for perm_key in ("allow", "ask", "deny"):
    user_list = set(result["permissions"].get(perm_key, []))
    for p in booster_perms.get(perm_key, []):
        user_list.add(p)
    if user_list:
        result["permissions"][perm_key] = sorted(user_list)
```

**P2.3 Darwin F_FULLFSYNC** (install.py:115)
```python
f.flush()
os.fsync(f.fileno())
if sys.platform == "darwin":
    import fcntl
    try:
        fcntl.fcntl(f.fileno(), fcntl.F_FULLFSYNC)
    except OSError:
        pass  # F_FULLFSYNC not supported on all FS
```

**P2.4 FTS5 preflight** (install.py:127+)
```python
try:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE _t USING fts5(x)")
    conn.close()
except sqlite3.OperationalError:
    fail(16, "Python sqlite3 lacks FTS5 support. Install Python via "
             "Homebrew/apt/dnf or compile SQLite with FTS5.")
```

### Phase 3 — MED / LOW (README only)

- Document Alpine/no-git edge case in Known Limitations.
- Document NixOS rebuild requires `install.py --yes` re-run.
- Document devcontainer volume-mount requirement.
- Remove 7-space indent in refusal message.

## Rejected Alternatives

| Alternative | Why rejected |
|-------------|--------------|
| Full Windows support in v1 | All 3 agents agree: 5+ days more work for `fcntl`→`msvcrt`, hook dispatch via cmd.exe, case-insensitive FS, JSON path escaping, MAX_PATH. Defer. |
| Ship as-is with "Known limitations" | 4 of 7 CRITICAL items are silent misinstalls — user can't self-diagnose. Unacceptable. |
| `${PYTHON}` via wrapper script `~/.claude/bin/python3` | Adds runtime indirection + another file to manage. `shutil.which("python3")` is simpler and covers 95%. |
| Auto-repair on every session start | Too magic; install.py should be the one source of truth for paths. |
| Drop FTS5 requirement | Breaks `/start` cross-project search, a core feature. |

## Go / No-Go Recommendation

**NO-GO on current `eca5d57`** for a public release claiming "Linux + macOS supported."

**Minimum to flip to GO:** apply Phase 1 (7 CRITICAL patches, ~90 LOC, 45 min). After that:
- Honest claim: "Supported: macOS (Homebrew Python), Ubuntu/Debian/Fedora (system Python). WSL2 with caveats. Windows native, Snap-packaged Claude Code, WSL1, network-mount HOMEs: refused with actionable error."
- Phase 2 (HIGH, ~40 LOC, 25 min) eliminates the 3 biggest HIGH items — recommended same session.
- Phase 3 (docs) any time.

Total: **~110 LOC patch, ~70 min work** to ship v1.0.1 as genuinely cross-platform-safe.

## Dmitry's Machine TODAY

Per macOS agent's trace on Dmitry's actual `~/.claude/` state:

1. `install.py --dry-run` → "0 write, 23 skip, 7 preserve (user-modified)" ✓ (already verified).
2. `install.py --yes` (no --force) → installs only settings.json merge. His 7 personalized IBKR/Horizon rules PRESERVED in place. `rolling_memory.db` untouched. `mcpServers` tokens intact. `~/claude_backup_*.tar.gz` appears before any write. ✓ SAFE.
3. `install.py --yes --force` → his rules overwritten with depersonalized templates, **timebomb** on next `brew upgrade python`.
4. Recovery path (README) has wrong tarball name. **Fix README before Dmitry trusts it.**

**Recommendation:** apply Phase 1 + P2.1 (Python path stability) in next 70 min, then Dmitry's machine is genuinely safe even under `--force`.

## Files

Cited evidence: `install.py:58,61,115,131-137,151-153,211+,285,319,478,518-531,601-607,638-642`; `templates/settings.json.template:53-87`; `README.md:39`.

## Links

- Consilium: `reports/consilium_2026-04-18_self_install_repo.md`
- Commit under audit: `eca5d57` — "feat: self-installing Claude Booster repo"
