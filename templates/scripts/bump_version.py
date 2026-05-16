#!/usr/bin/env python3
"""
bump_version.py — Auto-version bumping based on conventional commits.

Purpose:
    Reads git log since last tag, detects the highest-priority semantic version
    bump type from commit subjects (feat!/BREAKING → major, feat: → minor,
    fix: → patch, everything else → no bump), updates the VERSION file, and
    creates an annotated git tag on HEAD.

Contract:
    Inputs:
        --show            Print current version state; exit 0 without changes.
        --dry-run         Print what would happen; exit 0 without changes.
        --bump major|minor|patch
                          Force a specific bump type regardless of commits.
        --set X.Y.Z       Set an explicit version; bypass commit analysis.
    Outputs:
        VERSION file updated (no 'v' prefix, newline-terminated).
        Annotated git tag 'v<new>' on HEAD created.
        Prints "Version: <old> → <new>" on success.
    Exit codes:
        0 — success, or no-op (nothing to bump / already at HEAD).
        1 — error (not in git repo, invalid args, git command failed).

CLI / Examples:
    python3 bump_version.py                 # auto-detect bump from commits
    python3 bump_version.py --show          # show state only
    python3 bump_version.py --dry-run       # preview without changes
    python3 bump_version.py --bump minor    # force minor bump
    python3 bump_version.py --set 2.0.0     # set explicit version
    python3 bump_version.py --bump patch --dry-run

Limitations:
    Does not push tags to remote — user pushes manually.
    Does not handle pre-release suffixes (alpha, rc, etc.).
    Reads commit subjects only; BREAKING CHANGE in body is NOT detected
    (use 'feat!:' or 'fix!:' prefix to signal a breaking change).

ENV:
    None required. Git must be available in PATH.
    Script auto-detects project root via .git/ directory presence.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    """Run a git command; raise SystemExit(1) on failure."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"ERROR: git {' '.join(args)} failed:\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def _git_ok(*args: str) -> tuple[bool, str]:
    """Run a git command; return (success, stdout). Never raises."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode == 0, result.stdout.strip()


def find_project_root() -> Path:
    """Walk up from CWD until .git/ is found. Exit 1 if not in a repo."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    print("ERROR: not inside a git repository (.git/ not found).", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(version_str: str) -> tuple[int, int, int]:
    """Parse 'X.Y.Z' or 'vX.Y.Z'; raise ValueError on bad format."""
    m = _VERSION_RE.match(version_str.strip())
    if not m:
        raise ValueError(f"Invalid version string: {version_str!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def format_version(major: int, minor: int, patch: int) -> str:
    """Return 'X.Y.Z' (no 'v' prefix)."""
    return f"{major}.{minor}.{patch}"


def apply_bump(
    major: int, minor: int, patch: int, bump_type: str
) -> tuple[int, int, int]:
    if bump_type == "major":
        return major + 1, 0, 0
    if bump_type == "minor":
        return major, minor + 1, 0
    if bump_type == "patch":
        return major, minor, patch + 1
    raise ValueError(f"Unknown bump_type: {bump_type!r}")


# ---------------------------------------------------------------------------
# Commit analysis
# ---------------------------------------------------------------------------

# Priority: major > minor > patch > none
_BUMP_PRIORITY = {"major": 3, "minor": 2, "patch": 1, "none": 0}

_BREAKING_RE = re.compile(r"^[a-z]+!:")   # feat!: fix!: etc.
_FEAT_RE = re.compile(r"^feat:")
_FIX_RE = re.compile(r"^fix:")


def classify_commit(subject: str) -> str:
    """Return 'major', 'minor', 'patch', or 'none' for one commit subject."""
    s = subject.strip()
    if _BREAKING_RE.match(s):
        return "major"
    if _FEAT_RE.match(s):
        return "minor"
    if _FIX_RE.match(s):
        return "patch"
    return "none"


def detect_bump_type(commits: list[str]) -> str:
    """Return the highest-priority bump type from a list of commit subjects."""
    best = "none"
    for subject in commits:
        t = classify_commit(subject)
        if _BUMP_PRIORITY[t] > _BUMP_PRIORITY[best]:
            best = t
            if best == "major":
                break  # can't go higher
    return best


# ---------------------------------------------------------------------------
# State resolution
# ---------------------------------------------------------------------------

def get_last_tag() -> str | None:
    """Return the most recent 'v*' tag, or None if no tags exist."""
    ok, out = _git_ok("describe", "--tags", "--abbrev=0", "--match", "v*")
    return out if ok and out else None


def get_commits_since(tag: str | None) -> list[str]:
    """Return commit subjects since tag (or all commits if tag is None)."""
    if tag:
        ok, out = _git_ok("log", f"{tag}..HEAD", "--pretty=format:%s")
    else:
        ok, out = _git_ok("log", "--pretty=format:%s")
    if not ok or not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def is_tag_at_head(tag: str) -> bool:
    """Return True if the given tag points to the current HEAD commit."""
    ok_tag, tag_rev = _git_ok("rev-list", "-n", "1", tag)
    ok_head, head_rev = _git_ok("rev-parse", "HEAD")
    return ok_tag and ok_head and tag_rev == head_rev


def read_version_file(root: Path) -> str | None:
    """Return the content of VERSION (stripped), or None if absent."""
    vf = root / "VERSION"
    if vf.exists():
        return vf.read_text(encoding="utf-8").strip()
    return None


def current_version_str(last_tag: str | None, version_file: str | None) -> str:
    """Resolve the authoritative current version string (without 'v' prefix)."""
    if last_tag:
        return last_tag.lstrip("v")
    if version_file:
        return version_file.lstrip("v")
    return "0.0.0"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def write_version_file(root: Path, version: str) -> None:
    """Write 'version\n' to VERSION file."""
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")


def create_tag(version: str, dry_run: bool) -> None:
    tag = f"v{version}"
    if dry_run:
        print(f"  [dry-run] would create annotated tag: {tag}")
        return
    _git("tag", "-a", tag, "-m", f"Release {tag}")
    print(f"  Created tag: {tag}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_show(root: Path) -> None:
    last_tag = get_last_tag()
    vf = read_version_file(root)
    commits = get_commits_since(last_tag)
    bump = detect_bump_type(commits)

    print(f"Last tag      : {last_tag or '(none)'}")
    print(f"VERSION file  : {vf or '(absent)'}")
    print(f"Commits since : {len(commits)}")
    if commits:
        for s in commits[:5]:
            print(f"  {s}")
        if len(commits) > 5:
            print(f"  ... and {len(commits) - 5} more")
    print(f"Detected bump : {bump}")


def cmd_auto(root: Path, forced_bump: str | None, dry_run: bool) -> None:
    last_tag = get_last_tag()
    vf = read_version_file(root)
    cur_str = current_version_str(last_tag, vf)

    # Already at HEAD check
    if last_tag and is_tag_at_head(last_tag):
        print(f"Already at {last_tag} (no new commits).")
        return

    commits = get_commits_since(last_tag)
    bump = forced_bump if forced_bump else detect_bump_type(commits)

    if bump == "none":
        print(f"No version-bumping commits since {last_tag or 'beginning'}.")
        return

    try:
        major, minor, patch = parse_version(cur_str)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    new_major, new_minor, new_patch = apply_bump(major, minor, patch, bump)
    new_str = format_version(new_major, new_minor, new_patch)

    print(f"Version: {cur_str} → {new_str}  [{bump}]")
    if dry_run:
        print(f"  [dry-run] would update VERSION file and create tag v{new_str}")
        return

    write_version_file(root, new_str)
    create_tag(new_str, dry_run=False)


def cmd_set(root: Path, version_str: str, dry_run: bool) -> None:
    try:
        major, minor, patch = parse_version(version_str)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    new_str = format_version(major, minor, patch)
    last_tag = get_last_tag()
    vf = read_version_file(root)
    cur_str = current_version_str(last_tag, vf)

    print(f"Version: {cur_str} → {new_str}  [explicit set]")
    if dry_run:
        print(f"  [dry-run] would update VERSION file and create tag v{new_str}")
        return

    write_version_file(root, new_str)
    create_tag(new_str, dry_run=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bump project version from conventional commits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Print current version state and exit.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would happen without making changes.",
    )
    parser.add_argument(
        "--bump", choices=["major", "minor", "patch"],
        help="Force a specific bump type.",
    )
    parser.add_argument(
        "--set", dest="set_version", metavar="X.Y.Z",
        help="Set an explicit version, bypassing commit analysis.",
    )
    args = parser.parse_args()

    # Validation: --bump and --set are mutually exclusive
    if args.bump and args.set_version:
        print("ERROR: --bump and --set are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    root = find_project_root()

    if args.show:
        cmd_show(root)
    elif args.set_version:
        cmd_set(root, args.set_version, dry_run=args.dry_run)
    else:
        cmd_auto(root, forced_bump=args.bump, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
