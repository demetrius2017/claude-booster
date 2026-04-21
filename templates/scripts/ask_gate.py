#!/usr/bin/env python3
"""
Stop hook: physically block Claude from ending a turn with a forbidden
"Apply? / Proceed? / Want me to?" question after a research agent returned.

Purpose:
  pipeline.md and core.md say "don't ask Apply patch?" — soft rules get
  ignored. This hook inspects the transcript's last assistant message
  at Stop time, regex-matches against a forbidden-question vocabulary,
  and exits 2 to REFUSE the stop, forcing Claude to continue with an
  Agent or /supervise spawn.

Contract:
  stdin  — Stop event JSON {session_id, transcript_path, stop_hook_active, cwd}
  stderr — feedback when blocking
  exit   — 0 allow stop, 2 block stop (Claude continues generating)

Forbidden patterns (last assistant message's final ~600 chars only):
  - "Apply patch?", "Apply the fix?", "Apply this?"
  - "Proceed?", "Proceed with X?"
  - "Deploy?", "Deploy now?", "Deploy to prod?"
  - "Want me to X?", "Shall I X?", "Should I (apply|deploy|proceed|commit|push|run|add|remove)?"
  - Multi-option asks: A) / B) / C) or 1) / 2) / 3) + ?
  - "Which option" / "which do you want"
  - Russian: "применить?", "делать?", "запушить?"

Bypass:
  env  CLAUDE_BOOSTER_SKIP_ASK_GATE=1
  flag stop_hook_active=true (Claude Code's own re-entrancy guard — respect it)
  repo <project>/.claude/.ask_gate=off

Limitations:
  - Regex on last-600-chars; false positives possible on legitimate
    pedagogical questions. Escape hatches available.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

TAIL_SCAN_CHARS = 600

FORBIDDEN_REGEXES = [
    # Direct apply/deploy/proceed questions
    re.compile(r"\bapply\s+(patch|the\s+fix|this|it|now|it\s+now)\b[^.!]*\?", re.I),
    re.compile(r"\bproceed\b[^.!?\n]{0,80}\?", re.I),
    re.compile(r"\bdeploy\b[^.!?\n]{0,80}\?", re.I),

    # "Want me to ... ?" / "Shall I ... ?"
    re.compile(r"\bwant\s+me\s+to\b[^.!?]{0,100}\?", re.I),
    re.compile(r"\bshall\s+i\b[^.!?]{0,100}\?", re.I),

    # "Should I (apply|deploy|proceed|commit|push|run|try|add|remove|fix)...?"
    re.compile(
        r"\bshould\s+i\s+(apply|deploy|proceed|commit|push|run|try|add|remove|fix|patch|merge|restart)\b[^.!?]{0,80}\?",
        re.I,
    ),

    # "Which option" / "which do you want"
    re.compile(r"\bwhich\s+(option|one|path|approach|do\s+you\s+want|would\s+you)\b[^.!?]{0,60}\?", re.I),

    # Multi-option A/B/C patterns ending with ?
    re.compile(r"\(?[aA]\)[^\n]{0,80}\n.*\(?[bB]\)[^\n]{0,80}[^.!?]*\?", re.DOTALL),
    re.compile(r"\n\s*1\)[^\n]{0,80}\n\s*2\)[^\n]{0,80}[^.!?]*\?", re.DOTALL),

    # Russian equivalents (field-log vocabulary)
    re.compile(r"\bделать\s*\?\s*$", re.I | re.M),
    re.compile(r"\bприменить\s+(патч|исправлен)[^.!?]{0,60}\?", re.I),
    re.compile(r"\bзапушить\b[^.!?]{0,60}\?", re.I),
]


def _last_assistant_text(transcript_path: Path) -> str:
    if not transcript_path.exists():
        return ""
    try:
        lines = transcript_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            evt = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if evt.get("type") != "assistant":
            continue
        msg = evt.get("message") or {}
        content = msg.get("content") or []
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        combined = "\n".join(t for t in texts if t)
        if combined.strip():
            return combined
    return ""


def _matches_forbidden(text: str) -> tuple[bool, str]:
    tail = text[-TAIL_SCAN_CHARS:] if len(text) > TAIL_SCAN_CHARS else text
    for pat in FORBIDDEN_REGEXES:
        m = pat.search(tail)
        if m:
            return True, m.group(0)[:120]
    return False, ""


def _repo_disabled(cwd_hint: str) -> bool:
    try:
        cwd = Path(cwd_hint) if cwd_hint else Path.cwd()
    except (FileNotFoundError, OSError):
        return False
    for p in [cwd, *cwd.parents]:
        flag = p / ".claude" / ".ask_gate"
        if flag.exists():
            try:
                return flag.read_text().strip().lower() == "off"
            except OSError:
                return False
    return False


def main() -> int:
    if os.environ.get("CLAUDE_BOOSTER_SKIP_ASK_GATE") == "1":
        return 0
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0

    if data.get("stop_hook_active"):
        return 0

    if _repo_disabled(data.get("cwd") or ""):
        return 0

    transcript_path = data.get("transcript_path")
    if not transcript_path:
        return 0

    text = _last_assistant_text(Path(transcript_path))
    if not text:
        return 0

    matched, sample = _matches_forbidden(text)
    if not matched:
        return 0

    sys.stderr.write(
        f"ask_gate: your last message ended with a forbidden question pattern "
        f"({sample!r}).\n"
        f"The user pre-approved the full research → apply → verify → commit chain. "
        f"Do NOT ask 'Apply?' / 'Proceed?' / 'Which option?'.\n"
        f"Spawn an Agent (Explore/Plan/general-purpose) or /supervise to continue.\n"
        f"Bypass: CLAUDE_BOOSTER_SKIP_ASK_GATE=1 (env) | echo off > <repo>/.claude/.ask_gate"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
