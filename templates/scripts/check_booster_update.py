#!/usr/bin/env python3
"""
check_booster_update.py — SessionStart hook that surfaces Booster version drift.

Purpose:
  Users install Booster once, then the origin repo moves on. Without a
  reminder they end up weeks behind, missing policy fixes or new /supervise
  features. This hook runs on every SessionStart, cheaply checks whether
  the installed manifest's git_sha lags origin/<branch>, and either warns
  (default) or auto-reinstalls (opt-in via env).

Contract:
  Reads JSON from stdin (Claude Code SessionStart event — we ignore it,
  only need the trigger). Emits additionalContext via stdout per the
  SessionStart hook protocol when drift detected. Always exits 0 —
  never block session start.

CLI / Examples:
  # Dry-run against the installed manifest:
  python3 ~/.claude/scripts/check_booster_update.py --check
  # Manually run the auto-update:
  CLAUDE_BOOSTER_AUTO_UPDATE=1 python3 ~/.claude/scripts/check_booster_update.py --check

Limitations:
  - Needs `git` on PATH. Offline → silent exit 0.
  - Skips if manifest has no git_sha (tar-extracted installs, no repo).
  - `git fetch` has 5s timeout. Intermittent network → silent skip.
  - Does NOT run `git pull` — only fetch. We don't mutate the user's repo.

ENV / Files:
  CLAUDE_BOOSTER_AUTO_UPDATE=1  → run `python3 install.py --yes` when behind
  CLAUDE_BOOSTER_UPDATE_BRANCH  → override branch (default: manifest.git_branch)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_MANIFEST_PATH = Path.home() / ".claude" / ".booster-manifest.json"
FETCH_TIMEOUT = 5
INSTALL_TIMEOUT = 180


def _manifest_path() -> Path:
    """Respect env override so tests can point the hook at a tmpfile."""
    override = os.environ.get("CLAUDE_BOOSTER_MANIFEST_PATH")
    return Path(override) if override else DEFAULT_MANIFEST_PATH


def _load_manifest() -> dict | None:
    path = _manifest_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _git(repo: Path, *args: str, timeout: int = 3) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _count_behind(repo: Path, branch: str) -> int | None:
    if _git(repo, "fetch", "--quiet", "origin", branch, timeout=FETCH_TIMEOUT) is None:
        return None
    count = _git(repo, "rev-list", f"HEAD..origin/{branch}", "--count")
    try:
        return int(count) if count is not None else None
    except ValueError:
        return None


def _emit_additional_context(text: str) -> None:
    """SessionStart hook response shape per Claude Code docs."""
    payload = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    sys.stdout.write(json.dumps(payload))


def _maybe_auto_install(repo: Path) -> tuple[bool, str]:
    install_script = repo / "install.py"
    if not install_script.exists():
        return False, "install.py not found in repo"
    try:
        result = subprocess.run(
            [sys.executable, str(install_script), "--yes"],
            capture_output=True, text=True, timeout=INSTALL_TIMEOUT,
        )
        ok = result.returncode == 0
        return ok, (result.stdout or "")[-2000:] if ok else (result.stderr or "")[-2000:]
    except subprocess.TimeoutExpired:
        return False, "install.py timed out"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Run the check even without stdin (for manual use)")
    args = parser.parse_args(argv)
    # Drain stdin if SessionStart hook is feeding us event JSON.
    if not args.check and not sys.stdin.isatty():
        try:
            sys.stdin.read()
        except Exception:
            pass

    manifest = _load_manifest()
    if not manifest or "git_sha" not in manifest:
        return 0  # tar install or missing manifest — nothing to compare against

    repo_path = Path(manifest["repo_path"])
    if not (repo_path / ".git").exists():
        return 0
    if shutil.which("git") is None:
        return 0

    branch = os.environ.get("CLAUDE_BOOSTER_UPDATE_BRANCH") or manifest.get("git_branch", "main")
    behind = _count_behind(repo_path, branch)
    if behind is None:
        return 0  # fetch failed (offline) — stay silent
    if behind <= 0:
        return 0  # up to date

    if os.environ.get("CLAUDE_BOOSTER_AUTO_UPDATE") == "1":
        ok, tail = _maybe_auto_install(repo_path)
        if ok:
            _emit_additional_context(
                f"=== Claude Booster auto-updated ===\n"
                f"Pulled {behind} new commit(s) from origin/{branch}, ran install.py --yes. "
                f"Manifest now reflects latest git_sha."
            )
        else:
            _emit_additional_context(
                f"=== Claude Booster auto-update FAILED ===\n"
                f"{behind} commit(s) behind origin/{branch}, "
                f"CLAUDE_BOOSTER_AUTO_UPDATE=1 tried to install but failed.\n"
                f"Tail of install output:\n{tail}"
            )
        return 0

    _emit_additional_context(
        f"=== Claude Booster update available ===\n"
        f"Installed from {repo_path}\n"
        f"On branch {branch}, currently {behind} commit(s) behind origin/{branch}.\n"
        f"To update:  cd {repo_path} && python3 install.py --yes\n"
        f"To auto-update on session start, export CLAUDE_BOOSTER_AUTO_UPDATE=1."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
