#!/usr/bin/env python3
"""PreToolUse hook — enforce structured self-verification on handover commits.

Purpose:
    Consilium 2026-04-18 §Q2 verdict (HOOK + STRUCTURED JSON + ALLOWLIST N/A
    + FAKE-EVIDENCE REJECTION). Scenario §5 warns that forcing functions
    decay into theater without a bite. Hook-enforced gate is that bite:
    blocks handover commits when the agent has not posted a
    ``{"verified": {...}}`` block in recent assistant messages, OR when the
    block contains only fake evidence (localhost, ``|| true``, curl without
    status code, SQL without rowcount), OR when it claims N/A but the diff
    touches non-allowlisted paths.

Contract:
    Fires from Claude Code as a PreToolUse hook (stdin JSON schema:
    {session_id, transcript_path, cwd, hook_event_name, tool_name,
    tool_input, …}).

    Activation triggers (v1 — narrow, easy to promote later):
        * Bash tool call whose ``command`` touches ``reports/handover_*.md``
          via ``git add`` / ``git commit`` (the canonical /handover path).

    TaskUpdate(status=completed) gating is deliberately deferred to v2 —
    every task completion firing the gate would be too noisy while the
    JSON-block muscle is still being built. Surface the v1 deferral via
    commands.md so it's explicit.

    Per-project flag precedence (first hit wins):
        1. Env ``VERIFY_GATE_MODE`` ∈ {enforcing, warn, off} — highest priority
           so tests and one-off overrides never need file edits.
        2. ``.claude/CLAUDE.md`` YAML frontmatter key ``verify_gate``.
        3. Auto-detect: ``warn`` if ``deploy/`` or ``.github/workflows/``
           exists at the project root; else ``off``.

    Decision output:
        * allow  → exit 0, no output.
        * block  → exit 2 + JSON envelope with
                   ``hookSpecificOutput.permissionDecision="deny"``.
                   Only emitted when mode=enforcing.
        * warn   → exit 0 + stderr message (non-blocking).

CLI / Examples:
    # Normal hook invocation (Claude Code pipes JSON to stdin):
    echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git commit -m \\"docs: handover\\" -- reports/handover_2026-04-18.md"},"cwd":"/Users/me/Projects/Claude_Booster","transcript_path":"/tmp/x.jsonl"}' | python3 verify_gate.py

    # Force-enable while testing locally:
    VERIFY_GATE_MODE=warn python3 verify_gate.py < hook_payload.json

Limitations (v1):
    - Only gates Bash(``git add`` | ``git commit``) on handover files. Does
      not fire on other /handover-step Bash calls (e.g., ``git push``) — the
      commit is the canonical capture moment.
    - Hook version marker (``CLAUDE_CODE_HOOK_SCHEMA_VERSION`` / ``hook_event_name``)
      missing → logged loudly + hook fails open (exit 0, stderr warning).
      The telemetry script + ``check_rules_loaded.py`` at /start are the
      second-line detectors.
    - Artifact-adjacency scan is line-based on the last 200 assistant text
      blocks. A claim that references evidence >200 blocks back will be
      rejected; that is the intended friction.
    - **Evidence-to-tool-output correlation is NOT enforced in v1.** The
      gate validates that evidence entries *look* real (markers, HTTP
      status, rowcount, no fake-pattern bypasses), but does not cross-
      reference them against actual ``tool_use`` / ``tool_result`` blocks
      in the transcript. An adversarial agent could fabricate realistic-
      looking evidence text without running the command. Deferred to v2
      with a dedicated design — see handover 2026-04-18 follow-ups.

ENV/Files:
    - Reads  : ~/.claude/CLAUDE.md (optional env override); <cwd>/.claude/
               CLAUDE.md (per-project flag); the session transcript JSONL;
               optionally ``git diff --name-only --cached`` for N/A path
               allowlisting.
    - Writes : ~/.claude/logs/verify_gate_decisions.jsonl
               (append-only, one record per invocation); ~/.claude/logs/
               verify_gate.log (WARN/ERROR only, RotatingFileHandler).
    - ENV    : VERIFY_GATE_MODE (enforcing|warn|off) — test override.
               CLAUDE_CODE_HOOK_SCHEMA_VERSION — sanity marker.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# --------------------------------------------------------------------------
# Paths + logging
# --------------------------------------------------------------------------

LOG_DIR = Path.home() / ".claude" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "verify_gate.log"
DECISIONS_LOG = LOG_DIR / "verify_gate_decisions.jsonl"

logger = logging.getLogger("verify_gate")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = RotatingFileHandler(
        str(LOG_FILE), maxBytes=500_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)

HANDOVER_FILE_RE = re.compile(r"reports/handover_[0-9-]{10}(?:_\d+)?\.md")
GIT_COMMIT_RE = re.compile(r"\bgit\s+(?:commit|add)\b")

# --------------------------------------------------------------------------
# Allowlists / fake-evidence patterns
# --------------------------------------------------------------------------

# Prefixes / globs that count as "docs-only" for the N/A escape hatch.
# Anything whose diff touches paths OUTSIDE these ⇒ evidence required.
NA_ALLOWLIST_PREFIXES = (
    "docs/", "doc/", "reports/", "audits/", ".claude/", "tests/", "test/",
)
NA_ALLOWLIST_SUFFIXES = (
    ".md", ".txt", ".rst",
)
NA_ALLOWLIST_BASENAMES = {
    "README", "README.md", "README.txt", "LICENSE", "CHANGELOG", "CHANGELOG.md",
    "AUTHORS", ".gitignore",
}

# Evidence markers — at least one must appear in each evidence entry.
EVIDENCE_MARKERS = re.compile(
    r"(?i)(?:\bcurl\b|\bwget\b|\bpsql\b|\bsqlite3\b|\bSELECT\s|"
    r"\bPRAGMA\b|\bHTTP/\d|\bdocker\b|\bkubectl\b|\bDevTools\b|"
    r"\blist_network_requests\b|\bpytest\b|\bexit\s*=\s*\d)"
)

# Fake-evidence patterns — reject even if the shape looks right.
# Note on the curl -s rule: `--` is not a word-boundary character in Python
# regex (`-` is non-word), so `\b--fail\b` never matches. Use explicit space
# or line-boundary anchors. The negative lookahead lists the flags that
# legitimately prove exit code / body were inspected: `--fail`, `-o <file>`,
# `| tee`, `-w <fmt>`, `-S` (show errors). Order inside the group does not
# matter because alternation is unanchored.
#
# Loopback equivalents: `localhost`, `127.0.0.1`, `0.0.0.0`, `::1` / `[::1]`
# all resolve to the local machine and therefore cannot prove a staging or
# production deploy works. Any of them in an evidence entry is a bypass.
FAKE_EVIDENCE_PATTERNS = [
    (re.compile(r"\|\|\s*true\b"), "output swallowed with `|| true` — actual failure is hidden"),
    (re.compile(r"(?i)localhost(?::\d+)?(?:/|\b)"), "localhost target — should be a real staging/prod URL"),
    (re.compile(r"(?i)127\.0\.0\.1(?::\d+)?\b"), "127.0.0.1 target — should be a real staging/prod URL"),
    (re.compile(r"(?i)\b0\.0\.0\.0(?::\d+)?\b"), "0.0.0.0 target — should be a real staging/prod URL"),
    (re.compile(r"(?:\[::1\]|::1)(?::\d+)?"), "::1 (IPv6 loopback) target — should be a real staging/prod URL"),
    (
        re.compile(
            r"(?i)\bcurl\s+-s\b"
            r"(?![^\n]*(?:"
            r"--fail"       # --fail anywhere on the line after curl -s
            r"|\s-o\s+\S"   # -o <file> redirect
            r"|\s-w\s+"     # -w <format> (usually %{http_code})
            r"|\s-S\b"      # -S (show errors even with -s)
            r"|\|\s*tee\b"  # piped to tee
            r"|>\s*\S"      # stdout redirect to file
            r"))"
        ),
        "`curl -s` without `--fail` / `-o <file>` / `| tee` / `-w` / `-S` / stdout redirect "
        "— exit code and body are both suppressed",
    ),
]

# HTTP status sentinel (curl must show a status code within ±20 lines).
HTTP_STATUS_RE = re.compile(r"\b(?:HTTP/\d(?:\.\d)?\s+)?([1-5]\d{2})\b")
SQL_ROWCOUNT_RE = re.compile(r"(?i)\b(?:rows?|rowcount|affected|changes|\d+\s*rows?)\b")

# --------------------------------------------------------------------------
# Input / stdin
# --------------------------------------------------------------------------

def _read_payload() -> dict:
    """Parse hook stdin JSON. Return empty dict on malformed input."""
    try:
        raw = sys.stdin.read()
    except Exception:
        logger.exception("read stdin failed")
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("stdin was not valid JSON: %r", raw[:200])
        return {}


# --------------------------------------------------------------------------
# Trigger detection
# --------------------------------------------------------------------------

def _should_gate(payload: dict) -> tuple[bool, str]:
    """Return (fire, reason). ``fire=False`` → hook is a no-op allow.

    v1 fires only on Bash commands where ``command`` both mentions
    ``git add``/``git commit`` AND references a ``reports/handover_*.md``
    filename. The second gate (file reference) keeps the hook silent on
    all other commits and avoids surprising the agent mid-refactor.
    """
    if payload.get("hook_event_name") != "PreToolUse":
        return False, "non-PreToolUse event"
    if payload.get("tool_name") != "Bash":
        return False, f"tool {payload.get('tool_name')!r} not gated"
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not GIT_COMMIT_RE.search(cmd):
        return False, "bash command is not git add/commit"
    if not HANDOVER_FILE_RE.search(cmd):
        return False, "bash command does not reference a handover file"
    return True, "Bash git commit/add on handover file"


# --------------------------------------------------------------------------
# Project flag resolution
# --------------------------------------------------------------------------

_FRONTMATTER_VERIFY_GATE_RE = re.compile(
    r"(?m)^verify_gate\s*:\s*(enforcing|warn|off)\s*$",
)


def _resolve_mode(cwd: str) -> tuple[str, str]:
    """Return (mode, source). Mode ∈ {enforcing,warn,off}.

    Precedence: env var > project CLAUDE.md frontmatter > auto-detect > off.
    """
    env_mode = os.environ.get("VERIFY_GATE_MODE", "").strip().lower()
    if env_mode in ("enforcing", "warn", "off"):
        return env_mode, "env:VERIFY_GATE_MODE"

    project_root = Path(cwd) if cwd else Path.cwd()
    claude_md = project_root / ".claude" / "CLAUDE.md"
    if claude_md.is_file():
        try:
            text = claude_md.read_text(encoding="utf-8")
        except OSError:
            text = ""
        # Parse only the first YAML frontmatter block if present.
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                frontmatter = text[3:end]
                m = _FRONTMATTER_VERIFY_GATE_RE.search(frontmatter)
                if m:
                    return m.group(1), f"{claude_md}:frontmatter"

    # Auto-detect: deploy/ or CI workflows ⇒ warn by default.
    if (project_root / "deploy").is_dir() or (project_root / ".github" / "workflows").is_dir():
        return "warn", "auto:detected-deploy-or-ci"
    return "off", "auto:default"


# --------------------------------------------------------------------------
# Transcript parsing
# --------------------------------------------------------------------------

def _tail_jsonl(path: str, n: int = 200) -> list[str]:
    """Return last ``n`` lines of ``path``. Empty list on error."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 256 * 1024)  # 256KB tail is plenty for 200 lines.
            f.seek(size - chunk)
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[-n:]


def _extract_assistant_text(lines: list[str]) -> list[str]:
    """Return assistant-visible text blocks concatenated, newest first."""
    out: list[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw or raw[0] != "{":
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        for block in msg.get("content", []) or []:
            if block.get("type") == "text":
                t = block.get("text") or ""
                if t:
                    out.append(t)
    return out


# --------------------------------------------------------------------------
# {"verified": {...}} JSON block extraction
# --------------------------------------------------------------------------

_VERIFIED_KEY_RE = re.compile(r"\"verified\"\s*:\s*\{")


def _extract_verified_block(text_blocks: list[str]) -> dict | None:
    """Scan the most recent assistant text blocks for a {"verified": {...}}.

    The block is found by:
      1. Locating the substring ``"verified": {`` (with optional whitespace).
      2. Balancing braces from that point until depth returns to 0.
      3. Wrapping the matched segment in outer ``{ }`` to parse as JSON.

    Iterates blocks newest-first and, within a block, matches are scanned
    last-first. A stale ``pass`` block from earlier in the session must NOT
    satisfy the gate for a fresh handover commit whose diff has since
    changed — the newest block is authoritative.

    Returns the parsed dict's ``verified`` value, or ``None`` if no valid
    block is found in the inspected window.
    """
    for text in reversed(text_blocks):
        matches = list(_VERIFIED_KEY_RE.finditer(text))
        for m in reversed(matches):
            # Try to parse the enclosing object by scanning back to the
            # nearest `{` and forward balancing braces.
            # Easier: rebuild a minimal wrapper.
            start = text.rfind("{", 0, m.start())
            if start < 0:
                continue
            depth = 0
            end = -1
            in_str = False
            esc = False
            for i in range(start, len(text)):
                ch = text[i]
                if esc:
                    esc = False
                    continue
                if ch == "\\" and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end < 0:
                continue
            snippet = text[start:end]
            try:
                obj = json.loads(snippet)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("verified"), dict):
                return obj["verified"]
    return None


# --------------------------------------------------------------------------
# Evidence validation
# --------------------------------------------------------------------------

def _fake_evidence_reasons(entry: str) -> list[str]:
    reasons = []
    for pat, reason in FAKE_EVIDENCE_PATTERNS:
        if pat.search(entry):
            reasons.append(reason)
    return reasons


def _evidence_is_strong(entry: str) -> tuple[bool, str]:
    """Return (ok, reason_if_fail). A single evidence entry must:
        * match an evidence marker (curl, psql, sqlite3, HTTP/N, …);
        * not contain any fake-evidence pattern;
        * include a status/rowcount signal appropriate to the marker.
    """
    if not isinstance(entry, str) or not entry.strip():
        return False, "empty evidence entry"
    if not EVIDENCE_MARKERS.search(entry):
        return False, f"no recognised evidence marker in {entry[:80]!r}"
    fake = _fake_evidence_reasons(entry)
    if fake:
        return False, "; ".join(fake) + f" :: {entry[:80]!r}"
    # Marker-specific tail checks.
    lower = entry.lower()
    if "curl" in lower or "wget" in lower or "http/" in lower:
        if not HTTP_STATUS_RE.search(entry):
            return False, f"HTTP call without 1xx-5xx status in {entry[:80]!r}"
    if "select " in lower or "sqlite3" in lower or "psql" in lower:
        if not SQL_ROWCOUNT_RE.search(entry):
            return False, f"SQL/DB evidence without rowcount or 'rows' marker in {entry[:80]!r}"
    return True, ""


def _validate_pass(block: dict) -> tuple[bool, list[str]]:
    """status=pass: ALL evidence entries must validate. Returns (ok, reasons).

    Contract: every entry must be a real-looking artifact. Mixing one strong
    entry with fake padding is rejected because padded blocks normalise fake
    evidence ("fake-but-accompanied") as acceptable, which defeats the gate.
    """
    evidence = block.get("evidence") or []
    if not isinstance(evidence, list) or not evidence:
        return False, ["status='pass' requires non-empty 'evidence' list"]
    reasons: list[str] = []
    for entry in evidence:
        ok, why = _evidence_is_strong(entry)
        if not ok:
            reasons.append(why)
    if reasons:
        return False, reasons
    return True, []


def _git_staged_files(cwd: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "diff", "--name-only", "--cached"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if out.returncode != 0:
            return []
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _path_in_allowlist(path: str) -> bool:
    base = os.path.basename(path)
    if base in NA_ALLOWLIST_BASENAMES:
        return True
    if any(path.startswith(p) for p in NA_ALLOWLIST_PREFIXES):
        return True
    if any(path.endswith(s) for s in NA_ALLOWLIST_SUFFIXES):
        return True
    return False


def _validate_na(cwd: str, block: dict) -> tuple[bool, list[str]]:
    """status=na: all staged paths must be in the allowlist AND
    ``reason_na`` must be non-empty.
    """
    reason_na = (block.get("reason_na") or "").strip()
    if not reason_na:
        return False, ["status='na' requires non-empty 'reason_na'"]
    staged = _git_staged_files(cwd)
    if not staged:
        # No staged files (hook fired on commit-in-progress that has
        # already staged nothing) — allow, nothing to verify.
        return True, []
    offenders = [p for p in staged if not _path_in_allowlist(p)]
    if offenders:
        return False, [
            "status='na' but diff touches non-allowlisted paths: "
            + ", ".join(offenders[:5])
            + (" …" if len(offenders) > 5 else "")
        ]
    return True, []


# --------------------------------------------------------------------------
# Decision + output
# --------------------------------------------------------------------------

def _log_decision(record: dict) -> None:
    try:
        with open(DECISIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        logger.exception("failed to append to %s", DECISIONS_LOG)


def _emit_deny(reason: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(payload, ensure_ascii=False))


def _emit_warn(reason: str) -> None:
    print(f"[verify_gate] WARN: {reason}", file=sys.stderr)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    payload = _read_payload()
    if not payload:
        # No payload / malformed — fail open, but surface a stderr warning
        # so the agent + user + check_hook_health all notice. Silent fail-
        # open is the exact decay mode scenario §4.1 warns against.
        _emit_warn("SYSTEM WARNING: empty or malformed stdin — verify_gate cannot enforce, failing open.")
        logger.error("empty or malformed payload — hook fails open")
        return 0

    schema_marker = payload.get("hook_event_name") or os.environ.get("CLAUDE_CODE_HOOK_SCHEMA_VERSION")
    if not schema_marker:
        _emit_warn("SYSTEM WARNING: hook_event_name missing — Claude Code schema drift? This hook may not be enforcing.")
        logger.error("hook_event_name missing from payload — possible schema drift")
        return 0

    fire, why = _should_gate(payload)
    cwd = payload.get("cwd") or os.getcwd()
    mode, source = _resolve_mode(cwd)

    decision = {
        "ts": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": payload.get("session_id"),
        "cwd": cwd,
        "tool_name": payload.get("tool_name"),
        "mode": mode,
        "mode_source": source,
        "fired": bool(fire),
        "reason": why,
    }

    if not fire:
        decision["result"] = "allow"
        _log_decision(decision)
        return 0

    if mode == "off":
        decision["result"] = "allow (mode=off)"
        _log_decision(decision)
        return 0

    # From here we're gating. Parse the transcript + look for the block.
    # Retry up to 3 times with short backoff — Claude Code may have buffered
    # the latest assistant message at the moment the hook fires, so a single
    # tail read can miss a block that is about to land on disk. Total worst-
    # case delay: ~350 ms, which is acceptable for a commit-time gate.
    transcript_path = payload.get("transcript_path") or ""
    block = None
    for attempt, delay in enumerate((0.0, 0.05, 0.15, 0.3)):
        if delay:
            time.sleep(delay)
        lines = _tail_jsonl(transcript_path, n=200) if transcript_path else []
        text_blocks = _extract_assistant_text(lines)
        block = _extract_verified_block(text_blocks)
        if block is not None:
            decision["tail_attempts"] = attempt + 1
            break
    else:
        decision["tail_attempts"] = 4

    if block is None:
        msg = (
            "missing {\"verified\": {...}} JSON block in the last 200 "
            "transcript lines. Emit it BEFORE running `git commit` per "
            "~/.claude/rules/commands.md §handover."
        )
        decision.update({"result": "block" if mode == "enforcing" else "warn", "why": msg})
        _log_decision(decision)
        if mode == "enforcing":
            _emit_deny(msg)
            return 2
        _emit_warn(msg)
        return 0

    status = (block.get("status") or "").strip().lower()
    if status == "pass":
        ok, reasons = _validate_pass(block)
    elif status in ("na", "n/a"):
        ok, reasons = _validate_na(cwd, block)
    else:
        ok, reasons = False, [f"unknown status={status!r}, expected 'pass' or 'na'"]

    if ok:
        decision.update({"result": "allow (verified)", "block_status": status})
        _log_decision(decision)
        return 0

    msg = (
        f"verify_gate rejected {status!r} block: "
        + " | ".join(reasons[:5])
        + (" …" if len(reasons) > 5 else "")
    )
    decision.update({"result": "block" if mode == "enforcing" else "warn",
                     "block_status": status, "why": msg})
    _log_decision(decision)
    if mode == "enforcing":
        _emit_deny(msg)
        return 2
    _emit_warn(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
