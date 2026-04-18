#!/usr/bin/env python3
"""InstructionsLoaded hook — log every instruction file load event.

Назначение:
    Phase 3 verification. Каждый раз когда harness загружает CLAUDE.md /
    CLAUDE.local.md / .claude/rules/*.md файл, hook срабатывает и пишет
    запись в лог. Позволяет эмпирически проверить, какие файлы реально
    попадают в system prompt на session_start vs lazy load.

Контракт:
    Вход: JSON на stdin — см. payload ниже.
    Выход: нет (exit 0 всегда, observability-only hook).

Payload (из https://code.claude.com/docs/en/hooks):
    session_id, cwd, hook_event_name=InstructionsLoaded,
    file_path, memory_type (User|Project|Local|Managed),
    load_reason (session_start|nested_traversal|path_glob_match|include|compact),
    globs (optional), trigger_file_path (optional), parent_file_path (optional).

ENV/Файлы:
    ~/.claude/logs/instructions_loaded.jsonl — лог событий (append-only JSONL)

Ограничения:
    Never crashes Claude — все ошибки проглатываются, exit 0.
"""

import json
import os
import sys
from datetime import datetime, timezone

LOG_PATH = os.path.expanduser("~/.claude/logs/instructions_loaded.jsonl")


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {"_parse_error": True}

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": payload.get("session_id", ""),
        "cwd": payload.get("cwd", ""),
        "file_path": payload.get("file_path", ""),
        "memory_type": payload.get("memory_type", ""),
        "load_reason": payload.get("load_reason", ""),
        "globs": payload.get("globs"),
        "trigger_file_path": payload.get("trigger_file_path"),
        "parent_file_path": payload.get("parent_file_path"),
    }

    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
