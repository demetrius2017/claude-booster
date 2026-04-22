"""read_file tool: resolves path relative to fixtures_root, returns file content (sliced)."""
from __future__ import annotations

from pathlib import Path

READ_TOOL_SCHEMA = {
    "name": "read_file",
    "description": (
        "Read a file from the project. Use for inspecting source code, READMEs, configs. "
        "Returns file content as a string. Supports optional line-range slicing via offset/limit."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to project root."},
            "offset": {"type": "integer", "description": "1-indexed start line (optional)."},
            "limit": {"type": "integer", "description": "Max lines to return (optional)."},
        },
        "required": ["path"],
    },
}


def execute_read(input_dict: dict, fixtures_root: Path) -> str:
    raw_path = input_dict.get("path", "").strip()
    if not raw_path:
        return "ERROR: path is required"
    try:
        target = (fixtures_root / raw_path).resolve()
        root_resolved = fixtures_root.resolve()
        if not str(target).startswith(str(root_resolved)):
            return f"ERROR: path escapes project root: {raw_path}"
        if not target.exists():
            return f"ERROR: file not found: {raw_path}"
        if not target.is_file():
            return f"ERROR: not a file: {raw_path}"
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"

    offset = input_dict.get("offset")
    limit = input_dict.get("limit")
    if offset is None and limit is None:
        return content
    lines = content.splitlines()
    start = max(0, (offset or 1) - 1)
    end = start + limit if limit else len(lines)
    sliced = lines[start:end]
    return "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(sliced))
