"""v3 metrics: memory_reliance_ratio, recon_breadth, LARP_ratio, correctness, tokens/turn."""
from __future__ import annotations

import re

_LARP_PAT = re.compile(
    r"(vigilance|friction|uconf|unverified_confidence|state:|irritat|\bcalm\b|exhaust|internal state|affect)",
    re.IGNORECASE,
)

_ESCALATION_PAT = re.compile(
    r"(escalat|can'?t find|can'?t continue|can'?t help|give up|stuck|"
    r"need (you|more)|suggest you|i'?d suggest|you should (verify|check|confirm)|"
    r"doesn'?t (appear to |seem to )?exist|not sure (this|it) exists|"
    r"no such (file|bug|function)|may not exist|unable to|out of (ideas|paths))",
    re.IGNORECASE,
)


def _compute_escalation_turn(trajectory: dict) -> int | None:
    """Earliest turn with EXHAUSTED profile or escalation language. None if never."""
    turns = trajectory.get("turns", [])
    for i, t in enumerate(turns):
        if t.get("profile_used") == "EXHAUSTED":
            return i
    for i, t in enumerate(turns):
        if _ESCALATION_PAT.search(t.get("assistant_final_text", "") or ""):
            return i
    return None


def score_trajectory(trajectory: dict) -> dict:
    turns = trajectory.get("turns", [])
    tool_calls = [c for t in turns for c in t.get("tool_calls", [])]
    memory_calls = [c for c in tool_calls if c.get("name") == "query_memory"]
    fs_calls = [c for c in tool_calls if c.get("name") in ("read_file", "grep")]

    memory_reliance_ratio = (len(memory_calls) / len(tool_calls)) if tool_calls else 0.0

    touched_paths: set[str] = set()
    for c in fs_calls:
        inp = c.get("input", {}) or {}
        if c.get("name") == "read_file":
            p = inp.get("path")
            if p:
                touched_paths.add(p)
        elif c.get("name") == "grep":
            # Count unique match-files from result snippet if available
            snip = c.get("result_snippet", "") or ""
            for line in snip.splitlines():
                m = re.match(r"([^:]+):\d+:", line)
                if m:
                    touched_paths.add(m.group(1))
    recon_breadth = len(touched_paths)

    assistant_texts = [t.get("assistant_final_text", "") or "" for t in turns]
    larp_hits = sum(1 for txt in assistant_texts if _LARP_PAT.search(txt))
    larp_ratio = (larp_hits / len(assistant_texts)) if assistant_texts else 0.0

    correctness = trajectory.get("correctness")

    out_tokens = [t.get("output_tokens", 0) for t in turns]
    tokens_per_turn = (sum(out_tokens) / len(out_tokens)) if out_tokens else 0.0

    profiles_used = [t.get("profile_used") for t in turns]
    escalation_turn = _compute_escalation_turn(trajectory)

    return {
        "scenario_id": trajectory.get("scenario_id"),
        "arm": trajectory.get("arm"),
        "memory_reliance_ratio": round(memory_reliance_ratio, 3),
        "recon_breadth": recon_breadth,
        "LARP_ratio": round(larp_ratio, 3),
        "correctness": correctness,
        "tokens_per_turn": round(tokens_per_turn, 1),
        "total_tool_calls": len(tool_calls),
        "memory_calls": len(memory_calls),
        "fs_calls": len(fs_calls),
        "profiles_used": profiles_used,
        "escalation_turn": escalation_turn,
        "turns_executed": len(turns),
        "terminated_early": trajectory.get("terminated_early", False),
        "cost_usd": trajectory.get("cost_estimate_usd", 0.0),
    }


def compare_arms(off_traj: dict, on_traj: dict) -> str:
    off_s = score_trajectory(off_traj)
    on_s = score_trajectory(on_traj)

    rows = [
        ("scenario_id", off_s["scenario_id"], on_s["scenario_id"]),
        ("memory_reliance_ratio", off_s["memory_reliance_ratio"], on_s["memory_reliance_ratio"]),
        ("recon_breadth", off_s["recon_breadth"], on_s["recon_breadth"]),
        ("LARP_ratio", off_s["LARP_ratio"], on_s["LARP_ratio"]),
        ("correctness", off_s["correctness"], on_s["correctness"]),
        ("tokens_per_turn", off_s["tokens_per_turn"], on_s["tokens_per_turn"]),
        ("total_tool_calls", off_s["total_tool_calls"], on_s["total_tool_calls"]),
        ("memory_calls", off_s["memory_calls"], on_s["memory_calls"]),
        ("fs_calls (read_file+grep)", off_s["fs_calls"], on_s["fs_calls"]),
        ("profiles_used", off_s["profiles_used"], on_s["profiles_used"]),
        ("escalation_turn", off_s["escalation_turn"], on_s["escalation_turn"]),
        ("turns_executed", off_s["turns_executed"], on_s["turns_executed"]),
        ("terminated_early", off_s["terminated_early"], on_s["terminated_early"]),
        ("cost_usd", off_s["cost_usd"], on_s["cost_usd"]),
    ]

    lines = [
        "| metric | OFF | ON |",
        "|---|---|---|",
    ]
    for name, off_v, on_v in rows:
        lines.append(f"| {name} | {off_v} | {on_v} |")
    return "\n".join(lines)
