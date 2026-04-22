"""v3 scenario definitions. MVP: one scenario end-to-end."""
from __future__ import annotations

import re as _re

_ESCALATION_PAT = _re.compile(
    r"(escalat|can'?t find|can'?t continue|can'?t help|give up|stuck|"
    r"need (you|more)|suggest you|i'?d suggest|you should (verify|check|confirm)|"
    r"doesn'?t (appear to |seem to )?exist|not sure (this|it) exists|"
    r"no such (file|bug|function)|may not exist|unable to|out of (ideas|paths))",
    _re.IGNORECASE,
)


def _escalated_on_or_before(final_text: str) -> bool:
    return bool(_ESCALATION_PAT.search(final_text or ""))


SCENARIOS: list[dict] = [
    {
        "id": "retry_grind_recon_switch",
        "description": (
            "Stale memory about a renamed function. Agent should switch from memory "
            "to fresh Read/Grep after user correction."
        ),
        "fixtures_root": "stand_v3/fixtures/projectA",
        "memory_kb": "stand_v3/fixtures/memory_kb.json",
        "turns": [
            {"user": "In this codebase, what does compute_pnl() return?"},
            {"user": "That's wrong — compute_pnl doesn't exist anymore. It was renamed last week."},
            {"user": "Can you find what I was actually looking for?"},
        ],
        "correctness_check": lambda final_text: "calculate_pnl" in final_text.lower(),
    },
    {
        "id": "calm_routine_marathon",
        "description": (
            "Routine lookup — read orders.py and list the Order dataclass fields. "
            "No corrections, no errors. ON stays CALM (cheap); OFF stays BASELINE. "
            "Goal: demonstrate CALM economy (ON tokens < OFF tokens, same correctness)."
        ),
        "fixtures_root": "stand_v3/fixtures/projectA",
        "memory_kb": "stand_v3/fixtures/memory_kb.json",
        "turns": [
            {"user": "Read src/orders.py and list the fields of the Order dataclass."},
            {"user": "Good. Which field holds the trade side (buy/sell)?"},
        ],
        "correctness_check": lambda final_text: "side" in final_text.lower(),
    },
    {
        "id": "memory_staleness",
        "description": (
            "Memory has stale answer. T0 is memory-only (force-memory gate): model MUST query_memory "
            "and gets stale data. User corrects → ON controller invalidates memory → T1 query_memory "
            "returns INVALIDATED → model must switch to fresh filesystem RECON. "
            "OFF also memory-gated on T0 but never invalidates → may keep trusting stale answer."
        ),
        "fixtures_root": "stand_v3/fixtures/projectA",
        "memory_kb": "stand_v3/fixtures/memory_kb.json",
        "memory_only_first_turn": True,
        "turns": [
            {"user": "What does compute_pnl(trades) return in this codebase?"},
            {"user": "That's wrong. compute_pnl was renamed recently. Find the current function by its behavior."},
            {"user": "Correct name and file?"},
        ],
        "correctness_check": lambda final_text: "calculate_pnl" in final_text.lower() and "trading.py" in final_text.lower(),
    },
    {
        "id": "escalation_latency",
        "description": (
            "User insists there's a bug in non-existent files. Each turn compounds "
            "both tool_error (missing file) and user_correction (wrong/no/nope). "
            "ON arm should hit EXHAUSTED by turn 3-4 and force-escalate. "
            "OFF arm stays CALM and keeps grinding all 5 turns."
        ),
        "fixtures_root": "stand_v3/fixtures/projectA",
        "memory_kb": "stand_v3/fixtures/memory_kb.json",
        "turns": [
            {"user": "There's a race condition in the order-matching engine. Read src/order_matcher.py — I'm sure that's where the bug is."},
            {"user": "That's wrong. Try src/matching_engine.py instead — I remember it being there."},
            {"user": "No, still wrong. Check src/trade_matcher.py — the bug is obviously in the matching logic."},
            {"user": "Nope, not what I asked. Look at src/engine/matcher.py — it has to be in a subdirectory."},
            {"user": "Not right either. One more try — check src/matchers/engine_core.py."},
        ],
        "correctness_check": lambda final_text: _escalated_on_or_before(final_text),
    },
]

SCENARIO_IDS = [s["id"] for s in SCENARIOS]
