#!/usr/bin/env python3
"""
PreToolUse passive counter — logs approval-requiring tool calls for the
v1.2.0 Supervisor baseline.

Purpose:
  Before Supervisor Agent v1.2.0 ships, measure the current baseline of
  approval-requiring tool calls per active hour. Without a "before" number,
  the KPI "≤3 clicks/hr @ D+30" is unverifiable. Run for 3 full working
  days (≥20h active) then `--report` to lock the baseline.

Contract:
  stdin  — PreToolUse JSON: tool_name, tool_input, cwd, session_id
  stdout — (silent in hook mode)
  stderr — (silent in hook mode)
  exit   — always 0 (NEVER blocks — this is a passive observer)

CLI:
  python approval_counter.py                 — hook mode (reads stdin)
  python approval_counter.py --report        — aggregate summary
  python approval_counter.py --report --since 3d
  python approval_counter.py --reset         — backup and truncate log

ENV/Files:
  ~/.claude/logs/approval_baseline.jsonl     — append-only event log
  CLAUDE_BOOSTER_SKIP_APPROVAL_COUNTER=1     — disable logging

Limitations:
  Counts *potential-prompt* tool calls. Whether Auto Mode auto-resolved
  the prompt is reconstructed post-hoc from require_task.jsonl and
  phase_gate.jsonl (same directory). For MVP baseline this aggregate is
  sufficient — the delta after v1.2.0 supervisor ships is what matters.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_PATH = Path.home() / ".claude" / "logs" / "approval_baseline.jsonl"

# Tools that, in Claude Code's default permissionMode, would surface a
# confirmation prompt. Read/Grep/Glob/WebSearch/Task* are excluded because
# they never block for user input.
PROMPTING_TOOLS = {
    "Bash",
    "Edit",
    "Write",
    "NotebookEdit",
    "WebFetch",
}
# MCP tools are captured via prefix (mcp__*) — most require approval on
# first use. Cheap to over-count; the baseline is about trend, not precision.
MCP_PREFIX = "mcp__"


def _log_event(payload: dict) -> None:
    tool = payload.get("tool_name", "")
    if not tool:
        return
    if tool not in PROMPTING_TOOLS and not tool.startswith(MCP_PREFIX):
        return

    tool_input = payload.get("tool_input") or {}
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "session_id": payload.get("session_id", ""),
        "cwd": payload.get("cwd", ""),
        "file": tool_input.get("file_path") or tool_input.get("path") or "",
        "bash_cmd": (tool_input.get("command") or "")[:200] if tool == "Bash" else "",
    }
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def hook_mode() -> int:
    if os.environ.get("CLAUDE_BOOSTER_SKIP_APPROVAL_COUNTER") == "1":
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    _log_event(payload)
    return 0


def _parse_since(spec: str) -> datetime:
    spec = spec.strip().lower()
    now = datetime.now(timezone.utc)
    unit = spec[-1]
    try:
        qty = int(spec[:-1])
    except ValueError as exc:
        raise ValueError(f"bad --since value: {spec!r} (expected e.g. 3d, 12h, 30m)") from exc
    if unit == "d":
        return now - timedelta(days=qty)
    if unit == "h":
        return now - timedelta(hours=qty)
    if unit == "m":
        return now - timedelta(minutes=qty)
    raise ValueError(f"unsupported --since unit: {unit!r} (use d/h/m)")


def _iter_events(since: datetime | None):
    if not LOG_PATH.exists():
        return
    with LOG_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
                ts = datetime.fromisoformat(evt["ts"])
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            if since and ts < since:
                continue
            yield evt, ts


def report(since_spec: str | None) -> int:
    since = _parse_since(since_spec) if since_spec else None
    tools: Counter[str] = Counter()
    by_hour: Counter[int] = Counter()
    active_hours: set[tuple[int, int, int, int]] = set()
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    total = 0

    for evt, ts in _iter_events(since):
        total += 1
        tools[evt.get("tool", "")] += 1
        by_hour[ts.hour] += 1
        active_hours.add((ts.year, ts.month, ts.day, ts.hour))
        first_ts = ts if first_ts is None or ts < first_ts else first_ts
        last_ts = ts if last_ts is None or ts > last_ts else last_ts

    print(f"approval_baseline — {total} events ({len(active_hours)} active hours)")
    if first_ts and last_ts:
        span_h = (last_ts - first_ts).total_seconds() / 3600.0
        print(f"span: {first_ts.isoformat()} .. {last_ts.isoformat()} ({span_h:.1f}h wall)")
    if active_hours:
        rate = total / len(active_hours)
        print(f"rate: {rate:.2f} prompts/active-hour")

    print("\nby hour-of-day (UTC):")
    for h in range(24):
        bar = "#" * min(by_hour[h], 60)
        print(f"  {h:02d}  {by_hour[h]:4d}  {bar}")

    print("\ntop-10 tools:")
    for tool, n in tools.most_common(10):
        print(f"  {n:6d}  {tool}")

    if not active_hours:
        print("\n(no events yet — let baseline run for 3 working days before locking)")
    return 0


def reset() -> int:
    if not LOG_PATH.exists():
        print(f"no log to reset at {LOG_PATH}")
        return 0
    backup = LOG_PATH.with_suffix(
        f".jsonl.bak.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    shutil.move(str(LOG_PATH), str(backup))
    print(f"backed up to {backup}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--report", action="store_true", help="print aggregate summary")
    parser.add_argument("--since", help="window for --report (e.g. 3d, 12h)")
    parser.add_argument("--reset", action="store_true", help="backup and truncate log")
    args = parser.parse_args(argv)

    if args.reset:
        return reset()
    if args.report:
        return report(args.since)
    return hook_mode()


if __name__ == "__main__":
    sys.exit(main())
