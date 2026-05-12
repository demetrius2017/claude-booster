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
from datetime import datetime, timezone

# Ensure the scripts directory is on sys.path for import
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

try:
    from _gate_common import project_root_from
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _gate_common import project_root_from  # type: ignore[no-redef]

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


def _reset_delegate_counter(cwd: str) -> None:
    """Reset the delegate_gate counter to 0 at session start.

    Finds the project root by walking up from *cwd*, then overwrites
    <project_root>/.claude/.delegate_counter with "0\\n".  Does nothing if
    the counter file does not exist (delegate_gate creates it on first use).
    Never raises — any error is logged at WARNING level.
    """
    try:
        root = project_root_from(cwd)
        if root is None:
            logger.debug("reset_delegate_counter: no project root found from cwd=%s — skipping", cwd)
            return
        counter_file = root / ".claude" / ".delegate_counter"
        if not counter_file.is_file():
            logger.debug("reset_delegate_counter: counter file absent at %s — skipping", counter_file)
            return
        counter_file.write_text("0\n", encoding="utf-8")
        logger.info("reset_delegate_counter: reset %s to 0", counter_file)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reset_delegate_counter failed (non-fatal): %s", exc)


def _build_balancer_summary() -> str:
    """Build a one-line MODEL BALANCER routing summary from model_balancer.json.

    Returns a two-line string (header + asterisk line) in all cases — never raises.
    """
    header = "=== MODEL BALANCER ==="
    balancer_path = Path.home() / ".claude" / "model_balancer.json"
    try:
        if not balancer_path.exists():
            return f"{header}\n  * (no decision file — run `python3 ~/.claude/scripts/model_balancer.py decide`)"

        try:
            data = json.loads(balancer_path.read_text(encoding="utf-8"))
        except Exception:
            return f"{header}\n  * (decision file corrupt — using tool-strategy.md defaults)"

        decision_date = data.get("decision_date", "?")
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        freshness = "fresh" if decision_date == today_str else "stale"

        routing = data.get("routing", {})

        def fmt(key: str) -> str:
            entry = routing.get(key)
            if not entry:
                return "?"
            provider = entry.get("provider", "?")
            model = entry.get("model", "?")
            return f"{provider}:{model}"

        lead_val = fmt("lead")
        coding_val = fmt("coding")
        hard_val = fmt("hard")
        audit_val = fmt("audit_external")

        line = (
            f"  * date={decision_date} ({freshness}) — "
            f"lead={lead_val}, coding={coding_val}, hard={hard_val}, audit={audit_val}"
        )
        return f"{header}\n{line}"
    except Exception as exc:  # noqa: BLE001
        return f"{header}\n  * (error: {type(exc).__name__})"


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

    # Reset delegate counter first — before any DB work — so each new session
    # starts the delegation budget from zero regardless of previous sessions.
    _reset_delegate_counter(cwd)

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

        balancer_summary = _build_balancer_summary()
        if full_context:
            full_context = f"{balancer_summary}\n\n{full_context}"
        else:
            full_context = balancer_summary

        _output(full_context)
        logger.info("session_start: session=%s scope=%s context_len=%d", session_id, scope, len(full_context))

    except Exception as e:
        logger.exception("session_start hook failed: %s", e)
        _output("")


if __name__ == "__main__":
    main()
