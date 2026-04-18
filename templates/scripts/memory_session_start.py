#!/usr/bin/env python3
"""SessionStart hook — inject Rolling Memory context into Claude session.

Назначение:
    Читает stdin JSON от Claude Code CLI (session_id, cwd),
    инициализирует БД, делает backup, чистит expired,
    строит контекст и выводит JSON с additionalContext.

Контракт:
    Вход: JSON на stdin {session_id, cwd, ...}
    Выход: JSON на stdout {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}

Ограничения:
    Никогда не крашит Claude — ошибки логируются, выдаётся пустой контекст.

ENV/Файлы:
    ~/.claude/rolling_memory.db — БД
    ~/.claude/logs/memory_hooks.log — логи
"""

import json
import os
import sys

# Ensure the scripts directory is on sys.path for import
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".claude" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "memory_hooks.log"

logger = logging.getLogger("memory_session_start")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    handler = RotatingFileHandler(
        str(LOG_PATH), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    )
    logger.addHandler(handler)


def _output(context: str) -> None:
    """Print the hook output JSON."""
    result = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    }
    print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    session_id = data.get("session_id", "")
    cwd = data.get("cwd", os.getcwd())

    try:
        import rolling_memory

        rolling_memory.init_db()
        rolling_memory.backup_db()
        rolling_memory.forget_expired()

        # Determine scope from cwd
        scope = cwd if cwd and cwd != "/" else "global"

        context = rolling_memory.build_context(scope=scope, token_budget=4000)

        if context:
            header = "=== Rolling Memory ==="
            full_context = f"{header}\n{context}"
        else:
            full_context = ""

        _output(full_context)
        logger.info("session_start: session=%s scope=%s context_len=%d", session_id, scope, len(full_context))

    except Exception as e:
        logger.exception("session_start hook failed: %s", e)
        _output("")


if __name__ == "__main__":
    main()
