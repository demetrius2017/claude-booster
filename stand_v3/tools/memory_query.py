"""query_memory tool: simulated stale-memory KB, with runtime invalidation flag."""
from __future__ import annotations

import json
from pathlib import Path

MEMORY_TOOL_SCHEMA = {
    "name": "query_memory",
    "description": (
        "Look up a topic in the memory knowledge base. May return stale data — "
        "verify with read_file/grep if the answer is important."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Topic keyword to look up."},
        },
        "required": ["topic"],
    },
}


def execute_memory(input_dict: dict, kb_path: Path, invalidated_topics: set[str]) -> str:
    topic = (input_dict.get("topic") or "").strip()
    if not topic:
        return "ERROR: topic is required"

    topic_lc = topic.lower()
    if "__ALL__" in invalidated_topics:
        return "MEMORY INVALIDATED for this topic. Use fresh tools (read_file, grep) to answer."
    for inv in invalidated_topics:
        if inv and (inv in topic_lc or topic_lc in inv):
            return "MEMORY INVALIDATED for this topic. Use fresh tools (read_file, grep) to answer."

    try:
        kb = json.loads(kb_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"ERROR: could not load memory KB: {exc}"

    entries = kb.get("entries", [])
    for entry in entries:
        entry_topic = str(entry.get("topic", "")).lower()
        if entry_topic and (entry_topic in topic_lc or topic_lc in entry_topic):
            return (
                f"[memory_hit topic={entry.get('topic')} last_updated={entry.get('last_updated')} "
                f"confidence={entry.get('confidence')}]\n{entry.get('answer', '')}"
            )
    return "No entry for this topic."
