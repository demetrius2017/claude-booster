"""
Trigger detectors for the affect register: user correction, unverified claims, tool errors.
Purpose: Evaluate conversation turns and return (channel, trigger_label) pairs for compounding.
Contract: All detectors operate on plain text via regex — no NLP dependencies.
"""
from __future__ import annotations

import re

_CORRECTION_PAT = re.compile(
    r"\b(wrong|no,?\s|not\s+(what|right)|that.{1,3}s\s+not|incorrect|nope|actually\s+no)\b",
    re.IGNORECASE,
)

_CLAIM_PAT = re.compile(
    r"(returns |is defined|is located|located in|exists in|in the file|the function|the class|implementation is)",
    re.IGNORECASE,
)

_EVIDENCE_PAT = re.compile(
    r"(Read|Grep|grep |glob|\bran\b|\bchecked\b|saw that|confirmed)",
    re.IGNORECASE,
)

_TOOL_ERROR_PAT = re.compile(
    r"(error|failed|exception|not found|timeout|refused|denied)",
    re.IGNORECASE,
)


def detect_user_correction(text: str) -> bool:
    """Return True if the user text contains a correction signal."""
    return bool(_CORRECTION_PAT.search(text))


def detect_unverified_claim(assistant_text: str) -> bool:
    """
    Return True if the assistant makes a factual claim without citing evidence.
    Conservative: returns False when unsure.
    """
    has_claim = bool(_CLAIM_PAT.search(assistant_text))
    has_evidence = bool(_EVIDENCE_PAT.search(assistant_text))
    return has_claim and not has_evidence


def detect_tool_error(tool_result_text: str) -> bool:
    """Return True if the tool result text signals an error condition."""
    if not tool_result_text:
        return False
    return bool(_TOOL_ERROR_PAT.search(tool_result_text))


def evaluate_turn(
    user_text: str,
    assistant_text: str,
    tool_result_text: str | None,
) -> list[tuple[str, str]]:
    """
    Evaluate a conversation turn and return (channel, trigger_label) pairs.
    Called after the assistant responds; compounds appropriate channels.
    """
    events: list[tuple[str, str]] = []

    if detect_user_correction(user_text):
        events.append(("vigilance", "user_correction"))
        events.append(("friction", "user_correction"))

    if detect_unverified_claim(assistant_text):
        events.append(("unverified_confidence", "unverified_claim"))

    if tool_result_text and detect_tool_error(tool_result_text):
        events.append(("vigilance", "tool_error"))
        events.append(("friction", "tool_error"))

    return events
