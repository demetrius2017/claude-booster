#!/usr/bin/env python3
"""
PreToolUse hook: enforce [model] tag in Agent tool call descriptions.

Purpose:
    Claude Code's UI renders the Agent tool's `description` field but does NOT
    display the `model` parameter separately. Without a visible [model] tag in
    the description, there is no way to know at a glance which model tier a
    spawned sub-agent runs on.  This hook checks every Agent tool call made by
    the Lead (top-level Claude session) and blocks any call whose `description`
    field lacks a recognised [model] tag like [sonnet], [opus], or [haiku].

    On block, the hook emits a helpful stderr message telling Claude exactly
    what tag to add and where, so the retry succeeds on the first attempt.

Contract:
    stdin  — PreToolUse JSON from Claude Code harness:
               {tool_name, tool_input.{description, model, prompt, …},
                cwd, session_id, agent_id, agent_type, …}
    stdout — (silent on block)
             OR JSON {hookSpecificOutput: {hookEventName, permissionDecision,
             updatedInput}} when AUTO_INJECT mode is enabled via env var (see
             ENV section below)
    stderr — human-readable block reason on exit 2 only
    exit   — 0 allow, 2 block

    Sub-agent auto-skip:
        When `agent_id` is a non-empty string in the stdin JSON, this hook is
        firing inside a sub-agent context (the sub-agent itself is spawning a
        tool). The check is meaningless here — delegation has already happened.
        We exit 0 immediately.

    Fail-open policy:
        Any parsing error, unexpected JSON structure, or unexpected exception
        exits 0 so the hook NEVER breaks normal Claude Code operation.

CLI / Examples:
    # Allowed — tag present:
    echo '{"tool_name":"Agent","tool_input":{"description":"[sonnet] Explore files"},
           "session_id":"s1"}' | python3 model_tag_enforcer.py; echo "exit: $?"

    # Blocked — tag absent:
    echo '{"tool_name":"Agent","tool_input":{"description":"Explore files"},
           "session_id":"s1"}' | python3 model_tag_enforcer.py; echo "exit: $?"

    # Auto-skip — sub-agent context:
    echo '{"tool_name":"Agent","tool_input":{"description":"Explore files"},
           "agent_id":"sub-42","session_id":"s1"}' | python3 model_tag_enforcer.py

    # Non-Agent tool — passthrough:
    echo '{"tool_name":"Bash","tool_input":{"command":"ls"},"session_id":"s1"}' \
         | python3 model_tag_enforcer.py

    # Auto-inject mode (env):
    CLAUDE_MODEL_TAG_AUTO_INJECT=1  — derive tag from `model` param and inject
                                      into description silently (exit 0 + JSON
                                      stdout).  Falls back to [inherit] when
                                      model param is absent.

Limitations:
    - Tag matching is case-insensitive for the keyword part; bracket syntax
      [sonnet], [SONNET] etc. all recognised.  Custom sub-tier suffixes like
      [sonnet-3-7] are also accepted.
    - Mismatch between description tag and `model` param is logged to stderr as
      a WARNING but does NOT block — it's advisory only.  Enforcing strict
      parity would break callers that legitimately use [inherit] as a tag while
      specifying a concrete model param for the harness.
    - This hook only fires when registered in settings.json under PreToolUse
      with matcher "Agent".  If the matcher is omitted, the hook fires for ALL
      tools (harmless but wastes cycles on non-Agent calls).
    - Auto-inject mode requires Claude Code >= v2.0.10 which introduced the
      `updatedInput` stdout field.

ENV / Files:
    - Reads  : stdin (PreToolUse JSON)
    - Writes : nothing (no side effects, no log files)
    - ENV    : CLAUDE_BOOSTER_SKIP_MODEL_TAG_ENFORCER=1
                   -- bypass this hook entirely (useful for testing / one-off
                      sessions where tagging is intentionally relaxed)
               CLAUDE_MODEL_TAG_AUTO_INJECT=1
                   -- instead of blocking, silently inject a [model] tag derived
                      from the `model` param (or [inherit] when absent) into the
                      description via updatedInput stdout JSON (requires CC
                      >= v2.0.10)
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional

try:
    from _gate_common import is_subagent_context
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from _gate_common import is_subagent_context
    except ImportError:
        def is_subagent_context(data):  # type: ignore[misc]
            aid = (data or {}).get("agent_id")
            return bool(aid and isinstance(aid, str))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tags recognised as valid model indicators (case-insensitive, bracket syntax)
# Pattern: [<tier>] or [<tier>-<suffix>] e.g. [sonnet], [opus-3], [haiku-4-5]
_MODEL_TAG_RE = re.compile(
    r"\["
    r"(?:sonnet|opus|haiku|inherit|claude)"
    r"(?:[-\w.]*)"   # optional sub-tier / version suffix
    r"\]",
    re.IGNORECASE,
)

# Human-readable tier examples for the block message
_TIER_EXAMPLES = "[sonnet], [opus], [haiku]"

# Environment variables
_SKIP_ENV = "CLAUDE_BOOSTER_SKIP_MODEL_TAG_ENFORCER"
_AUTO_INJECT_ENV = "CLAUDE_MODEL_TAG_AUTO_INJECT"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_model_tag(description: str):
    """Return the regex match object if description contains a recognised [model] tag, else None."""
    return _MODEL_TAG_RE.search(description)


def _infer_tag_from_model_param(model_param: Optional[str]) -> str:
    """
    Derive a human-readable [tag] from the model param string.

    Claude Code accepts short aliases: "sonnet", "opus", "haiku" as well as
    full model IDs like "claude-sonnet-4-6".  We extract the first recognised
    tier keyword.  Falls back to "[inherit]" when model param is absent or
    unrecognised (the agent inherits the Lead's model).
    """
    if not model_param:
        return "[inherit]"

    lower = model_param.lower()
    for tier in ("opus", "sonnet", "haiku"):
        if tier in lower:
            return f"[{tier}]"

    # Unknown explicit model string -- use it verbatim with brackets
    return f"[{model_param}]"


def _check_mismatch(tag_match, model_param: Optional[str]) -> Optional[str]:
    """
    Return a warning string if description tag and model param appear to
    disagree, or None if they are consistent / cannot be determined.

    Parameters
    ----------
    tag_match : re.Match
        Match object returned by _find_model_tag (caller has already confirmed it is non-None).
    model_param : str or None
        The `model` parameter from tool_input.
    """
    if not model_param:
        return None  # inherit is always compatible

    tag_text = tag_match.group(0).lower()
    lower_model = model_param.lower()

    for tier in ("opus", "sonnet", "haiku"):
        tag_has_tier = tier in tag_text
        param_has_tier = tier in lower_model
        if tag_has_tier and not param_has_tier:
            return (
                f"WARNING: description says {tag_match.group(0)!r} but "
                f"model param is {model_param!r} -- consider aligning them."
            )
        if param_has_tier and not tag_has_tier:
            return (
                f"WARNING: model param is {model_param!r} but description tag "
                f"is {tag_match.group(0)!r} -- consider aligning them."
            )

    return None


def _build_block_message(
    description: str, model_param: Optional[str]
) -> str:
    """Build a helpful stderr block message."""
    suggested_tag = _infer_tag_from_model_param(model_param)
    preview = description[:80] + ("..." if len(description) > 80 else "")
    lines = [
        "model_tag_enforcer: Agent description is missing a [model] tag.",
        "",
        f"  Current description: {preview!r}",
        f"  Suggested fix:       {suggested_tag} {description}",
        "",
        f"Recognised tags: {_TIER_EXAMPLES} (and [inherit] for model-inherit).",
        "Add the tag at the START of the description so it is visible in the UI.",
    ]
    if model_param:
        lines.append(f"  (model param is {model_param!r} -- use {suggested_tag})")
    return "\n".join(lines)


def _build_auto_inject_stdout(
    description: str, model_param: Optional[str]
) -> str:
    """
    Build stdout JSON using updatedInput to inject the tag automatically.
    Available since Claude Code v2.0.10.
    """
    tag = _infer_tag_from_model_param(model_param)
    new_description = f"{tag} {description}"
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": (
                f"model_tag_enforcer: auto-injected {tag} into description"
            ),
            "updatedInput": {"description": new_description},
        }
    }
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """
    Entry point.

    Returns:
        0 -- allow (tool proceeds)
        2 -- block (stderr is fed back to Claude as error message)
    """
    # --- bypass env (check before reading stdin for speed) ---
    if os.environ.get(_SKIP_ENV) == "1":
        return 0

    # --- fail-open guard: parse stdin ---
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError, OSError):
        return 0  # malformed / empty -- fail open

    # --- only care about Agent tool calls ---
    tool_name = payload.get("tool_name", "")
    if tool_name != "Agent":
        return 0

    # --- sub-agent auto-skip: delegation already happened ---
    if is_subagent_context(payload):
        return 0

    # --- extract relevant fields from tool_input ---
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0  # unexpected type -- fail open

    description: str = tool_input.get("description") or ""
    model_param: Optional[str] = tool_input.get("model") or None

    # --- check for tag ---
    tag_match = _find_model_tag(description)
    if tag_match:
        # Tag present -- check for mismatch and warn, but allow
        warning = _check_mismatch(tag_match, model_param)
        if warning:
            print(warning, file=sys.stderr)
        return 0

    # --- tag absent ---

    # Auto-inject mode: silently fix the description via updatedInput
    if os.environ.get(_AUTO_INJECT_ENV) == "1":
        stdout_json = _build_auto_inject_stdout(description, model_param)
        print(stdout_json)
        return 0

    # Default mode: block with helpful message
    msg = _build_block_message(description, model_param)
    print(msg, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- last-resort fail-open
        sys.exit(0)
