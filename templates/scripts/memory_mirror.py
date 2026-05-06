#!/usr/bin/env python3
"""Mirror a memory .md file into rolling_memory.db via the memorize CLI.

Purpose:
    Reads a memory file written by a "запомни" command (or any Write to
    ``~/.claude/projects/-<hash>/memory/*.md``), parses its YAML frontmatter,
    and calls ``rolling_memory.py memorize`` so the entry is available across
    sessions via the cross-session SQLite memory engine.

    This script is forked fire-and-forget by ``memory_post_tool.py`` and MUST
    NOT be imported by it directly (no shared state).

Contract:
    Inputs:
        sys.argv[1]  — absolute path to the .md memory file to mirror
    Outputs:
        None (silent). Calls rolling_memory.py as subprocess; rolling_memory
        handles dedup via content_hash (same content → silently skipped).
    Exit codes:
        0 always (errors are swallowed — mirror is convenience, not critical).

CLI/Examples::

    # Mirror a single memory file (normally called by memory_post_tool.py):
    python3 memory_mirror.py ~/.claude/projects/-Users-alice-Projects-foo/memory/feedback_bar.md

    # Manual re-sync after a batch import:
    for f in ~/.claude/projects/-Users-alice-Projects-foo/memory/*.md; do
        [[ "$(basename "$f")" == "MEMORY.md" ]] && continue
        python3 memory_mirror.py "$f"
    done

Limitations:
    - MEMORY.md (the index file) is silently skipped — it is not a memory entry.
    - YAML frontmatter parser is regex-only (no pyyaml dependency) and handles
      only single-line ``key: value`` pairs; multi-line values are ignored.
    - The scope path reconstructed from the project hash may be wrong if the
      project directory was renamed after the hash was generated.
    - rolling_memory.py content_hash dedup ensures idempotency on re-runs.

ENV/Files:
    Input:   ~/.claude/projects/-<path-hash>/memory/<name>.md
    Calls:   ~/.claude/scripts/rolling_memory.py memorize ...
    Logs:    none — all errors are suppressed to avoid disrupting Claude.
"""

import os
import re
import subprocess
import sys

# Path to the rolling_memory CLI — same pattern as _INDEXER_SCRIPT in
# memory_post_tool.py.
_ROLLING_MEMORY_SCRIPT = os.path.expanduser("~/.claude/scripts/rolling_memory.py")

# Map from frontmatter ``type`` values to rolling_memory memory_type values.
# Keys cover all variants documented in the project; anything not listed falls
# back to "feedback" (safest default — always surfaced on /start).
_TYPE_MAP: dict = {
    "user": "feedback",
    "feedback": "feedback",
    "project": "project_context",
    "reference": "directive",
    "directive": "directive",
    "audit": "audit",
    "consilium": "consilium",
}

# Default values for mirrored entries (per Artifact Contract).
_DEFAULT_PRIORITY = "70"
_DEFAULT_SOURCE = "memory_mirror"


def _parse_frontmatter(text):
    # type: (str) -> tuple
    """Split ``text`` into (frontmatter_dict, body).

    Frontmatter is the YAML block between the first two ``---`` lines.
    Body is everything after the closing ``---``.

    Returns (``{}``, ``text``) when the file does not start with ``---``.
    """
    # stdlib-only — no pyyaml import; must work on clean installs without pip deps.
    if not text.startswith("---"):
        return {}, text

    # Find the closing --- after the opening one.
    # text[3:] skips the opening "---"; we search from position 4 so we
    # skip over a possible immediate newline.
    rest = text[3:]
    close_idx = rest.find("\n---")
    if close_idx == -1:
        return {}, text

    fm_block = rest[:close_idx]
    body = rest[close_idx + 4:]  # skip "\n---"
    # Strip optional trailing newline after the closing ---
    if body.startswith("\n"):
        body = body[1:]

    fm = {}
    for line in fm_block.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)", line)
        if m:
            fm[m.group(1).strip()] = m.group(2).strip()

    return fm, body


def _derive_scope(file_path):
    # type: (str) -> str
    """Derive a human-readable project path from the memory file path.

    Memory files live at::

        ~/.claude/projects/-Users-alice-Projects-foo/memory/some.md

    The hash segment ``-Users-alice-Projects-foo`` encodes the original project
    path: leading ``-`` represents ``/``, then every subsequent ``-`` represents
    ``/``.

    Example:
        ``-Users-dmitrijnazarov-Projects-horizon``
        -> ``/Users/dmitrijnazarov/Projects/horizon``

    Falls back to ``"global"`` if the path does not match the expected pattern.
    """
    # Match the path-hash component.
    m = re.search(r"/projects/(-[^/]+)/memory/", file_path)
    if not m:
        return "global"

    hash_segment = m.group(1)  # e.g. "-Users-foo-Projects-bar"

    # The leading "-" becomes the initial "/"; subsequent "-" become "/".
    # We cannot blindly replace every "-" because directory names may contain
    # hyphens.  The encoding used by Claude Code is: path separators become "-"
    # and the path starts with "-" (representing the root "/").  Since we cannot
    # unambiguously reverse hyphenated directory names from the hash alone, we do
    # a best-effort replacement that is correct for the common case (no hyphens
    # in directory names), consistent with how other tools in this repo treat it.
    path = "/" + hash_segment[1:].replace("-", "/")
    if not os.path.isdir(path):
        return "global"
    return path


def main():
    # type: () -> None
    """Entry point — mirror the file at sys.argv[1] into rolling_memory.db."""
    if len(sys.argv) < 2:
        return

    file_path = sys.argv[1]

    # Safety: never mirror the index file.
    if os.path.basename(file_path) == "MEMORY.md":
        return

    try:
        with open(file_path, encoding="utf-8") as fh:
            text = fh.read()
    except Exception:
        return

    try:
        fm, body = _parse_frontmatter(text)

        # Determine content: prefer body; fall back to description from
        # frontmatter; fall back to the whole file text.
        content = body.strip()
        if not content:
            content = fm.get("description", text.strip())
        if not content:
            return  # Nothing to store.

        # Map type.
        raw_type = fm.get("type", "feedback").lower()
        memory_type = _TYPE_MAP.get(raw_type, "feedback")

        # Derive scope from path.
        scope = _derive_scope(file_path)

        # Category: use the file name without extension as a stable identifier,
        # or fall back to "memory_mirror".
        category = os.path.splitext(os.path.basename(file_path))[0] or "memory_mirror"

        cmd = [
            "python3",
            _ROLLING_MEMORY_SCRIPT,
            "memorize",
            "--type", memory_type,
            "--content", content,
            "--priority", _DEFAULT_PRIORITY,
            "--scope", scope,
            "--category", category,
            "--source", _DEFAULT_SOURCE,
        ]

        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        # Silent — mirror is a convenience layer, not critical path.
        pass


if __name__ == "__main__":
    main()
