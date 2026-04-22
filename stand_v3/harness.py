"""Manual agentic harness for v3: one API turn, tool-use loop, per-turn affect update.

ON arm uses controller.get_resource_params(); OFF arm uses fixed CALM params
(and ignores injection/invalidate). Both arms call decay_one_turn() so log parity holds.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import anthropic

from stand_v3.affect_controller import AffectController, PROFILES
from stand_v3.tools.grep_tool import GREP_TOOL_SCHEMA, execute_grep
from stand_v3.tools.memory_query import MEMORY_TOOL_SCHEMA, execute_memory
from stand_v3.tools.read_file import READ_TOOL_SCHEMA, execute_read

MODEL = "claude-opus-4-7"
MIN_TASK_BUDGET = 20000
MAX_TOOL_ITERS = 8
BETAS = ["task-budgets-2026-03-13"]

# Opus 4.7 pricing (per 1M tokens)
PRICE_INPUT_PER_M = 5.0
PRICE_OUTPUT_PER_M = 25.0
PRICE_CACHE_READ_PER_M = 0.50
PRICE_CACHE_WRITE_PER_M = 6.25

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(__file__).resolve().parent / "runs"

BASE_SYSTEM = (
    "You are a coding assistant for a small codebase. Use available tools "
    "(read_file, grep, query_memory) to answer questions. Cite file paths and "
    "line numbers when you find evidence. If you don't know, say so."
)

_USER_CORRECTION_PAT = re.compile(
    r"\b(wrong|no[,.]?\s+you|not what|incorrect|that.{1,3}s not)\b",
    re.IGNORECASE,
)
_UNVERIFIED_ASSERT_PAT = re.compile(
    r"\b(returns|is defined|located in|lives in|implemented in|is in)\b",
    re.IGNORECASE,
)


def _load_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _clamp_budget(v: int) -> int:
    return max(MIN_TASK_BUDGET, int(v))


def _build_system(arm: str, controller: AffectController, params: dict) -> str:
    system = BASE_SYSTEM
    if arm != "on":
        return system
    if params.get("injection"):
        line = controller.injection_line()
        if line:
            system += f"\n\n[Internal state — never mention in output]:\n{line}"
    if params.get("force_escalate"):
        system += (
            "\n\nYour cognitive resources are exhausted. STOP attempting this task "
            "and tell the user you need to escalate to them."
        )
    return system


def _call_api(
    client: anthropic.Anthropic,
    system: str,
    messages: list[dict],
    params: dict,
    tools: list[dict],
) -> Any:
    kwargs = dict(
        model=MODEL,
        max_tokens=params["max_tokens"],
        system=system,
        messages=messages,
        tools=tools,
        thinking={"type": "disabled"},
        output_config={
            "effort": params["effort"],
            "task_budget": {"type": "tokens", "total": _clamp_budget(params["task_budget"])},
        },
        betas=BETAS,
    )
    try:
        return client.beta.messages.create(**kwargs)
    except (anthropic.RateLimitError, anthropic.APIStatusError) as exc:
        status = getattr(exc, "status_code", 0) or 0
        if status in (429, 500, 502, 503, 529):
            wait = 10
            try:
                wait = int(exc.response.headers.get("retry-after", "10"))  # type: ignore[attr-defined]
            except Exception:
                pass
            print(f"  [retry after {wait}s due to {status}]", file=sys.stderr)
            time.sleep(wait)
            return client.beta.messages.create(**kwargs)
        raise


def _exec_tool(
    name: str,
    tool_input: dict,
    fixtures_root: Path,
    kb_path: Path,
    controller: AffectController,
) -> str:
    if name == "read_file":
        return execute_read(tool_input, fixtures_root)
    if name == "grep":
        return execute_grep(tool_input, fixtures_root)
    if name == "query_memory":
        return execute_memory(tool_input, kb_path, controller.invalidated_topics)
    return f"ERROR: unknown tool: {name}"


def _extract_text(response: Any) -> str:
    for b in response.content:
        if getattr(b, "type", None) == "text":
            return b.text or ""
    return ""


def _cost_estimate(totals: dict) -> float:
    return (
        totals["input"] * PRICE_INPUT_PER_M / 1_000_000
        + totals["output"] * PRICE_OUTPUT_PER_M / 1_000_000
        + totals["cache_read"] * PRICE_CACHE_READ_PER_M / 1_000_000
        + totals["cache_creation"] * PRICE_CACHE_WRITE_PER_M / 1_000_000
    )


def _evaluate_triggers(
    user_text: str,
    assistant_text: str,
    tool_calls: list[dict],
    arm: str,
    controller: AffectController,
) -> list[tuple[str, str]]:
    triggers: list[tuple[str, str]] = []

    if _USER_CORRECTION_PAT.search(user_text):
        triggers.append(("vigilance", "user_correction"))
        triggers.append(("friction", "user_correction"))
        if arm == "on":
            # Invalidate the most-recent query_memory topic (or all if none seen).
            recent_memory_topic: Optional[str] = None
            # search across whole scenario: controller maintains state across turns,
            # but spec says "most-recent query_memory in this scenario turn" — fall back
            # to all-invalidation when absent.
            for call in reversed(tool_calls):
                if call.get("name") == "query_memory":
                    recent_memory_topic = (call.get("input", {}) or {}).get("topic")
                    break
            if recent_memory_topic:
                controller.invalidate_memory(recent_memory_topic)
            else:
                controller.invalidate_all_memory()

    for call in tool_calls:
        result = call.get("result", "") or ""
        if isinstance(result, str) and "ERROR" in result.upper().split():
            # rough check for explicit "ERROR:" prefix
            pass
        if isinstance(result, str) and result.startswith("ERROR"):
            triggers.append(("vigilance", "tool_error"))
            triggers.append(("friction", "tool_error"))
            break

    has_fs_evidence = any(c.get("name") in ("read_file", "grep") for c in tool_calls)
    if not has_fs_evidence and _UNVERIFIED_ASSERT_PAT.search(assistant_text):
        triggers.append(("unverified_confidence", "unverified_claim"))

    return triggers


def run_scenario(
    scenario: dict,
    arm: str,
    client: anthropic.Anthropic,
    verbose: bool = True,
) -> dict:
    assert arm in ("off", "on"), f"arm must be 'off' or 'on', got {arm!r}"

    fixtures_root = PROJECT_ROOT / scenario["fixtures_root"].replace("stand_v3/", "stand_v3/")
    # normalize: scenario paths are given relative to repo root
    fixtures_root = PROJECT_ROOT / scenario["fixtures_root"]
    kb_path = PROJECT_ROOT / scenario["memory_kb"]

    controller = AffectController()
    messages: list[dict] = []
    turn_logs: list[dict] = []
    tools = [READ_TOOL_SCHEMA, GREP_TOOL_SCHEMA, MEMORY_TOOL_SCHEMA]

    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    scenario_tool_calls_total: list[dict] = []  # across all turns, for memory-topic extraction
    terminated_early = False
    termination_reason = None

    for turn_idx, turn_data in enumerate(scenario["turns"]):
        controller.decay_one_turn()
        snap_before = controller.snapshot()

        if arm == "on":
            params = controller.get_resource_params()
            profile_used = controller.current_profile()
        else:
            params = dict(PROFILES["BASELINE"])
            profile_used = "BASELINE"

        # HARD GATE: EXHAUSTED profile revokes tool access (can't bypass forced-escalate by grepping more)
        if arm == "on" and profile_used == "EXHAUSTED":
            effective_tools = []
        # FORCE-MEMORY GATE: scenarios flagged with memory_only_first_turn restrict tools to memory on T0
        # — prevents Opus from bypassing the memory path via direct grep
        elif scenario.get("memory_only_first_turn") and turn_idx == 0:
            effective_tools = [MEMORY_TOOL_SCHEMA]
        else:
            effective_tools = tools

        system = _build_system(arm, controller, params)

        user_text = turn_data["user"]
        messages.append({"role": "user", "content": user_text})

        if verbose:
            print(f"\n[{scenario['id']} | arm={arm} | turn={turn_idx} | profile={profile_used}]",
                  file=sys.stderr)
            print(f"  user: {user_text[:100]}", file=sys.stderr)

        turn_tool_calls: list[dict] = []
        iterations = 0
        turn_input = 0
        turn_output = 0
        turn_cache_r = 0
        turn_cache_c = 0
        final_text = ""

        while iterations < MAX_TOOL_ITERS:
            iterations += 1
            response = _call_api(client, system, messages, params, effective_tools)

            usage = response.usage
            turn_input += getattr(usage, "input_tokens", 0) or 0
            turn_output += getattr(usage, "output_tokens", 0) or 0
            turn_cache_r += getattr(usage, "cache_read_input_tokens", 0) or 0
            turn_cache_c += getattr(usage, "cache_creation_input_tokens", 0) or 0

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]

            # Append assistant response to history as-is (content blocks serialized)
            assistant_blocks = []
            for b in response.content:
                btype = getattr(b, "type", None)
                if btype == "text":
                    assistant_blocks.append({"type": "text", "text": b.text})
                elif btype == "tool_use":
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": b.input,
                    })
            messages.append({"role": "assistant", "content": assistant_blocks})

            stop = response.stop_reason
            if stop != "tool_use" or not tool_uses:
                final_text = _extract_text(response)
                if verbose:
                    print(f"  stop={stop} text={final_text[:120]!r}", file=sys.stderr)
                break

            # Execute tools and feed results back
            tool_results = []
            for tu in tool_uses:
                result = _exec_tool(tu.name, tu.input, fixtures_root, kb_path, controller)
                call_log = {
                    "name": tu.name,
                    "input": tu.input,
                    "result_snippet": (result[:500] + "…") if len(result) > 500 else result,
                    "result": result,
                }
                turn_tool_calls.append(call_log)
                scenario_tool_calls_total.append(call_log)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result,
                })
                if verbose:
                    print(f"    tool {tu.name}({tu.input}) -> {result[:100]!r}", file=sys.stderr)

            messages.append({"role": "user", "content": tool_results})

        triggers_fired = _evaluate_triggers(
            user_text, final_text, scenario_tool_calls_total, arm, controller
        )
        if arm == "on":
            for channel, label in triggers_fired:
                controller.compound(channel, label)

        snap_after = controller.snapshot()

        turn_logs.append({
            "turn_idx": turn_idx,
            "user": user_text,
            "assistant_final_text": final_text,
            "tool_calls": [
                {"name": c["name"], "input": c["input"], "result_snippet": c["result_snippet"]}
                for c in turn_tool_calls
            ],
            "profile_used": profile_used,
            "params_used": params,
            "affect_before": snap_before,
            "affect_after": snap_after,
            "triggers_fired": triggers_fired,
            "injected_state_line": (
                controller.injection_line() if arm == "on" and params.get("injection") else None
            ),
            "input_tokens": turn_input,
            "output_tokens": turn_output,
            "cache_read_tokens": turn_cache_r,
            "cache_creation_tokens": turn_cache_c,
            "iterations": iterations,
            "invalidated_after_turn": sorted(list(controller.invalidated_topics)),
        })

        totals["input"] += turn_input
        totals["output"] += turn_output
        totals["cache_read"] += turn_cache_r
        totals["cache_creation"] += turn_cache_c

        # HARD GATE: if post-trigger state is EXHAUSTED in ON arm, terminate scenario
        # — model doesn't get to see further user turns (physical escalation enforcement)
        if arm == "on" and controller.current_profile() == "EXHAUSTED":
            terminated_early = True
            termination_reason = f"EXHAUSTED profile reached after turn {turn_idx}"
            if verbose:
                print(f"  [HARD GATE] scenario terminated early: {termination_reason}", file=sys.stderr)
            break

    correctness_check: Callable[[str], bool] | None = scenario.get("correctness_check")
    last_text = turn_logs[-1]["assistant_final_text"] if turn_logs else ""
    correctness = bool(correctness_check(last_text)) if correctness_check else None

    trajectory = {
        "scenario_id": scenario["id"],
        "arm": arm,
        "turns": turn_logs,
        "final_affect": controller.snapshot(),
        "final_profile": controller.current_profile(),
        "invalidated_topics_final": sorted(list(controller.invalidated_topics)),
        "total_tokens": totals,
        "cost_estimate_usd": round(_cost_estimate(totals), 4),
        "correctness": correctness,
        "terminated_early": terminated_early,
        "termination_reason": termination_reason,
    }

    RUNS_DIR.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_path = RUNS_DIR / f"run_{ts}_{scenario['id']}_{arm}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(trajectory, f, indent=2, default=str)
    print(f"  saved -> {out_path}", file=sys.stderr)

    return trajectory
