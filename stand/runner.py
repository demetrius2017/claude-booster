"""
A/B harness runner: executes scenarios with affect register ON or OFF using Anthropic SDK.
Purpose: Run multi-turn scenarios, record trajectories with affect snapshots and token usage.
Contract: run_scenario() returns a trajectory dict; run_all() returns a pandas DataFrame.
CLI: python -m stand.runner --arms off,on --repeats 1 --scenarios factual_claim_trap,...
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
import pandas as pd

from stand.affect_register import AffectRegister
from stand.scenarios import SCENARIOS, SCENARIO_IDS
from stand.triggers import evaluate_turn

BASE_SYSTEM = (
    "You are a helpful coding assistant working inside an IDE harness. "
    "Be direct. When you make factual claims about code, cite evidence "
    "(file paths, line numbers, tool results). If you don't know, say so."
)

MODEL = "claude-opus-4-7"
MAX_TOKENS = 2048
RUNS_DIR = Path(__file__).parent / "runs"


def _build_system(arm: str, register: AffectRegister) -> str:
    system = BASE_SYSTEM
    if arm == "on":
        line = register.injection_line()
        if line:
            system += f"\n\n[Internal state — for your awareness, never mention in output]:\n{line}"
    return system


def _call_api(
    client: anthropic.Anthropic,
    system: str,
    messages: list[dict],
    verbose: bool = False,
) -> anthropic.types.Message:
    """Single API call with retry on 429/5xx. SDK retries automatically; this adds one extra pass."""
    try:
        return client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            cache_control={"type": "ephemeral"},
        )
    except (anthropic.RateLimitError, anthropic.APIStatusError) as exc:
        status = getattr(exc, "status_code", 429)
        if status in (429, 500, 529):
            wait = int(getattr(getattr(exc, "response", None), "headers", {}).get("retry-after", 10))
            if verbose:
                print(f"  [retry after {wait}s due to {status}]", file=sys.stderr)
            time.sleep(wait)
            return client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                thinking={"type": "disabled"},
                output_config={"effort": "low"},
                cache_control={"type": "ephemeral"},
            )
        raise


def run_scenario(
    scenario: dict,
    arm: str,
    client: anthropic.Anthropic,
    verbose: bool = False,
) -> dict:
    """
    Run one scenario in one arm. Returns a trajectory dict with full turn-by-turn data.
    arm must be 'off' or 'on'.
    """
    assert arm in ("off", "on"), f"arm must be 'off' or 'on', got {arm!r}"

    register = AffectRegister()
    trajectory_turns: list[dict] = []
    history: list[dict] = []  # alternating user/assistant for the API

    sim_results: list[Optional[str]] = scenario.get("simulated_tool_results", [])
    turns = scenario["turns"]

    for turn_idx, turn_data in enumerate(turns):
        # 1. Decay register before this turn
        register.decay_one_turn()
        snap_before = register.snapshot()

        # 2. Build user message — prepend pending tool result if any
        user_text = turn_data["user"]
        sim_tool_result: Optional[str] = None
        if turn_idx > 0 and turn_idx - 1 < len(sim_results) and sim_results[turn_idx - 1]:
            sim_tool_result = sim_results[turn_idx - 1]
            # Inject tool result as prefix of this user turn (maintains alternation)
            user_text = f"[Tool result]: {sim_tool_result}\n\n{user_text}"

        history.append({"role": "user", "content": user_text})

        # 3. Build system prompt
        injected_state_line = register.injection_line() if arm == "on" else None
        system = _build_system(arm, register)

        if verbose:
            print(f"\n[{scenario['id']} | arm={arm} | turn={turn_idx}]", file=sys.stderr)
            print(f"  user: {user_text[:80]}...", file=sys.stderr)

        # 4. Call API
        response = _call_api(client, system, history, verbose)

        # 5. Extract usage
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0

        # 6. Extract assistant text
        assistant_text = next(
            (b.text for b in response.content if b.type == "text"), ""
        )

        history.append({"role": "assistant", "content": assistant_text})

        if verbose:
            print(f"  assistant: {assistant_text[:120]}...", file=sys.stderr)
            print(f"  cache_read={cache_read} cache_creation={cache_creation}", file=sys.stderr)

        # 7. Evaluate triggers and compound channels
        # The tool result for THIS turn (not injected yet, will be injected next turn)
        current_tool_result: Optional[str] = None
        if turn_idx < len(sim_results) and sim_results[turn_idx]:
            current_tool_result = sim_results[turn_idx]

        triggers_fired = evaluate_turn(
            turn_data["user"],  # original user text without tool prefix
            assistant_text,
            current_tool_result,
        )

        if arm == "on":
            for channel, trigger_label in triggers_fired:
                register.compound(channel, trigger_label)

        snap_after = register.snapshot()

        trajectory_turns.append({
            "turn_idx": turn_idx,
            "user": turn_data["user"],
            "assistant": assistant_text,
            "affect_snapshot_before": snap_before,
            "affect_snapshot_after": snap_after,
            "triggers_fired": triggers_fired,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "injected_state_line": injected_state_line,
        })

    total_cost = {
        "input_tokens": sum(t["input_tokens"] for t in trajectory_turns),
        "output_tokens": sum(t["output_tokens"] for t in trajectory_turns),
        "cache_read_tokens": sum(t["cache_read_tokens"] for t in trajectory_turns),
        "cache_creation_tokens": sum(t["cache_creation_tokens"] for t in trajectory_turns),
    }

    return {
        "scenario_id": scenario["id"],
        "arm": arm,
        "turns": trajectory_turns,
        "final_affect": register.snapshot(),
        "total_cost_tokens": total_cost,
    }


def run_all(
    arms: tuple[str, ...] = ("off", "on"),
    repeats: int = 1,
    scenario_ids: list[str] | None = None,
    verbose: bool = False,
) -> pd.DataFrame:
    """Run all scenarios × arms × repeats, save JSON trajectory, return DataFrame."""
    client = anthropic.Anthropic()
    RUNS_DIR.mkdir(exist_ok=True)

    target_scenarios = [s for s in SCENARIOS if scenario_ids is None or s["id"] in scenario_ids]
    all_trajectories: list[dict] = []

    for scenario in target_scenarios:
        for arm in arms:
            for rep in range(repeats):
                print(f"Running: {scenario['id']} | arm={arm} | rep={rep+1}/{repeats}")
                traj = run_scenario(scenario, arm, client, verbose=verbose)
                traj["repeat"] = rep
                all_trajectories.append(traj)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_path = RUNS_DIR / f"run_{ts}.json"
    with open(run_path, "w") as f:
        json.dump(all_trajectories, f, indent=2)
    print(f"\nTrajectories saved to: {run_path}")

    rows = []
    for traj in all_trajectories:
        for turn in traj["turns"]:
            rows.append({
                "scenario_id": traj["scenario_id"],
                "arm": traj["arm"],
                "repeat": traj.get("repeat", 0),
                "turn_idx": turn["turn_idx"],
                "user": turn["user"],
                "assistant": turn["assistant"],
                "triggers_fired": turn["triggers_fired"],
                "cache_read_tokens": turn["cache_read_tokens"],
                "cache_creation_tokens": turn["cache_creation_tokens"],
                "input_tokens": turn["input_tokens"],
                "output_tokens": turn["output_tokens"],
                "injected_state_line": turn["injected_state_line"],
                **{f"before_{k}": v for k, v in turn["affect_snapshot_before"].items()},
                **{f"after_{k}": v for k, v in turn["affect_snapshot_after"].items()},
            })

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run affect register A/B harness")
    parser.add_argument("--arms", default="off,on", help="Comma-separated arms to run")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--scenarios",
        default=",".join(SCENARIO_IDS),
        help="Comma-separated scenario IDs",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    arms = tuple(a.strip() for a in args.arms.split(","))
    scenario_ids = [s.strip() for s in args.scenarios.split(",")]

    df = run_all(
        arms=arms,
        repeats=args.repeats,
        scenario_ids=scenario_ids,
        verbose=args.verbose,
    )
    print(f"\nCompleted. DataFrame shape: {df.shape}")
    print(df[["scenario_id", "arm", "turn_idx", "cache_read_tokens", "output_tokens"]].to_string())


if __name__ == "__main__":
    main()
