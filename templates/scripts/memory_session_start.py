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
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

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


def _build_limits_summary() -> str:
    """Build a LIMITS block showing 5h token usage, /lead quota, and weekly snapshot.

    Returns a multi-line string (header + 4 asterisk lines) in all cases — never raises.
    Internal errors degrade to a single fallback line.
    """
    header = "=== LIMITS ==="
    try:
        db_path = Path.home() / ".claude" / "rolling_memory.db"
        balancer_path = Path.home() / ".claude" / "model_balancer.json"
        now_utc = datetime.now(timezone.utc)
        window_start = now_utc - timedelta(hours=5)
        window_start_str = window_start.isoformat()

        # --- 5h window: anthropic + codex-cli ---
        anthropic_tokens = 0
        anthropic_calls = 0
        codex_tokens = 0
        codex_calls = 0
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as conn:
                cur = conn.execute(
                    """SELECT provider,
                              COUNT(*) AS calls,
                              COALESCE(SUM(COALESCE(tokens_in,0) + COALESCE(tokens_out,0)), 0) AS total_tokens
                       FROM model_metrics
                       WHERE ts_utc >= ?
                       GROUP BY provider""",
                    (window_start_str,),
                )
                for row in cur.fetchall():
                    prov, calls, tokens = row
                    if prov == "anthropic":
                        anthropic_calls = calls
                        anthropic_tokens = tokens
                    elif prov == "codex-cli":
                        codex_calls = calls
                        codex_tokens = tokens
        except Exception:
            pass  # degrade to zeros

        anthropic_k = round(anthropic_tokens / 1000)
        codex_k = round(codex_tokens / 1000)
        line_5h = (
            f"  * 5h window: anthropic {anthropic_k}k tokens / {anthropic_calls} calls"
            f" · codex-cli {codex_k}k tokens / {codex_calls} calls"
        )

        # --- /lead supervisor quota ---
        lead_state = "inactive"
        lead_session_tokens = None
        lead_pct = None
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0) as conn:
                cur = conn.execute(
                    """SELECT circuit_state, supervisor_tokens, worker_tokens, window_end
                       FROM supervisor_quota
                       ORDER BY updated_at DESC
                       LIMIT 1""",
                )
                row = cur.fetchone()
                if row is not None:
                    circuit_state, sup_tok, wrk_tok, window_end_str = row
                    # Parse window_end — it may have +00:00 suffix or Z
                    try:
                        window_end_str_norm = window_end_str.replace("Z", "+00:00")
                        window_end_dt = datetime.fromisoformat(window_end_str_norm)
                        if window_end_dt.tzinfo is None:
                            window_end_dt = window_end_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        window_end_dt = now_utc - timedelta(seconds=1)  # treat as expired

                    if window_end_dt > now_utc:
                        lead_state = circuit_state
                        total_tokens = (sup_tok or 0) + (wrk_tok or 0)
                        lead_session_tokens = total_tokens
                        lead_pct = round(total_tokens / 50000 * 100)
        except Exception:
            pass  # degrade to inactive

        if lead_session_tokens is not None:
            line_lead = (
                f"  * /lead supervisor: state={lead_state},"
                f" session_tokens={lead_session_tokens}/50000 ({lead_pct}%)"
            )
        else:
            line_lead = f"  * /lead supervisor: state={lead_state}"

        # --- weekly_max_snapshot ---
        weekly_line = "  * weekly_max_snapshot: unknown"
        try:
            if balancer_path.exists():
                balancer_data = json.loads(balancer_path.read_text(encoding="utf-8"))
                snap = balancer_data.get("inputs_snapshot", {})
                decision_date = balancer_data.get("decision_date", "")

                # Try direct key in inputs_snapshot first (current layout)
                weekly_pct_raw = snap.get("claude_max_weekly_used_pct")

                # Fallback: look inside probe_* sub-dicts
                if weekly_pct_raw is None:
                    probe_keys = sorted(
                        [k for k in snap if k.startswith("probe_")], reverse=True
                    )
                    for pk in probe_keys:
                        sub = snap.get(pk)
                        if isinstance(sub, dict) and "claude_max_weekly_used_pct" in sub:
                            weekly_pct_raw = sub["claude_max_weekly_used_pct"]
                            break

                if weekly_pct_raw is not None:
                    pct_int = round(float(weekly_pct_raw) * 100)
                    staleness = ""
                    if decision_date:
                        try:
                            dec_dt = datetime.strptime(decision_date, "%Y-%m-%d").replace(
                                tzinfo=timezone.utc
                            )
                            if (now_utc - dec_dt).days >= 2:
                                staleness = " — stale"
                        except Exception:
                            pass
                    weekly_line = (
                        f"  * weekly_max_snapshot: {pct_int}%"
                        f" (captured {decision_date}){staleness}"
                    )
        except Exception:
            pass  # degrade to unknown

        # --- codex_pro_quota (no live source yet) ---
        line_codex = "  * codex_pro_quota: (no source — wire in day-N)"

        return f"{header}\n{line_5h}\n{line_lead}\n{weekly_line}\n{line_codex}"

    except Exception as exc:  # noqa: BLE001
        return f"{header}\n  * (limits unavailable — {type(exc).__name__})"


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
        try:
            limits_summary = _build_limits_summary()
        except Exception:
            limits_summary = "=== LIMITS ===\n  * (limits unavailable — Exception)"
        combined_header = f"{balancer_summary}\n\n{limits_summary}"
        if full_context:
            full_context = f"{combined_header}\n\n{full_context}"
        else:
            full_context = combined_header

        _output(full_context)
        logger.info("session_start: session=%s scope=%s context_len=%d", session_id, scope, len(full_context))

    except Exception as e:
        logger.exception("session_start hook failed: %s", e)
        _output("")


if __name__ == "__main__":
    main()
