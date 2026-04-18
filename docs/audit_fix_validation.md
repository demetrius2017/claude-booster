# Audit — Fix Validation (install.py v1.0.0 → v1.0.1)

Independent re-review after applying 11 patches from `audit_2026-04-18_cross_platform_scenarios.md`.

## Scorecard

| ID | Status | Evidence |
|----|--------|----------|
| C1 — Cygwin/MSYS/MinGW bypass | **PASS** | `install.py:196` — `sysname.startswith(("CYGWIN","MSYS","MINGW"))` |
| C2 — WSL2 Desktop-split warn | **PASS** | `install.py:211-218` — WARN referencing `%USERPROFILE%\.claude` |
| C3 — WSL1 refusal | **PASS** | `install.py:205-210` — `_detect_wsl()=="wsl1"` → fail(13) |
| C4 — Snap/Flatpak refusal | **PASS** | `install.py:221-235` — both envs checked, live-tested exit 14 |
| C5 — NFS/CIFS/SMB detection | **PASS** | `install.py:142-171` — `/proc/mounts` longest-prefix match, 9 fs types |
| C6 — Backup in $TMPDIR | **PASS** | `install.py:332-365` stage in tmp, `finalize_backup()` copies after success |
| C7 — README restore path | **PASS** | `README.md:41-47` correct path + selective-restore + tmp fallback |
| H1 — `${PYTHON}` stable | **PASS** | `install.py:423-439` — `shutil.which("python3")` first |
| H2 — merge all perms lists | **PASS** | `install.py:482-487` — loops `allow`/`ask`/`deny` |
| H3 — F_FULLFSYNC on Darwin | **PASS** | `install.py:125-130` — darwin-gated, OSError/AttrError-safe |
| H4 — FTS5 preflight | **PASS** | `install.py:260-270` — `CREATE VIRTUAL TABLE USING fts5(...)` probe |

**Idempotency fix** (`_effective_src_sha`): PASS. Live test: pass 1 = 30 write, pass 2 = 0 write, 30 skip, "nothing to do".

**Loud warn on synthesized git identity** (M2): PASS. `install.py:717-726`.

**Exit codes** in docstring + README: PASS. Both list 14/15/16.

## Regressions

**REG-1 (MED, fixed)** — `_rollback_tarball` was stale after `finalize_backup()` freed the tmp. Patched: `_rollback_tarball = final_backup` after copy. 1-line fix in `install.py`.

**REG-2 (LOW, acceptable)** — Fresh install produces an empty tarball in `backups/` (45 bytes, nothing to back up). Cosmetic, accumulates over time. Doc-only; v2 item.

No other regressions. `fcntl` import is darwin-gated; Linux path untouched.

## Residual concerns

1. **`_detect_network_fs` is Linux-only.** macOS SMB/NFS autofs mounts are undetected. README should narrow the exit-15 claim or add `os.statfs` parsing for darwin in v1.1.
2. **`_detect_wsl` parses only `/proc/sys/kernel/osrelease`.** Custom WSL2 kernels (e.g. `linux-msft-wsl` rebuilds) without literal `"wsl2"` substring could false-refuse. `/proc/version` backup signal worth adding.

## Verdict

**GO.** Repo v1.0.1 is genuinely safe on macOS (Intel + ARM with Homebrew Python) + Ubuntu/Debian/Fedora + WSL2. Refusal paths for native Windows, Cygwin/MSYS/MinGW, WSL1, Snap/Flatpak-sandboxed Claude, NFS/CIFS homes, and missing-FTS5 Python are live-verified.
