"""grep tool: pure-Python recursive regex search under fixtures_root."""
from __future__ import annotations

import re
from pathlib import Path

GREP_TOOL_SCHEMA = {
    "name": "grep",
    "description": (
        "Search for a regex pattern in files under the project. "
        "Returns matching lines prefixed with 'file:line_no:'. Use for locating "
        "function definitions, references, and text across the codebase."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python-regex pattern."},
            "path": {"type": "string", "description": "Subdirectory relative to project root (default: '.')."},
        },
        "required": ["pattern"],
    },
}

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache"}
_MAX_MATCHES = 200


def execute_grep(input_dict: dict, fixtures_root: Path) -> str:
    pattern = input_dict.get("pattern", "")
    sub_path = input_dict.get("path", ".") or "."
    if not pattern:
        return "ERROR: pattern is required"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: invalid regex: {exc}"

    try:
        search_root = (fixtures_root / sub_path).resolve()
        root_resolved = fixtures_root.resolve()
        if not str(search_root).startswith(str(root_resolved)):
            return f"ERROR: path escapes project root: {sub_path}"
        if not search_root.exists():
            return f"ERROR: path not found: {sub_path}"
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"

    matches: list[str] = []
    targets = [search_root] if search_root.is_file() else list(_iter_files(search_root))
    for fpath in targets:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = fpath.relative_to(root_resolved)
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{rel}:{line_no}:{line}")
                if len(matches) >= _MAX_MATCHES:
                    matches.append(f"... (truncated at {_MAX_MATCHES} matches)")
                    return "\n".join(matches)
    if not matches:
        return "No matches found."
    return "\n".join(matches)


def _iter_files(root: Path):
    for p in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.is_file():
            yield p
