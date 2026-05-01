#!/usr/bin/env python3
"""Claude Booster — self-installing deployer for ~/.claude/ configuration.

Installs rules, Python hook scripts, slash commands, agent protocols, and a
`settings.json` that wires them into Claude Code's hook system.

Contract:
  - Stdlib-only (no pip deps).
  - Atomic writes (tmp + os.replace).
  - `settings.json` merged via `_booster` namespace + `source: booster@<ver>`
    hook tags — user's own hooks/permissions/MCP servers survive untouched.
  - Per-user runtime data (`rolling_memory.db`, `history.jsonl`,
    `.credentials.json`, `projects/`, `cache/`, `sessions/`, ...) NEVER
    touched.
  - Backup of everything we will write lands in
    `~/.claude/backups/booster_install_<UTC>.tar.gz` before any mutation.
  - Manifest at `~/.claude/.booster-manifest.json` records installed files +
    SHA-256 for idempotency, rollback, future uninstall.

Flags:
  --dry-run    Print planned actions + settings.json diff, write nothing.
  --yes        Skip confirmation prompt.
  --force      Overwrite user-modified files we manage (logged).
  --version    Print version and exit.

Supported: Linux, macOS. Native Windows is refused in v1 (use WSL2).

Exit codes:
  0   success (or dry-run)
  10  Python < 3.8
  11  ~/.claude/ not writable
  12  Downgrade attempt (manifest version > source)
  13  Native Windows / Cygwin / MSYS / WSL1
  14  Sandboxed Claude Code (Snap / Flatpak)
  15  ~/.claude/ on a network filesystem (NFS / CIFS / SMB / sshfs)
  16  sqlite3 lacks FTS5 support
  20  Backup failed
  30  Write failed (rolled back)
  40  Settings merge failed (rolled back)
  130 User interrupted (Ctrl+C, rolled back)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import platform
import shutil
import signal
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

BOOSTER_VERSION = "1.3.0"
REPO_ROOT = Path(__file__).resolve().parent
TEMPLATES = REPO_ROOT / "templates"
CLAUDE_HOME = Path.home() / ".claude"
MANIFEST_PATH = CLAUDE_HOME / ".booster-manifest.json"
CONFIG_PATH = CLAUDE_HOME / ".booster-config.json"
BACKUP_DIR = CLAUDE_HOME / "backups"

# Directories we install into (relative to CLAUDE_HOME)
MANAGED_DIRS = ("rules", "scripts", "commands", "agents")
# Files we never touch under CLAUDE_HOME
NEVER_TOUCH = {
    ".credentials.json",
    "rolling_memory.db",
    "history.jsonl",
    "CLAUDE.md.backup",
}
# Directories we never touch / never back up
NEVER_TOUCH_DIRS = {
    "projects", "plugins", "cache", "sessions", "session-env",
    "file-history", "paste-cache", "image-cache", "chrome", "ide",
    "debug", "logs", "plans", "downloads", "scheduled-tasks", "backups",
}

_rollback_tarball: Path | None = None


# ──────────────────────────── utilities ────────────────────────────


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str, level: str = "INFO") -> None:
    sys.stdout.write(f"[{level}] {msg}\n")
    sys.stdout.flush()


def fail(code: int, msg: str) -> None:
    sys.stderr.write(f"[FATAL] {msg}\n")
    sys.exit(code)


def atomic_write(target: Path, data: bytes, mode: int = 0o644) -> None:
    """Write `data` to `target` via tmp + fsync + os.replace.

    On Darwin, additionally issue F_FULLFSYNC — plain fsync() does not
    guarantee platter flush on macOS, exposing a power-loss corruption
    window (SQLite docs §atomiccommit).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            if sys.platform == "darwin":
                try:
                    import fcntl
                    fcntl.fcntl(f.fileno(), fcntl.F_FULLFSYNC)
                except (OSError, AttributeError):
                    pass  # F_FULLFSYNC not supported on all filesystems
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ──────────────────────────── preflight ────────────────────────────


def _detect_network_fs(path: Path) -> str | None:
    """Return filesystem type string if `path` lives on a network FS, else None.

    SQLite WAL is documented-unsafe on NFS/CIFS (sqlite.org/wal.html §2.2).
    Parses /proc/mounts on Linux; returns None on other OSes (macOS network
    mounts are rarer and detectable via `statfs` in a follow-up).
    """
    try:
        if sys.platform.startswith("linux"):
            mounts = Path("/proc/mounts").read_text().splitlines()
            path_str = str(path.resolve())
            best_mount = ""
            best_fs: str | None = None
            for line in mounts:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point, fs_type = parts[1], parts[2]
                if path_str == mount_point or path_str.startswith(mount_point.rstrip("/") + "/"):
                    if len(mount_point) > len(best_mount):
                        best_mount = mount_point
                        best_fs = fs_type
            if best_fs in {
                "nfs", "nfs4", "cifs", "smbfs", "smb3",
                "fuse.sshfs", "fuse.rclone", "9p", "davfs",
            }:
                return best_fs
    except (OSError, ValueError):
        pass
    return None


def _detect_wsl() -> str | None:
    """Return "wsl1" / "wsl2" if running under WSL, else None."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        rel = Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        return None
    if "microsoft" not in rel and "wsl" not in rel:
        return None
    # WSL2 kernel identifies itself in /proc/version / osrelease.
    if "wsl2" in rel or "-wsl2" in rel or "microsoft-standard-wsl2" in rel:
        return "wsl2"
    return "wsl1"


def preflight() -> None:
    if sys.version_info < (3, 8):
        fail(10, f"Python 3.8+ required, got {sys.version.split()[0]}")

    sysname = platform.system()
    # C1: widen to Cygwin / MSYS2 / MinGW — equality check was bypassed.
    if sysname == "Windows" or sysname.startswith(("CYGWIN", "MSYS", "MINGW")):
        fail(
            13,
            "Native Windows / Cygwin / MSYS2 / MinGW is not supported in v1. "
            "Use WSL2: https://learn.microsoft.com/windows/wsl/install",
        )

    # C2+C3: WSL detection — refuse WSL1 (drvfs WAL corruption), warn WSL2.
    wsl = _detect_wsl()
    if wsl == "wsl1":
        fail(
            13,
            "WSL1 detected — SQLite WAL corrupts on drvfs. Upgrade to WSL2: "
            "wsl --set-version <distro> 2",
        )
    if wsl == "wsl2":
        log(
            "WSL2 detected. If Claude Code runs on the Windows host (not "
            "inside WSL), it reads %USERPROFILE%\\.claude and will NOT see "
            "this install. Install on the side where Claude Code actually "
            "runs.",
            "WARN",
        )

    # C4: Snap / Flatpak confinement — HOME differs from $HOME for the app.
    if os.environ.get("SNAP") or os.environ.get("SNAP_NAME"):
        fail(
            14,
            "Snap sandbox detected ($SNAP set). If Claude Code is the "
            "Snap-packaged app, its HOME is under ~/snap/<app>/common/ "
            "and is not writable from outside. Install Claude Code via "
            "deb/dmg/direct download, or run install.py from inside the "
            "sandbox.",
        )
    if os.environ.get("FLATPAK_ID"):
        fail(
            14,
            "Flatpak sandbox detected. Claude Code as a Flatpak uses a "
            "separate HOME that this installer cannot reach.",
        )

    try:
        CLAUDE_HOME.mkdir(parents=True, exist_ok=True)
        probe = CLAUDE_HOME / ".booster-write-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        fail(11, f"~/.claude/ not writable: {e}")

    if not TEMPLATES.is_dir():
        fail(1, f"templates/ dir missing at {TEMPLATES} — repo corrupted?")

    # C5: Network filesystem detection — SQLite WAL forbidden on NFS.
    net_fs = _detect_network_fs(CLAUDE_HOME)
    if net_fs:
        fail(
            15,
            f"~/.claude/ is on {net_fs} (network filesystem). SQLite WAL "
            "corrupts on network filesystems — rolling_memory.db cannot "
            "live here. Move ~/.claude/ to a local disk, or symlink it "
            "from a local path.",
        )

    # P2.4: FTS5 capability check — rolling_memory.py requires it.
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
        conn.close()
    except sqlite3.OperationalError:
        fail(
            16,
            "Python sqlite3 lacks FTS5 support. Install Python via Homebrew "
            "(macOS), apt/dnf (Linux), or compile SQLite with -DSQLITE_ENABLE_FTS5. "
            "The Claude Booster memory engine requires FTS5 for cross-project search.",
        )

    # v1.2.0 supervisor prereq: the `claude` CLI must be reachable via PATH
    # (supervisor.py subprocess-execs it to spawn the worker). Warn-only —
    # users can still install and use the rest of Booster without it; only
    # `/supervise run` will error at runtime.
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        log(
            "`claude` CLI not on PATH. /supervise <task> will fail at "
            "runtime (subprocess cannot spawn the worker). Install Claude "
            "Code from https://claude.com/claude-code — everything else in "
            "Booster works without it.",
            "WARN",
        )

    # OneDrive / Dropbox / iCloud detection (cloud-sync folder warning).
    sync_markers = ("onedrive", "dropbox", "google drive", "icloud")
    if any(m in str(CLAUDE_HOME).lower() for m in sync_markers):
        log(
            "~/.claude/ appears to be inside a cloud-sync folder — SQLite WAL "
            "can corrupt. Consider relocating.",
            "WARN",
        )


# ────────────────────────── template discovery ────────────────────


def enumerate_template_files() -> list[tuple[Path, Path]]:
    """Return list of (source, target) pairs for every template file."""
    pairs: list[tuple[Path, Path]] = []
    for managed in MANAGED_DIRS:
        src_dir = TEMPLATES / managed
        if not src_dir.is_dir():
            continue
        for src in src_dir.rglob("*"):
            if not src.is_file():
                continue
            if src.name.startswith(".") or "__pycache__" in src.parts:
                continue
            if src.name.endswith((".bak", ".pyc", ".pyo")):
                continue
            # Exclude test suites — supervisor ships tests in-repo but they
            # don't belong in the user's ~/.claude/ tree.
            if "tests" in src.parts:
                continue
            rel = src.relative_to(TEMPLATES)
            pairs.append((src, CLAUDE_HOME / rel))
    return pairs


# ─────────────────────────── state detection ───────────────────────


def load_manifest() -> dict | None:
    if not MANIFEST_PATH.exists():
        return None
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except Exception as e:
        log(f"manifest unreadable ({e}) — treating as missing", "WARN")
        return None


def classify_state(manifest: dict | None) -> str:
    if not CLAUDE_HOME.exists() or not any(
        (CLAUDE_HOME / d).exists() for d in MANAGED_DIRS
    ):
        return "FRESH"
    if manifest is None:
        return "NON_BOOSTER"
    if manifest.get("version") == BOOSTER_VERSION:
        return "BOOSTER_SAME"
    return "BOOSTER_OLD"


# ─────────────────────────── backup ────────────────────────────────


def make_backup() -> Path:
    """Tarball everything we intend to touch. Excludes runtime/user data.

    C6: stage the backup tarball in $TMPDIR (local tmpfs), NOT in
    `~/.claude/backups/`. If CLAUDE_HOME lives on an external / network
    drive that unmounts mid-install, the rollback target would otherwise
    be on the same failed volume. `finalize_backup()` copies the tarball
    into `BACKUP_DIR` after a successful install.
    """
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tmp_tarball = Path(tempfile.gettempdir()) / f"booster_install_{stamp}.tar.gz"

    def filt(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = info.name
        parts = name.split("/")
        if parts and parts[0] == ".claude":
            rel_parts = parts[1:]
        else:
            rel_parts = parts
        if rel_parts and rel_parts[0] in NEVER_TOUCH_DIRS:
            return None
        if rel_parts and rel_parts[0] in NEVER_TOUCH:
            return None
        if any(p.startswith("rolling_memory.db") for p in rel_parts):
            return None
        return info

    with tarfile.open(tmp_tarball, "w:gz") as tar:
        for entry in MANAGED_DIRS + ("settings.json", "CLAUDE.md"):
            src = CLAUDE_HOME / entry
            if src.exists():
                tar.add(src, arcname=f".claude/{entry}", filter=filt)
    return tmp_tarball


def finalize_backup(tmp_tarball: Path) -> Path:
    """Copy tmp-staged backup into CLAUDE_HOME/backups/ after success."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    final = BACKUP_DIR / tmp_tarball.name
    try:
        shutil.copy2(tmp_tarball, final)
        tmp_tarball.unlink(missing_ok=True)
        return final
    except OSError as e:
        log(
            f"could not copy backup to {final} ({e}); backup remains at "
            f"{tmp_tarball} — move it somewhere durable.",
            "WARN",
        )
        return tmp_tarball


def restore_backup(tarball: Path) -> None:
    if not tarball or not tarball.exists():
        log("no backup to restore from", "ERROR")
        return
    log(f"rolling back from {tarball}", "WARN")
    # wipe managed dirs then extract
    for d in MANAGED_DIRS:
        target = CLAUDE_HOME / d
        if target.exists():
            shutil.rmtree(target)
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(path=Path.home())


# ─────────────────────────── settings merge ────────────────────────


BOOSTER_SOURCE = f"booster@{BOOSTER_VERSION}"


def _strip_booster_entries(hooks: dict) -> dict:
    out = {}
    for hook_type, entries in hooks.items():
        new_entries = []
        for entry in entries:
            hs = entry.get("hooks", [])
            filtered = [h for h in hs if not str(h.get("source", "")).startswith("booster@")]
            if filtered:
                new_entry = dict(entry)
                new_entry["hooks"] = filtered
                new_entries.append(new_entry)
            elif not hs:
                # preserve untouched (no embedded booster tag)
                new_entries.append(entry)
        if new_entries:
            out[hook_type] = new_entries
    return out


def _resolve_python() -> str:
    """Pick the most stable python3 path for hook commands.

    Prefer `shutil.which("python3")` over `sys.executable` because:
      - Homebrew ships a stable `/opt/homebrew/bin/python3` symlink that
        survives `brew upgrade python@3.x → 3.y` (sys.executable resolves
        to the versioned Cellar path, which disappears).
      - apt/dnf keep `/usr/bin/python3` pointing at the active version.
      - pyenv shims stay at `~/.pyenv/shims/python3`.
      - NixOS: `python3` via PATH, not the nix-store hash that changes on
        every `nixos-rebuild switch`.
    Falls back to `sys.executable` when `python3` is not on PATH.
    """
    stable = shutil.which("python3")
    if stable:
        return stable
    return sys.executable


def _render_booster_settings() -> dict:
    raw = (TEMPLATES / "settings.json.template").read_text()
    # Windows-aware path escaping for JSON: forward slashes work on both
    # POSIX and NT and don't collide with JSON escape sequences like \U.
    claude_home_json = str(CLAUDE_HOME).replace("\\", "/")
    python_json = _resolve_python().replace("\\", "/")
    raw = raw.replace("${BOOSTER_VERSION}", BOOSTER_VERSION)
    raw = raw.replace("${INSTALLED_AT}", now_iso())
    raw = raw.replace("${CLAUDE_HOME}", claude_home_json)
    raw = raw.replace("${PYTHON}", python_json)
    return json.loads(raw)


def merge_settings(user: dict, booster: dict) -> dict:
    """Merge booster template into user settings; preserve user fields.

    Rules:
      - installer owns top-level `_booster` key.
      - installer owns hooks with `source` starting `booster@`.
      - user's permissions.*, additionalDirectories, enabledPlugins, env,
        mcpServers are preserved verbatim.
      - for hooks: strip old booster-sourced entries, append new ones.
    """
    result = json.loads(json.dumps(user))  # deep copy

    # hooks: strip then extend
    user_hooks = result.get("hooks", {})
    cleaned = _strip_booster_entries(user_hooks)
    for hook_type, entries in booster.get("hooks", {}).items():
        cleaned.setdefault(hook_type, []).extend(entries)
    if cleaned:
        result["hooks"] = cleaned

    # permissions: only seed if user has none; else union allow/ask/deny
    if "permissions" not in result:
        result["permissions"] = booster.get("permissions", {})
    else:
        booster_perms = booster.get("permissions", {})
        # H2: union all three list keys, not just `allow`. User's rm-guards
        # (ask list) would silently drop if we only merged allow.
        for perm_key in ("allow", "ask", "deny"):
            merged = set(result["permissions"].get(perm_key, []))
            for p in booster_perms.get(perm_key, []):
                merged.add(p)
            if merged:
                result["permissions"][perm_key] = sorted(merged)
        result["permissions"].setdefault(
            "defaultMode", booster_perms.get("defaultMode", "auto")
        )
        result["permissions"].setdefault("additionalDirectories", [])

    # env
    user_env = result.setdefault("env", {})
    for k, v in booster.get("env", {}).items():
        user_env.setdefault(k, v)

    # enabledPlugins, mcpServers — preserve user verbatim, do not inject
    result.setdefault("enabledPlugins", {})

    # top-level simple keys: adopt booster default only when user has no value.
    # User overrides (e.g., effortLevel=max, skipAutoPermissionPrompt=true)
    # are preserved verbatim.
    for key in ("effortLevel", "skipAutoPermissionPrompt"):
        if key in booster and key not in result:
            result[key] = booster[key]

    # ownership marker
    result["_booster"] = booster["_booster"]

    return result


def _redact_for_diff(settings: dict) -> dict:
    """Redact secrets (mcpServer tokens etc.) before printing diff."""
    s = json.loads(json.dumps(settings))
    for server_name, cfg in s.get("mcpServers", {}).items():
        if isinstance(cfg, dict):
            for k in list(cfg.keys()):
                if any(tok in k.lower() for tok in ("token", "key", "secret", "password")):
                    cfg[k] = "***REDACTED***"
            env = cfg.get("env", {})
            for k in list(env.keys()):
                if any(tok in k.lower() for tok in ("token", "key", "secret", "password")):
                    env[k] = "***REDACTED***"
    return s


# ─────────────────────────── install core ──────────────────────────


def plan(
    pairs: list[tuple[Path, Path]],
    manifest: dict | None,
    force: bool,
    subs: dict[str, str],
) -> dict:
    """Build action plan: write / skip (idempotent) / preserve (user-modified).

    Compares the installer's effective output (after {{...}} substitution)
    against the currently-installed file. Using raw-template sha here
    breaks idempotency — see _effective_src_sha().
    """
    to_write: list[tuple[Path, Path]] = []
    to_skip: list[Path] = []
    to_preserve: list[Path] = []

    prev_shas = {}
    if manifest:
        for rec in manifest.get("files", []):
            prev_shas[rec["path"]] = rec["sha256"]

    for src, dst in pairs:
        rel = str(dst.relative_to(CLAUDE_HOME))
        src_sha = _effective_src_sha(src, subs)
        if not dst.exists():
            to_write.append((src, dst))
            continue
        dst_sha = sha256(dst)
        if dst_sha == src_sha:
            to_skip.append(dst)
            continue
        # dst exists and differs from src
        prev_sha = prev_shas.get(rel)
        if prev_sha and dst_sha == prev_sha:
            # user hasn't modified since last install — safe to overwrite
            to_write.append((src, dst))
        else:
            # no manifest record OR user modified — preserve unless --force
            if force:
                to_write.append((src, dst))
            else:
                to_preserve.append(dst)
    return {"write": to_write, "skip": to_skip, "preserve": to_preserve}


def _apply_substitutions(data: bytes, subs: dict[str, str]) -> bytes:
    """Substitute {{KEY}} placeholders in text files. Binary-safe."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    for k, v in subs.items():
        text = text.replace("{{" + k + "}}", v)
    return text.encode("utf-8")


def _effective_src_bytes(src: Path, subs: dict[str, str]) -> bytes:
    """Bytes the installer WOULD write for this template, after substitution.

    Critical for idempotency: plan() must compare the post-substitution
    output against the currently-installed file, not the raw template
    (which still has {{GIT_AUTHOR_NAME}} placeholders). Without this,
    every re-run sees .md files as changed and rewrites them.
    """
    data = src.read_bytes()
    if src.suffix in (".md", ".txt"):
        data = _apply_substitutions(data, subs)
    return data


def _effective_src_sha(src: Path, subs: dict[str, str]) -> str:
    return hashlib.sha256(_effective_src_bytes(src, subs)).hexdigest()


def write_all(to_write: list[tuple[Path, Path]], subs: dict[str, str]) -> list[dict]:
    records = []
    for src, dst in to_write:
        data = _effective_src_bytes(src, subs)
        is_script = src.name.endswith((".py", ".sh"))
        mode = 0o755 if is_script else 0o644
        atomic_write(dst, data, mode=mode)
        records.append({
            "path": str(dst.relative_to(CLAUDE_HOME)),
            "sha256": sha256(dst),
            "source": str(src.relative_to(REPO_ROOT)),
            "mode": oct(mode),
        })
    return records


def write_settings(dry_run: bool) -> tuple[dict, str]:
    target = CLAUDE_HOME / "settings.json"
    booster_settings = _render_booster_settings()

    if target.exists():
        try:
            user = json.loads(target.read_text())
        except json.JSONDecodeError as e:
            fail(40, f"existing settings.json is invalid JSON: {e}")
    else:
        user = {}

    merged = merge_settings(user, booster_settings)
    diff = "\n".join(
        difflib.unified_diff(
            json.dumps(_redact_for_diff(user), indent=2, sort_keys=True).splitlines(),
            json.dumps(_redact_for_diff(merged), indent=2, sort_keys=True).splitlines(),
            fromfile="settings.json (current)",
            tofile="settings.json (after install)",
            lineterm="",
        )
    )

    if not dry_run:
        atomic_write(target, (json.dumps(merged, indent=2) + "\n").encode(), 0o644)

    return merged, diff


def _git_state(repo: Path) -> dict:
    """Capture repo/sha/branch so check_booster_update.py can detect drift.

    Returns empty dict when repo isn't a git checkout (e.g. tar-extracted).
    """
    if not (repo / ".git").exists():
        return {}
    import subprocess
    try:
        sha = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, timeout=3,
        ).strip()
        branch = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True, timeout=3,
        ).strip()
        remote = subprocess.check_output(
            ["git", "-C", str(repo), "remote", "get-url", "origin"], text=True, timeout=3,
        ).strip()
        return {"repo_path": str(repo), "git_sha": sha, "git_branch": branch, "git_remote": remote}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return {"repo_path": str(repo)}


def write_manifest(files: list[dict], settings_sha: str) -> None:
    manifest = {
        "version": BOOSTER_VERSION,
        "installed_at": now_iso(),
        "python": sys.executable,
        "platform": platform.system(),
        "files": files,
        "settings_sha256": settings_sha,
        "settings_patch_ids": [f"booster@{BOOSTER_VERSION}"],
        **_git_state(REPO_ROOT),  # repo_path / git_sha / git_branch / git_remote
    }
    atomic_write(
        MANIFEST_PATH,
        (json.dumps(manifest, indent=2) + "\n").encode(),
        0o644,
    )


# ─────────────────────────── main ──────────────────────────────────


def _detect_git_identity() -> tuple[str, str]:
    """Read ~/.gitconfig user.name/email via git (if available)."""
    import subprocess
    name = email = ""
    try:
        name = subprocess.check_output(
            ["git", "config", "--global", "--get", "user.name"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
    except Exception:
        pass
    try:
        email = subprocess.check_output(
            ["git", "config", "--global", "--get", "user.email"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
    except Exception:
        pass
    return name, email


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def prompt_identity(
    cli_name: str | None,
    cli_email: str | None,
    non_interactive: bool,
) -> dict:
    """Resolve git identity. Precedence: CLI args > config file > git-config > prompt."""
    config = load_config()
    name = cli_name or config.get("git_author_name")
    email = cli_email or config.get("git_author_email")

    if not name or not email:
        git_name, git_email = _detect_git_identity()
        name = name or git_name
        email = email or git_email

    if non_interactive:
        synthesized = []
        if not name:
            name = os.environ.get("USER", "claude-booster-user")
            synthesized.append("name")
        if not email:
            email = f"{name}@users.noreply.github.com"
            synthesized.append("email")
        if synthesized:
            # M2: loud warn when installer had to guess identity. Vercel and
            # some CI pipelines deploy only from a specific author — a silent
            # fake identity causes later deploys to fail mysteriously.
            log(
                f"git author {'+'.join(synthesized)} synthesized "
                f"({name} <{email}>). Override with --name/--email or "
                "`git config --global user.name/email` before first commit.",
                "WARN",
            )
    else:
        if not name:
            default = os.environ.get("USER", "")
            ans = input(f"Git author name [{default}]: ").strip()
            name = ans or default or "claude-booster-user"
        if not email:
            default = f"{name}@users.noreply.github.com"
            ans = input(f"Git author email [{default}]: ").strip()
            email = ans or default

    cfg = {"git_author_name": name, "git_author_email": email}
    atomic_write(CONFIG_PATH, (json.dumps(cfg, indent=2) + "\n").encode(), 0o600)
    return cfg


def _sigint(_sig, _frm):
    log("interrupted — rolling back", "WARN")
    if _rollback_tarball:
        try:
            restore_backup(_rollback_tarball)
        except Exception as e:
            log(f"rollback failed: {e}", "ERROR")
    sys.exit(130)


def main() -> int:
    global _rollback_tarball
    ap = argparse.ArgumentParser(
        prog="install.py",
        description="Install Claude Booster into ~/.claude/",
    )
    ap.add_argument("--dry-run", action="store_true", help="show plan, write nothing")
    ap.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    ap.add_argument("--force", action="store_true", help="overwrite user-modified files")
    ap.add_argument("--name", help="git author name (skips prompt)")
    ap.add_argument("--email", help="git author email (skips prompt)")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args()

    if args.version:
        print(f"Claude Booster {BOOSTER_VERSION}")
        return 0

    signal.signal(signal.SIGINT, _sigint)

    log(f"Claude Booster {BOOSTER_VERSION}")
    log(f"CLAUDE_HOME = {CLAUDE_HOME}")
    log(f"PYTHON      = {_resolve_python()}")

    preflight()

    identity = prompt_identity(args.name, args.email, non_interactive=args.yes or args.dry_run)
    subs = {
        "GIT_AUTHOR_NAME": identity["git_author_name"],
        "GIT_AUTHOR_EMAIL": identity["git_author_email"],
    }
    log(f"git author  = {subs['GIT_AUTHOR_NAME']} <{subs['GIT_AUTHOR_EMAIL']}>")

    manifest = load_manifest()
    if manifest:
        installed_ver = manifest.get("version", "?")
        if installed_ver > BOOSTER_VERSION and not args.force:
            fail(
                12,
                f"installed manifest version {installed_ver} > source "
                f"{BOOSTER_VERSION}. Use --force to downgrade.",
            )

    state = classify_state(manifest)
    log(f"state       = {state}")

    pairs = enumerate_template_files()
    actions = plan(pairs, manifest, args.force, subs)
    log(
        f"files plan  = {len(actions['write'])} write, "
        f"{len(actions['skip'])} skip, "
        f"{len(actions['preserve'])} preserve (user-modified)"
    )
    if actions["preserve"] and not args.force:
        log(
            f"{len(actions['preserve'])} file(s) differ from template but "
            "are not tracked by a prior manifest — preserved as user-owned. "
            "Use --force to overwrite.",
            "WARN",
        )

    if not actions["write"] and state == "BOOSTER_SAME":
        # Even when no files changed, settings.json.template may have been
        # edited (new allow-list patterns, hooks, env vars). Check for drift
        # and short-circuit only if the merged settings are byte-identical
        # to what's already on disk.
        settings_target = CLAUDE_HOME / "settings.json"
        current_bytes = settings_target.read_bytes() if settings_target.exists() else b""
        merged_preview, _ = write_settings(dry_run=True)
        preview_bytes = (json.dumps(merged_preview, indent=2) + "\n").encode()
        if current_bytes == preview_bytes:
            log("nothing to do — already at current version")
            return 0
        log("settings.json drift detected — will rewrite")

    if args.dry_run:
        log("=== DRY RUN ===")
        for _, dst in actions["write"]:
            print(f"  WRITE    {dst.relative_to(CLAUDE_HOME)}")
        for dst in actions["preserve"]:
            print(f"  PRESERVE {dst.relative_to(CLAUDE_HOME)} (user-modified)")
        for dst in actions["skip"][:5]:
            print(f"  SKIP     {dst.relative_to(CLAUDE_HOME)}")
        if len(actions["skip"]) > 5:
            print(f"  SKIP     ... +{len(actions['skip']) - 5} more")
        _, diff = write_settings(dry_run=True)
        if diff:
            print("\n--- settings.json diff (secrets redacted) ---")
            print(diff)
        else:
            print("\n--- settings.json: no changes ---")
        return 0

    if not args.yes:
        ans = input(f"Proceed with install into {CLAUDE_HOME}? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            log("aborted by user")
            return 0

    # backup — staged in $TMPDIR, finalized to BACKUP_DIR only on success
    try:
        _rollback_tarball = make_backup()
        log(f"backup (tmp) = {_rollback_tarball}")
    except Exception as e:
        fail(20, f"backup failed: {e}")

    # write files
    try:
        records = write_all(actions["write"], subs)
        # also record skipped files (already at target) so manifest is complete
        for dst in actions["skip"]:
            records.append({
                "path": str(dst.relative_to(CLAUDE_HOME)),
                "sha256": sha256(dst),
                "source": "(unchanged)",
                "mode": oct(dst.stat().st_mode & 0o777),
            })
    except Exception as e:
        log(f"write failed: {e}", "ERROR")
        restore_backup(_rollback_tarball)
        return 30

    # settings merge
    try:
        merged, _ = write_settings(dry_run=False)
        settings_sha = hashlib.sha256(
            json.dumps(merged, indent=2, sort_keys=True).encode()
        ).hexdigest()
    except SystemExit:
        raise
    except Exception as e:
        log(f"settings merge failed: {e}", "ERROR")
        restore_backup(_rollback_tarball)
        return 40

    # manifest
    write_manifest(records, settings_sha)
    log(f"manifest    = {MANIFEST_PATH}")

    # finalize backup (move from $TMPDIR → BACKUP_DIR); only after success.
    # Repoint _rollback_tarball at the finalized path so a late SIGINT /
    # exception still finds a valid tarball (REG-1 from fix-validation audit).
    if _rollback_tarball is not None:
        final_backup = finalize_backup(_rollback_tarball)
        _rollback_tarball = final_backup
        log(f"backup      = {final_backup}")

    log(f"installed {len(records)} files ({len(actions['write'])} written, {len(actions['skip'])} unchanged)")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
