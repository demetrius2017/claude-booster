#!/usr/bin/env python3
"""
Extract readable session context from Claude Code JSONL session files.

Purpose: give spawned agents access to the current (or any) session's
conversation — dialogue, code edits, tool calls — without the noise of
hook summaries, permission modes, and file-history snapshots.

Contract:
  Inputs:
    --session <uuid|current|latest>   Session ID or shortcut (default: current)
    --project-dir <path>              Override auto-detected project dir
    --tail <N>                        Only last N conversation turns
    --grep <pattern>                  Filter turns containing pattern (case-insensitive)
    --max-kb <N>                      Truncate output at ~N kilobytes (default: 400)
    --format <readable|json>          Output format (default: readable)
    --tools-only                      Show only tool_use/tool_result turns
    --no-thinking                     Strip <thinking> blocks from assistant messages
    --no-tool-results                 Strip tool_result content (keep tool_use calls)
  Output (stdout):
    Readable conversation log or JSON array of turns.
    Exit 0 on success, 1 on error with diagnostic on stderr.

CLI/Examples:
  # Current session, full context
  python3 ~/.claude/scripts/session_context.py

  # Last 10 turns
  python3 ~/.claude/scripts/session_context.py --tail 10

  # Search for reconcile-related discussion
  python3 ~/.claude/scripts/session_context.py --grep reconcile

  # Only code edits and their results
  python3 ~/.claude/scripts/session_context.py --tools-only --grep Edit

  # Specific session by UUID
  python3 ~/.claude/scripts/session_context.py --session abc123-def456

  # JSON output for programmatic use
  python3 ~/.claude/scripts/session_context.py --format json --tail 5

  # List subagents of current session (who ran, when, how big)
  python3 ~/.claude/scripts/session_context.py --subagents

  # Read a specific subagent's session by description keyword
  python3 ~/.claude/scripts/session_context.py --agent "Worker" --tail 20

  # Read a specific subagent by ID prefix
  python3 ~/.claude/scripts/session_context.py --agent "a2ebcd5d" --tail 10

Limitations:
  - Detects project dir from CWD-based hashing; may need --project-dir override
    for edge cases.
  - "current" session = most recently modified JSONL in project dir; breaks if
    two sessions write simultaneously.
  - Tool result content can be very large (full file reads); use --no-tool-results
    or --max-kb to cap output.

ENV/Files:
  - Reads: ~/.claude/projects/<project-hash>/<session-id>.jsonl
  - No writes, no side effects.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


NOISE_TYPES = frozenset({
    "last-prompt",
    "permission-mode",
    "file-history-snapshot",
    "ai-title",
})

SYSTEM_NOISE_SUBTYPES = frozenset({
    "stop_hook_summary",
    "turn_duration",
})

CODE_TOOLS = frozenset({"Edit", "Write", "Bash", "Read", "NotebookEdit"})


def find_project_dir(override: str | None = None) -> Path:
    """Locate the Claude project directory for CWD."""
    if override:
        p = Path(override)
        if not p.is_dir():
            raise FileNotFoundError(f"Project dir not found: {p}")
        return p

    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.is_dir():
        raise FileNotFoundError(f"~/.claude/projects/ not found")

    cwd = os.getcwd()

    slug_dash = cwd.replace("/", "-")
    candidate = claude_dir / slug_dash
    if candidate.is_dir():
        return candidate

    slug_normalized = cwd.replace("/", "-").replace("_", "-")
    candidate = claude_dir / slug_normalized
    if candidate.is_dir():
        return candidate

    cwd_lower = cwd.lower().replace("/", "-").replace("_", "-")
    for d in sorted(claude_dir.iterdir()):
        if d.is_dir():
            d_lower = d.name.lower().replace("_", "-")
            if cwd_lower == d_lower or cwd_lower in d_lower:
                return d

    raise FileNotFoundError(
        f"No project dir found for CWD={cwd}. Use --project-dir."
    )


def find_session_file(project_dir: Path, session: str) -> Path:
    """Resolve session shortcut to a JSONL file path."""
    if session in ("current", "latest"):
        jsonls = sorted(
            project_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not jsonls:
            raise FileNotFoundError(f"No .jsonl files in {project_dir}")
        return jsonls[0]

    direct = project_dir / f"{session}.jsonl"
    if direct.exists():
        return direct

    matches = list(project_dir.glob(f"{session}*.jsonl"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous session prefix '{session}', matches: "
            + ", ".join(m.stem for m in matches[:5])
        )

    raise FileNotFoundError(f"Session not found: {session} in {project_dir}")


def find_subagents_dir(project_dir: Path, session: str) -> Path:
    """Locate the subagents directory for a given session."""
    session_file = find_session_file(project_dir, session)
    subdir = session_file.parent / session_file.stem / "subagents"
    if not subdir.is_dir():
        raise FileNotFoundError(
            f"No subagents directory for session {session_file.stem}"
        )
    return subdir


def list_subagents(subagents_dir: Path) -> list[dict]:
    """List all subagents with their metadata, sorted by mtime."""
    agents = []
    for meta_file in sorted(subagents_dir.glob("*.meta.json")):
        agent_id = meta_file.name.replace(".meta.json", "")
        jsonl_file = subagents_dir / f"{agent_id}.jsonl"
        try:
            with open(meta_file) as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            meta = {}

        size_kb = jsonl_file.stat().st_size // 1024 if jsonl_file.exists() else 0
        mtime = jsonl_file.stat().st_mtime if jsonl_file.exists() else 0

        agents.append({
            "id": agent_id,
            "type": meta.get("agentType", "?"),
            "description": meta.get("description", "?"),
            "size_kb": size_kb,
            "mtime": mtime,
            "jsonl_path": str(jsonl_file),
        })

    agents.sort(key=lambda a: a["mtime"])
    return agents


def find_agent_jsonl(subagents_dir: Path, query: str) -> Path:
    """Find a subagent JSONL by description keyword or ID prefix."""
    agents = list_subagents(subagents_dir)
    if not agents:
        raise FileNotFoundError("No subagents found")

    by_id = [a for a in agents if query.lower() in a["id"].lower()]
    if len(by_id) == 1:
        return Path(by_id[0]["jsonl_path"])

    by_desc = [a for a in agents if query.lower() in a["description"].lower()]
    if len(by_desc) == 1:
        return Path(by_desc[0]["jsonl_path"])
    if len(by_desc) > 1:
        by_desc.sort(key=lambda a: a["mtime"], reverse=True)
        return Path(by_desc[0]["jsonl_path"])

    if by_id:
        by_id.sort(key=lambda a: a["mtime"], reverse=True)
        return Path(by_id[0]["jsonl_path"])

    raise FileNotFoundError(
        f"No subagent matching '{query}'. Available:\n"
        + "\n".join(f"  {a['id']}: {a['description']}" for a in agents)
    )


def parse_jsonl(path: Path) -> list[dict]:
    """Parse JSONL file into list of dicts."""
    entries = []
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                print(
                    f"Warning: skipped malformed JSON at line {lineno}",
                    file=sys.stderr,
                )
    return entries


def is_noise(entry: dict) -> bool:
    """Return True if entry is metadata noise, not conversation content."""
    entry_type = entry.get("type", "")

    if entry_type in NOISE_TYPES:
        return True

    if entry_type == "system":
        subtype = entry.get("subtype", "")
        if subtype in SYSTEM_NOISE_SUBTYPES:
            return True

    if entry_type == "attachment":
        att = entry.get("attachment", {})
        if isinstance(att, dict):
            hook_name = att.get("hookName", "")
            if hook_name:
                return True

    return False


def extract_text_from_content(content, *, strip_thinking: bool = False) -> str:
    """Extract human-readable text from message content."""
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return str(content)

    parts = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue

        block_type = block.get("type", "")

        if block_type == "text":
            parts.append(block.get("text", ""))

        elif block_type == "thinking" and not strip_thinking:
            thinking = block.get("thinking", "")
            if thinking:
                parts.append(f"[thinking]\n{thinking}\n[/thinking]")

        elif block_type == "tool_use":
            name = block.get("name", "?")
            inp = block.get("input", {})
            tool_id = block.get("id", "")

            if name == "Edit":
                fp = inp.get("file_path", "?")
                old = inp.get("old_string", "")
                new = inp.get("new_string", "")
                replace_all = inp.get("replace_all", False)
                header = f"Edit {fp}"
                if replace_all:
                    header += " (replace_all)"
                parts.append(
                    f"[tool: {header}]\n"
                    f"--- old ---\n{old}\n"
                    f"+++ new +++\n{new}\n"
                    f"[/tool]"
                )
            elif name == "Write":
                fp = inp.get("file_path", "?")
                content_str = inp.get("content", "")
                preview = content_str[:2000]
                suffix = (
                    f"\n... ({len(content_str)} chars total)"
                    if len(content_str) > 2000
                    else ""
                )
                parts.append(
                    f"[tool: Write {fp}]\n{preview}{suffix}\n[/tool]"
                )
            elif name == "Bash":
                cmd = inp.get("command", "")
                desc = inp.get("description", "")
                label = f"Bash: {desc}" if desc else "Bash"
                parts.append(f"[tool: {label}]\n{cmd}\n[/tool]")
            elif name == "Read":
                fp = inp.get("file_path", "?")
                parts.append(f"[tool: Read {fp}]")
            elif name == "Agent":
                desc = inp.get("description", "")
                prompt = inp.get("prompt", "")
                model = inp.get("model", "default")
                preview = prompt[:500]
                suffix = (
                    f"\n... ({len(prompt)} chars)"
                    if len(prompt) > 500
                    else ""
                )
                parts.append(
                    f"[tool: Agent '{desc}' model={model}]\n"
                    f"{preview}{suffix}\n[/tool]"
                )
            else:
                inp_str = json.dumps(inp, ensure_ascii=False)
                if len(inp_str) > 500:
                    inp_str = inp_str[:500] + "..."
                parts.append(f"[tool: {name}]\n{inp_str}\n[/tool]")

        elif block_type == "tool_result":
            tool_id = block.get("tool_use_id", "")
            result_content = block.get("content", "")
            if isinstance(result_content, str):
                text = result_content
            elif isinstance(result_content, list):
                text_parts = []
                for sub in result_content:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        text_parts.append(sub.get("text", ""))
                text = "\n".join(text_parts)
            else:
                text = str(result_content)

            if len(text) > 3000:
                text = text[:1500] + f"\n... [{len(text)} chars total] ...\n" + text[-500:]

            parts.append(f"[result]\n{text}\n[/result]")

    return "\n".join(parts)


def entry_to_turn(
    entry: dict,
    *,
    strip_thinking: bool = False,
    include_tool_results: bool = True,
) -> dict | None:
    """Convert a JSONL entry to a structured turn dict, or None if noise."""
    if is_noise(entry):
        return None

    entry_type = entry.get("type", "")
    timestamp = entry.get("timestamp", "")

    if entry_type == "user":
        msg = entry.get("message", {})
        content = msg.get("content", "")
        text = extract_text_from_content(
            content, strip_thinking=strip_thinking
        )

        if not include_tool_results and "[result]" in text:
            result_free_parts = []
            for block in (content if isinstance(content, list) else [content]):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    continue
                result_free_parts.append(block)
            text = extract_text_from_content(
                result_free_parts, strip_thinking=strip_thinking
            )

        if not text.strip():
            return None

        has_tool_results = False
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    has_tool_results = True
                    break

        return {
            "role": "user",
            "timestamp": timestamp,
            "text": text,
            "has_tools": has_tool_results,
        }

    if entry_type == "assistant":
        msg = entry.get("message", {})
        content = msg.get("content", [])
        text = extract_text_from_content(
            content, strip_thinking=strip_thinking
        )
        if not text.strip():
            return None

        has_tools = False
        tool_names = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    has_tools = True
                    tool_names.append(block.get("name", "?"))

        return {
            "role": "assistant",
            "timestamp": timestamp,
            "text": text,
            "has_tools": has_tools,
            "tool_names": tool_names,
        }

    if entry_type == "system":
        subtype = entry.get("subtype", "")
        if subtype not in SYSTEM_NOISE_SUBTYPES:
            return {
                "role": "system",
                "timestamp": timestamp,
                "text": f"[system: {subtype}]",
            }

    return None


def format_readable(turns: list[dict]) -> str:
    """Format turns as a human-readable conversation log."""
    lines = []
    for turn in turns:
        role = turn["role"].upper()
        ts = turn.get("timestamp", "")
        if ts:
            ts_short = ts[11:19] if len(ts) >= 19 else ts
            header = f"── {role} ({ts_short}) "
        else:
            header = f"── {role} "
        header += "─" * max(0, 72 - len(header))
        lines.append(header)
        lines.append(turn["text"])
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Extract readable context from Claude Code session JSONL"
    )
    parser.add_argument(
        "--session",
        default="current",
        help="Session UUID, 'current', or 'latest' (default: current)",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Override auto-detected project directory",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=None,
        help="Only show last N conversation turns",
    )
    parser.add_argument(
        "--grep",
        default=None,
        help="Filter turns containing pattern (case-insensitive)",
    )
    parser.add_argument(
        "--max-kb",
        type=int,
        default=400,
        help="Truncate output at ~N kilobytes (default: 400)",
    )
    parser.add_argument(
        "--format",
        choices=["readable", "json"],
        default="readable",
        help="Output format (default: readable)",
    )
    parser.add_argument(
        "--tools-only",
        action="store_true",
        help="Show only turns with tool_use calls",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        help="Strip thinking blocks from assistant messages",
    )
    parser.add_argument(
        "--no-tool-results",
        action="store_true",
        help="Strip tool_result content (keep tool_use calls)",
    )
    parser.add_argument(
        "--subagents",
        action="store_true",
        help="List subagents of the session (id, description, size)",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Read a specific subagent by description keyword or ID prefix",
    )

    args = parser.parse_args()

    try:
        project_dir = find_project_dir(args.project_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.subagents:
        try:
            subdir = find_subagents_dir(project_dir, args.session)
            agents = list_subagents(subdir)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Subagents for session ({len(agents)} total):\n")
        for a in agents:
            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(a["mtime"], tz=timezone.utc).strftime("%H:%M:%S") if a["mtime"] else "?"
            print(f"  {a['id']}  ({a['size_kb']}KB, {ts})")
            print(f"    type: {a['type']}, desc: {a['description']}")
        sys.exit(0)

    try:
        if args.agent:
            subdir = find_subagents_dir(project_dir, args.session)
            session_file = find_agent_jsonl(subdir, args.agent)
        else:
            session_file = find_session_file(project_dir, args.session)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = session_file.stem
    entries = parse_jsonl(session_file)

    turns = []
    for entry in entries:
        turn = entry_to_turn(
            entry,
            strip_thinking=args.no_thinking,
            include_tool_results=not args.no_tool_results,
        )
        if turn is None:
            continue
        if args.tools_only and not turn.get("has_tools", False):
            if turn["role"] != "user":
                continue
        if args.grep:
            if not re.search(args.grep, turn["text"], re.IGNORECASE):
                continue
        turns.append(turn)

    if args.tail:
        turns = turns[-args.tail :]

    preamble = f"Session: {session_id}\nFile: {session_file}\nTurns: {len(turns)}\n\n"

    if args.format == "json":
        output = json.dumps(
            {"session_id": session_id, "turns": turns},
            ensure_ascii=False,
            indent=2,
        )
    else:
        output = preamble + format_readable(turns)

    max_bytes = args.max_kb * 1024
    if len(output.encode("utf-8")) > max_bytes:
        output_bytes = output.encode("utf-8")[:max_bytes]
        output = output_bytes.decode("utf-8", errors="ignore")
        output += f"\n\n... [truncated at {args.max_kb}KB]"

    print(output)


if __name__ == "__main__":
    main()
