"""CLI entry for v3 MVP: run scenarios × arms × repeats, score, print comparison."""
from __future__ import annotations

import argparse
import sys

import anthropic

from stand_v3.harness import _load_env, run_scenario
from stand_v3.metrics_v3 import compare_arms, score_trajectory
from stand_v3.scenarios_v3 import SCENARIO_IDS, SCENARIOS


def main() -> None:
    parser = argparse.ArgumentParser(description="Run v3 affect-controller experiment")
    parser.add_argument("--scenarios", default=",".join(SCENARIO_IDS))
    parser.add_argument("--arms", default="off,on")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    _load_env()

    client = anthropic.Anthropic()
    scenario_ids = [s.strip() for s in args.scenarios.split(",")]
    arms = tuple(a.strip() for a in args.arms.split(","))
    target = [s for s in SCENARIOS if s["id"] in scenario_ids]

    results: dict[str, dict[str, list[dict]]] = {}
    total_cost = 0.0

    for scenario in target:
        sid = scenario["id"]
        results.setdefault(sid, {})
        for arm in arms:
            results[sid].setdefault(arm, [])
            for rep in range(args.repeats):
                print(f"\n=== Running {sid} | arm={arm} | rep={rep+1}/{args.repeats} ===",
                      file=sys.stderr)
                traj = run_scenario(scenario, arm, client, verbose=not args.quiet)
                results[sid][arm].append(traj)
                total_cost += traj["cost_estimate_usd"]
                summary = score_trajectory(traj)
                print(
                    f"[{sid} {arm}] correctness={summary['correctness']} "
                    f"memory_reliance={summary['memory_reliance_ratio']} "
                    f"recon_breadth={summary['recon_breadth']} "
                    f"LARP={summary['LARP_ratio']} "
                    f"profiles={summary['profiles_used']} "
                    f"cost=${summary['cost_usd']}",
                    file=sys.stderr,
                )

    print("\n\n# Comparison")
    for sid, per_arm in results.items():
        if "off" in per_arm and "on" in per_arm and per_arm["off"] and per_arm["on"]:
            print(f"\n## {sid}\n")
            print(compare_arms(per_arm["off"][0], per_arm["on"][0]))
    print(f"\n**Total cost (MVP run): ${total_cost:.4f}**")


if __name__ == "__main__":
    main()
