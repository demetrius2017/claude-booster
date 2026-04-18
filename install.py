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
  13  Native Windows
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
import sys
import tarfile
import tempfile
from pathlib import Path

BOOSTER_VERSION = "1.0.0"
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
    """Write `data` to `target` via tmp + fsync + os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ──────────────────────────── preflight ────────────────────────────


def preflight() -> None:
    if sys.version_info < (3, 8):
        fail(10, f"Python 3.8+ required, got {sys.version.split()[0]}")

    sysname = platform.system()
    if sysname == "Windows":
        fail(
            13,
            "Native Windows is not supported in v1.\n"
            "       Use WSL2: https://learn.microsoft.com/windows/wsl/install",
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

    # OneDrive / Dropbox / iCloud detection
    home_parts = [p.lower() for p in Path.home().parts]
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
    """Tarball everything we intend to touch. Excludes runtime/user data."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tarball = BACKUP_DIR / f"booster_install_{stamp}.tar.gz"

    def filt(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = info.name
        parts = name.split("/")
        # strip cwd prefix
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

    with tarfile.open(tarball, "w:gz") as tar:
        for entry in MANAGED_DIRS + ("settings.json", "CLAUDE.md"):
            src = CLAUDE_HOME / entry
            if src.exists():
                tar.add(src, arcname=f".claude/{entry}", filter=filt)
    return tarball


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


def _render_booster_settings() -> dict:
    raw = (TEMPLATES / "settings.json.template").read_text()
    raw = raw.replace("${BOOSTER_VERSION}", BOOSTER_VERSION)
    raw = raw.replace("${INSTALLED_AT}", now_iso())
    raw = raw.replace("${CLAUDE_HOME}", str(CLAUDE_HOME))
    raw = raw.replace("${PYTHON}", sys.executable)
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

    # permissions: only seed if user has none
    if "permissions" not in result:
        result["permissions"] = booster.get("permissions", {})
    else:
        # merge allow lists (union)
        booster_perms = booster.get("permissions", {})
        user_allow = set(result["permissions"].get("allow", []))
        for p in booster_perms.get("allow", []):
            user_allow.add(p)
        result["permissions"]["allow"] = sorted(user_allow)
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
) -> dict:
    """Build action plan: write / skip (idempotent) / preserve (user-modified)."""
    to_write: list[tuple[Path, Path]] = []
    to_skip: list[Path] = []
    to_preserve: list[Path] = []

    prev_shas = {}
    if manifest:
        for rec in manifest.get("files", []):
            prev_shas[rec["path"]] = rec["sha256"]

    for src, dst in pairs:
        rel = str(dst.relative_to(CLAUDE_HOME))
        src_sha = sha256(src)
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


def write_all(to_write: list[tuple[Path, Path]], subs: dict[str, str]) -> list[dict]:
    records = []
    for src, dst in to_write:
        data = src.read_bytes()
        if src.suffix in (".md", ".txt"):
            data = _apply_substitutions(data, subs)
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


def write_manifest(files: list[dict], settings_sha: str) -> None:
    manifest = {
        "version": BOOSTER_VERSION,
        "installed_at": now_iso(),
        "python": sys.executable,
        "platform": platform.system(),
        "files": files,
        "settings_sha256": settings_sha,
        "settings_patch_ids": [f"booster@{BOOSTER_VERSION}"],
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
        if not name:
            name = os.environ.get("USER", "claude-booster-user")
        if not email:
            email = f"{name}@users.noreply.github.com"
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
    log(f"PYTHON      = {sys.executable}")

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
    actions = plan(pairs, manifest, args.force)
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
        log("nothing to do — already at current version")
        return 0

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

    # backup
    try:
        _rollback_tarball = make_backup()
        log(f"backup      = {_rollback_tarball}")
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
    log(f"installed {len(records)} files ({len(actions['write'])} written, {len(actions['skip'])} unchanged)")
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
