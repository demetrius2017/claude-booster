#!/usr/bin/env python3
"""PostToolUse hook — BATCH mode, appends significant events to JSONL file.

Назначение:
    Быстрый (<5ms) хук для PostToolUse. НЕ пишет в SQLite.
    Записывает только значимые события (ошибки bash, git commit) в JSONL батч-файл.
    Обработка — в memory_session_end.py.

    Дополнительно: при Write/Edit отчётов (``*/reports/{consilium,audit}_*.md``)
    форкает fire-and-forget subprocess ``index_reports.py`` чтобы SQLite FTS-индекс
    оставался свежим для следующего ``/start``. Subprocess async → <5ms контракт
    хука сохраняется. Rationale: reports/audit_2026-04-13_indexing_strategy.md.

    Дополнительно: при Write в ``*/memory/*.md`` (кроме MEMORY.md) форкает
    fire-and-forget subprocess ``memory_mirror.py`` чтобы запись автоматически
    появлялась в rolling_memory.db и была доступна в кросс-сессионной памяти.
    Паттерн идентичен _maybe_trigger_index — async Popen, <5ms контракт сохраняется.

Контракт:
    Вход: JSON на stdin {tool_name, tool_input, tool_response, session_id, cwd}
    Выход: нет (exit 0 всегда)

Ограничения:
    - Не импортирует rolling_memory (экономит время старта)
    - Не открывает SQLite
    - Общий путь (Read / non-report Write / Bash) должен завершаться за <5ms
      от входа в main() до выхода. Измерено: ~0.002 ms median.
    - Matching-report-write путь форкает `index_reports.py` через subprocess
      и платит цену macOS `posix_spawn` ≈ 5 ms median / 30 ms p95. Это
      допустимо потому что:
        * срабатывает только на Write/Edit по маске `*/reports/{consilium,audit}_*.md`
        * ~1-2 раза за сессию (при /audit, /consilium, /handover)
        * сам Write tool занимает на порядок больше времени
    - `_maybe_trigger_index` форкает subprocess и сразу возвращается —
      индексация происходит в отдельном процессе, hook не ждёт.
    - `_maybe_mirror_memory` форкает `memory_mirror.py` при Write в memory/*.md
      (кроме MEMORY.md) и сразу возвращается — зеркалирование async.

ENV/Файлы:
    ~/.claude/memory_batch_{session_id}.jsonl — батч-файл (append)
    ~/.claude/logs/memory_hooks.log — логи (только при ошибках)
    ~/.claude/scripts/index_reports.py — дочерний процесс (fire-and-forget)
    ~/.claude/scripts/memory_mirror.py — дочерний процесс (fire-and-forget)
"""

import json
import os
import re
import sys

# Matches ~/Projects/<any>/reports/consilium_*.md or audit_*.md (with optional
# nesting). Compiled once per process. Case-sensitive — report filenames are
# canonical lowercase.
_REPORT_WRITE_PATTERN = re.compile(r"/reports/(?:consilium|audit)_[^/]*\.md$")
_INDEXER_SCRIPT = os.path.expanduser("~/.claude/scripts/index_reports.py")

# Matches */memory/*.md but NOT */memory/MEMORY.md — the index file is excluded.
_MEMORY_WRITE_PATTERN = re.compile(r"/memory/(?!MEMORY\.md)[^/]+\.md$")
_MIRROR_SCRIPT = os.path.expanduser("~/.claude/scripts/memory_mirror.py")

_WRITE_TOOLS = ("Write", "Edit")


def _spawn(script, *extra_args):
    """Fire-and-forget a script via Popen. Silent on all errors."""
    try:
        import subprocess
        subprocess.Popen(
            ["python3", script, *extra_args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        pass


def _maybe_trigger_index(tool_name: str, tool_input: object) -> None:
    """Fire-and-forget `index_reports.py` when a consilium/audit file is written.

    Called from ``main`` right after JSON parsing. Returns in well under 1 ms
    when the tool or path doesn't match. When it does match, spawns a detached
    subprocess via ``Popen`` and returns immediately — the hook never waits on
    the indexer.

    Silent on every error path. The rule-prose self-heal in
    ``~/.claude/rules/commands.md`` `/start` step 2 is the backup mechanism
    when this trigger misses (external edits, subprocess crash).
    """
    if tool_name not in _WRITE_TOOLS:
        return
    if not isinstance(tool_input, dict):
        return
    path = tool_input.get("file_path", "")
    if not isinstance(path, str) or not _REPORT_WRITE_PATTERN.search(path):
        return
    _spawn(_INDEXER_SCRIPT)


def _maybe_mirror_memory(tool_name: str, tool_input: object) -> None:
    """Fire-and-forget ``memory_mirror.py`` when a memory .md file is written.

    Called from ``main`` right after ``_maybe_trigger_index``. Returns in well
    under 1 ms when the tool or path doesn't match. When it does match, spawns
    a detached subprocess via ``Popen`` and returns immediately — the hook never
    waits on the mirror script.

    Skips MEMORY.md (the index file) via the compiled regex pattern.
    Silent on every error path.
    """
    if tool_name not in _WRITE_TOOLS:
        return
    if not isinstance(tool_input, dict):
        return
    path = tool_input.get("file_path", "")
    if not isinstance(path, str) or not _MEMORY_WRITE_PATTERN.search(path):
        return
    _spawn(_MIRROR_SCRIPT, path)


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        data = json.loads(raw)
    except Exception:
        return

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})
    session_id = data.get("session_id", "unknown")
    cwd = data.get("cwd", "")

    # Fire-and-forget: refresh the consilium/audit index when a report is written.
    # This runs BEFORE the event-logging branches so that a malformed report
    # write still triggers indexing (and the error branch still fires). See
    # reports/audit_2026-04-13_indexing_strategy.md for the scoring rationale.
    _maybe_trigger_index(tool_name, tool_input)

    # Fire-and-forget: mirror memory .md writes into rolling_memory.db so
    # "запомни" entries are immediately available in cross-session context.
    _maybe_mirror_memory(tool_name, tool_input)

    event = None

    # Bash with non-zero exit code -> error event
    if tool_name in ("Bash", "bash"):
        # tool_response may have exit_code or exitCode
        exit_code = None
        if isinstance(tool_response, dict):
            exit_code = tool_response.get("exit_code") or tool_response.get("exitCode")

        if exit_code is not None and exit_code != 0:
            command = ""
            if isinstance(tool_input, dict):
                command = tool_input.get("command", "")
            elif isinstance(tool_input, str):
                command = tool_input

            stderr = ""
            if isinstance(tool_response, dict):
                stderr = tool_response.get("stderr", "") or tool_response.get("output", "")
            elif isinstance(tool_response, str):
                stderr = tool_response

            event = {
                "event_type": "bash_error",
                "command": command[:500],
                "exit_code": exit_code,
                "stderr": str(stderr)[:500],
                "cwd": cwd,
            }

    # Git commit detection
    if tool_name in ("Bash", "bash"):
        command = ""
        if isinstance(tool_input, dict):
            command = tool_input.get("command", "")
        elif isinstance(tool_input, str):
            command = tool_input

        response_exit = 0
        if isinstance(tool_response, dict):
            response_exit = tool_response.get("exit_code", 0) or tool_response.get("exitCode", 0)

        if "git commit" in command and response_exit == 0:
            # Extract commit message if possible
            msg = ""
            output = ""
            if isinstance(tool_response, dict):
                output = tool_response.get("stdout", "") or tool_response.get("output", "")
            elif isinstance(tool_response, str):
                output = tool_response
            # Try to get message from command
            if ' -m "' in command:
                try:
                    msg = command.split(' -m "', 1)[1].split('"', 1)[0]
                except Exception:
                    pass
            elif " -m '" in command:
                try:
                    msg = command.split(" -m '", 1)[1].split("'", 1)[0]
                except Exception:
                    pass

            # Don't overwrite a bash_error event with a git_commit for the same invocation
            if event is None:
                event = {
                    "event_type": "git_commit",
                    "command": command[:500],
                    "message": msg[:200],
                    "output": str(output)[:300],
                    "cwd": cwd,
                }

    if event is None:
        return

    # Append to batch file
    try:
        batch_path = os.path.join(
            os.path.expanduser("~"), ".claude", f"memory_batch_{session_id}.jsonl"
        )
        with open(batch_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # Silently skip — must not block Claude
        pass


if __name__ == "__main__":
    main()
