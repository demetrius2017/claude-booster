#!/usr/bin/env python3
"""Verify GPT-5.6 route defaults, migration, effort, and live policy contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(path: str):
    spec = importlib.util.spec_from_file_location("subject", ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    balancer = load("templates/scripts/model_balancer.py")
    capture = load("templates/scripts/model_metric_capture.py")
    expected = {
        "trivial": ("gpt-5.6-luna", "low"),
        "recon": ("gpt-5.6-luna", "low"),
        "medium": ("gpt-5.6-terra", "medium"),
        "coding": ("gpt-5.6-terra", "medium"),
        "hard": ("gpt-5.6-sol", "medium"),
        "lead": ("gpt-5.6-sol", "medium"),
        "consilium_bio": ("gpt-5.6-sol", "medium"),
    }
    for category, (model, effort) in expected.items():
        route = balancer.DEFAULTS["routing"][category]
        assert route["provider"] == "codex-cli"
        assert (route["model"], route["reasoning_effort"]) == (model, effort)

    legacy_categories = set(balancer._LEGACY_BOOTSTRAP_ROUTES) & set(expected)
    legacy = {"routing": {
        key: dict(balancer._LEGACY_BOOTSTRAP_ROUTES[key]) for key in legacy_categories
    }}
    migrated = balancer._with_default_routes(legacy)["routing"]
    for category in legacy_categories:
        model, effort = expected[category]
        assert (migrated[category]["model"], migrated[category]["reasoning_effort"]) == (model, effort)

    custom = {"provider": "codex-cli", "model": "custom-model", "note": "keep"}
    preserved = balancer._with_default_routes({"routing": {"coding": custom}})
    assert preserved["routing"]["coding"] == custom

    assert balancer.DEFAULTS["routing"]["high_blast_radius"]["provider"] == "anthropic"
    for model in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):
        assert capture._match_codex_command(f"codex exec -m {model} -") == model

    go = (ROOT / "templates/commands/go.md").read_text()
    skill = (ROOT / "templates/codex/skills/booster-command/SKILL.md").read_text()
    assert "Sol, Terra, and Luna are all OpenAI/Codex" in go
    assert "never select `xhigh` automatically" in skill
    assert "CODEX_REASONING_EFFORT" in go
    print("PASS: GPT-5.6 routes and effort contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
