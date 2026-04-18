#!/usr/bin/env python3
"""Report state of the rules-load canary for Claude Code.

Purpose
-------
Scenario #4+#7 (reports/scenario_planning_2026-04-18.md): a Claude Code upgrade
can silently break ``~/.claude/rules/*.md`` auto-loading; or a permission
change can prevent the harness from reading rules; or ``_canary.md`` can be
deleted. This script confirms the **file state** of the canary so that /start
can cross-check whether the canary token is also visible in Claude's loaded
instructions — the two signals together verify the end-to-end rules pipeline.

Contract
--------
Input  : none (reads ~/.claude/rules/_canary.md)
Output : one-line human-readable status, plus an expected-token echo so that
         the invoker (Claude, /start) can grep its own context for the token.
Exit   : 0 if canary file exists + has a parseable token; 1 otherwise.

CLI
---
    python ~/.claude/scripts/check_rules_loaded.py
    python ~/.claude/scripts/check_rules_loaded.py --json

Limitations
-----------
- The script verifies the **file on disk**; it cannot itself introspect what
  Claude actually loaded. Cross-check against Claude's system-reminder is
  required for end-to-end validation.
- Token is expected to match ``/^RULES_CANARY_\\d{4}_\\d{2}_\\d{2}_[0-9a-f]{6}$/``.

ENV / Files
-----------
- Reads  : ~/.claude/rules/_canary.md
- Writes : nothing
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

CANARY_PATH = pathlib.Path.home() / ".claude" / "rules" / "_canary.md"
_TOKEN_RE = re.compile(r"RULES_CANARY_\d{4}_\d{2}_\d{2}_[0-9a-f]{6}")


def _inspect() -> dict:
    out: dict = {"path": str(CANARY_PATH), "exists": False, "token": None, "status": "MISSING"}
    if not CANARY_PATH.exists():
        return out
    out["exists"] = True
    try:
        body = CANARY_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        out["status"] = f"READ_ERROR:{exc}"
        return out
    match = _TOKEN_RE.search(body)
    if not match:
        out["status"] = "NO_TOKEN"
        return out
    out["token"] = match.group(0)
    out["status"] = "OK"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of human output")
    args = ap.parse_args()

    result = _inspect()

    if args.json:
        print(json.dumps(result))
    else:
        if result["status"] == "OK":
            print(f"Rules canary OK — expected token: {result['token']}")
            print("Cross-check: grep Claude's system-reminder / loaded-instructions for this token.")
            print("If token absent from Claude's context, rules/ auto-load is BROKEN despite file being present.")
        elif result["status"] == "MISSING":
            print(f"CANARY FILE MISSING: {CANARY_PATH}")
            print("Restore from ~/claude_backup_*.tar.gz .claude/rules/_canary.md")
        elif result["status"] == "NO_TOKEN":
            print(f"CANARY FILE PRESENT BUT NO VALID TOKEN: {CANARY_PATH}")
            print("Expected pattern: RULES_CANARY_YYYY_MM_DD_<6hex>")
        else:
            print(f"CANARY STATE: {result['status']}")

    return 0 if result["status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
