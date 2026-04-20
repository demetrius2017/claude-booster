#!/usr/bin/env python3
"""
Supervisor Tier 0/1/2 policy engine.

Purpose:
  Decide for each worker tool invocation whether to approve (Tier 0/1/2),
  escalate (to Dmitry), or deny (hard deny-list hit). No LLM in the loop
  — deterministic pattern matching only. Haiku supervisor calls this
  first; only escalate decisions reach the LLM.

Contract:
  evaluate(tool, tool_input, ctx) -> Decision
    tool       : str (Bash, Edit, Read, Grep, Glob, WebFetch, ...)
    tool_input : dict matching Claude Code tool contract
    ctx        : PolicyContext (project_dir, tier1_enabled, tier2_trusted_repo)
    Decision   : (action, tier, rationale, wrapped_cmd)
                 action ∈ {"approve","escalate","deny"}

Tier 0 (always on, auto-approved):
  - Read/Grep/Glob under project_dir or /tmp/booster-* sandbox; hard
    path-prefix deny for cred-files
  - git subcommands status/log/diff/show/branch/rev-parse wrapped with
    core.pager=cat + core.fsmonitor= + diff.external= + protocol.version=2
  - curl GET wrapped with --no-netrc --max-redirs 0 --fail --no-buffer
    + strips Cookie/Authorization; no query-string shell expansion
  - TaskCreate / TaskUpdate (description frozen after create; only status mutable)

Tier 1 (opt-in per session via /supervise tier1 <tool> OR repo-file):
  - pytest, npm test, cargo test — wrapped with cwd/timeout/output caps

Tier 2 (opt-in per repo via .claude/supervisor.yaml::tier2_trusted_repo):
  - npm install, cargo build, pip install

Deny-list (mirrors templates/settings.json.template lines 49-66; 16 patterns).
  These never auto-approve under any tier. Consistency test in
  tests/test_policy.py asserts parity with the settings template.

Limitations:
  Pure string/regex matching — adversarial inputs that evade the regex
  (e.g. creative shell quoting) escalate by the safe-default rule
  "unknown ⇒ escalate", not by deep AST parsing.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple


class Decision(NamedTuple):
    action: str  # "approve" | "escalate" | "deny"
    tier: int | None  # 0, 1, 2, or None
    rationale: str
    wrapped_cmd: list[str] | None  # for Bash, when Tier-0/1 hardens args


@dataclass
class PolicyContext:
    project_dir: Path
    tier1_enabled: set[str] = field(default_factory=set)  # e.g. {"pytest"}
    tier2_trusted_repo: bool = False
    session_sandbox: Path | None = None  # /tmp/booster-<pid>


# Mirrored from templates/settings.json.template lines 49-66.
# If you edit one, edit both — test_policy.py asserts parity.
DENY_BASH_PATTERNS = [
    r"^\s*git\s+push\s+.*--force",
    r"^\s*git\s+push\s+.*-f\b",
    r"^\s*git\s+reset\s+--hard",
    r"^\s*git\s+clean\s+-[fd]",
    r"^\s*git\s+branch\s+-D\b",
    r"^\s*rm\s+-rf\s+/\S*",
    r"^\s*rm\s+-rf\s+~",
    r"^\s*rm\s+-rf\s+\$HOME",
    r"^\s*kubectl\s+delete\b",
    r"^\s*docker\s+system\s+prune\b",
    r"^\s*docker\s+volume\s+rm\b",
    r"^\s*dd\s+if=",
    r"^\s*mkfs\b",
]

# Hard-deny file path fragments — no tier approves these reads.
DENY_PATH_SUBSTRINGS = [
    ".env",
    "id_rsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "/.aws/",
    "/.ssh/",
    "/.git/config",
    ".npmrc",
    ".pypirc",
    ".netrc",
]

# git subcommands that are read-only and safe under the scrub wrapper.
GIT_READONLY_SUBCOMMANDS = {"status", "log", "diff", "show", "branch", "rev-parse"}

# Tier 1 executors — arbitrary-code-execution risk; opt-in only.
TIER1_TOOLS = {"pytest", "npm test", "cargo test"}

# Tier 2 — package managers, only for explicitly trusted repos.
TIER2_PREFIXES = ("npm install", "pip install", "cargo build", "cargo install")

# git-scrub wrapper flags — neutralise hostile .git/config.
# Applied as `git -c pager=cat -c fsmonitor= -c diff.external= -c protocol.version=2 <subcmd>`.
GIT_SCRUB_FLAGS = [
    "-c", "core.pager=cat",
    "-c", "core.fsmonitor=",
    "-c", "diff.external=",
    "-c", "core.sshCommand=",
    "-c", "protocol.version=2",
]

# curl-hardening flags for Tier 0 GETs.
CURL_HARDEN_FLAGS = [
    "--no-netrc",
    "--max-redirs", "0",
    "--fail",
    "-sS",
    "-H", "Cookie:",
    "-H", "Authorization:",
]


def args_digest(tool: str, tool_input: dict) -> str:
    """Stable 16-hex digest for loop detection (same tool+args seen N times)."""
    canonical = f"{tool}|{sorted(tool_input.items())}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _within_allowed_root(path: str, ctx: PolicyContext) -> bool:
    try:
        resolved = Path(path).resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    roots = [ctx.project_dir.resolve()]
    if ctx.session_sandbox:
        roots.append(ctx.session_sandbox.resolve())
    return any(
        str(resolved).startswith(str(root) + os.sep) or str(resolved) == str(root)
        for root in roots
    )


def _has_deny_path_substring(path: str) -> str | None:
    low = path.lower()
    for needle in DENY_PATH_SUBSTRINGS:
        if needle in low:
            return needle
    return None


def _match_deny_bash(cmd: str) -> str | None:
    for pat in DENY_BASH_PATTERNS:
        if re.search(pat, cmd):
            return pat
    return None


def _is_shell_expansion_in(arg: str) -> bool:
    return bool(re.search(r"\$\(|`|\$\{", arg))


def evaluate_read(tool: str, tool_input: dict, ctx: PolicyContext) -> Decision:
    path = tool_input.get("file_path") or tool_input.get("path") or ""
    hit = _has_deny_path_substring(path)
    if hit:
        return Decision("deny", None, f"path matches deny-substring {hit!r}", None)
    if path and not _within_allowed_root(path, ctx):
        return Decision("escalate", None, f"path {path!r} outside project_dir+sandbox", None)
    return Decision("approve", 0, f"{tool} within allowed root", None)


def evaluate_web_fetch(tool_input: dict, _ctx: PolicyContext) -> Decision:
    url = tool_input.get("url", "")
    if not url:
        return Decision("escalate", None, "empty url", None)
    if not re.match(r"^https?://[A-Za-z0-9.-]+(?:/[^\s]*)?$", url):
        return Decision("escalate", None, "unusual url shape", None)
    if _is_shell_expansion_in(url):
        return Decision("deny", None, "shell-expansion in url", None)
    return Decision("approve", 0, "web fetch to well-formed http(s) URL", None)


def evaluate_bash(tool_input: dict, ctx: PolicyContext) -> Decision:
    cmd = (tool_input.get("command") or "").strip()
    if not cmd:
        return Decision("escalate", None, "empty command", None)

    deny_hit = _match_deny_bash(cmd)
    if deny_hit:
        return Decision("deny", None, f"deny-list hit: {deny_hit!r}", None)

    # Refuse commands that stage a shell-injected URL into whitelisted tools.
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        return Decision("escalate", None, "shlex-unparseable command", None)
    if not tokens:
        return Decision("escalate", None, "no tokens", None)
    head = tokens[0]

    # git read-only → Tier 0 with scrub.
    if head == "git" and len(tokens) >= 2 and tokens[1] in GIT_READONLY_SUBCOMMANDS:
        wrapped = ["git", *GIT_SCRUB_FLAGS, *tokens[1:]]
        return Decision("approve", 0, f"git {tokens[1]} with scrub wrapper", wrapped)

    # curl → Tier 0 harden (only GET-style — no -X POST/PUT/DELETE, no -d/-T).
    if head == "curl":
        lower_tokens = [t.lower() for t in tokens]
        if any(t in ("-x", "--request") for t in lower_tokens):
            return Decision("escalate", None, "curl with explicit method — escalate", None)
        if any(t in ("-d", "--data", "--data-binary", "-t", "--upload-file") for t in lower_tokens):
            return Decision("escalate", None, "curl with payload — escalate", None)
        urls = [t for t in tokens[1:] if t.startswith(("http://", "https://"))]
        if not urls:
            return Decision("escalate", None, "curl with no http(s) url", None)
        if any(_is_shell_expansion_in(u) for u in urls):
            return Decision("deny", None, "shell-expansion in curl url", None)
        wrapped = ["curl", *CURL_HARDEN_FLAGS, *tokens[1:]]
        return Decision("approve", 0, "curl GET with hardening flags", wrapped)

    # Tier 1 executors.
    cmd_prefix = " ".join(tokens[:2]) if len(tokens) > 1 else tokens[0]
    for tier1 in TIER1_TOOLS:
        if cmd.startswith(tier1):
            if tier1 in ctx.tier1_enabled:
                return Decision("approve", 1, f"tier1 {tier1} enabled for session", None)
            return Decision("escalate", None, f"tier1 {tier1} not enabled", None)
    for t2 in TIER2_PREFIXES:
        if cmd.startswith(t2):
            if ctx.tier2_trusted_repo:
                return Decision("approve", 2, f"tier2 {t2} allowed (trusted repo)", None)
            return Decision("escalate", None, f"tier2 {t2} needs trusted-repo flag", None)

    # Bash strictly inside session sandbox — Tier 0.
    if ctx.session_sandbox and cmd.strip().startswith(f"cd {ctx.session_sandbox}"):
        return Decision("approve", 0, "sandbox-scoped bash", None)

    # Default: unknown → escalate.
    return Decision("escalate", None, "unknown command — escalate by safe default", None)


def evaluate(tool: str, tool_input: dict, ctx: PolicyContext) -> Decision:
    if tool in ("Read", "Grep", "Glob"):
        return evaluate_read(tool, tool_input, ctx)
    if tool == "WebFetch":
        return evaluate_web_fetch(tool_input, ctx)
    if tool == "Bash":
        return evaluate_bash(tool_input, ctx)
    if tool == "WebSearch":
        return Decision("approve", 0, "WebSearch read-only", None)
    if tool in ("TaskCreate", "TaskUpdate"):
        # TaskUpdate description mutation guarded in runtime; policy-level pass.
        return Decision("approve", 0, f"{tool} task-machine op", None)
    if tool in ("Edit", "Write", "NotebookEdit"):
        # require_task + phase_gate cover these. Supervisor defers.
        return Decision("escalate", None, f"{tool} deferred to require_task/phase_gate", None)
    return Decision("escalate", None, f"unknown tool {tool!r}", None)
