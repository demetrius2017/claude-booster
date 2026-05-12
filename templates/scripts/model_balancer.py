#!/usr/bin/env python3
"""
model_balancer.py — Daily routing decision engine for model_balancer.

Purpose:
    Maintains ~/.claude/model_balancer.json as the single source of routing
    truth for all Claude Booster projects. Day-1 scope: passive observation
    + seed defaults. No metric analysis (day-N).

Contract (inputs/outputs):
    Reads  : ~/.claude/model_balancer.json (schema_version=2)
    Writes : ~/.claude/model_balancer.json (atomic via .tmp + os.replace)
    Backups: ~/.claude/model_balancer.json.bak.<YYYY-MM-DD> (max 7 kept)

CLI:
    model_balancer.py decide          Idempotent daily refresh.
    model_balancer.py get <category>  Print routing dict for one category.
    model_balancer.py show            Print full current decision JSON.
    model_balancer.py status          One-line freshness summary.

Library mode:
    from model_balancer import get_routing, current_decision
    routing = get_routing("coding")   # → {"provider": ..., "model": ...}
    decision = current_decision()     # → full dict

Limitations:
    - Day-1: no model_metrics reads; routing never changes from seed values.
    - Backup filename uses prior file's decision_date; if that field is
      missing, falls back to today's date with a "-unknown" suffix.
    - Backup rotation deletes only files matching the expected .bak.* pattern.

ENV:
    CLAUDE_MODEL_BALANCER_PATH  Override default ~/.claude/model_balancer.json

Files:
    ~/.claude/model_balancer.json           live routing file
    ~/.claude/model_balancer.json.tmp       transient atomic-write temp
    ~/.claude/model_balancer.json.bak.*     rolling backups (max 7)
    ~/.claude/model_balancer.json.preworker-bak  pre-session safety backup
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DEFAULT_PATH = Path.home() / ".claude" / "model_balancer.json"
_BALANCER_PATH: Path = Path(
    os.environ.get("CLAUDE_MODEL_BALANCER_PATH", str(_DEFAULT_PATH))
)
_MAX_BACKUPS = 7

# ---------------------------------------------------------------------------
# Hardcoded bootstrap defaults (used only when no prior JSON exists)
# ---------------------------------------------------------------------------

DEFAULTS: dict = {
    "schema_version": 2,
    "weight_profile": "balanced",
    "rationale": "bootstrap — no prior decision",
    "routing": {
        "trivial":        {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "recon":          {"provider": "anthropic", "model": "claude-haiku-4-5"},
        "medium":         {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "coding":         {"provider": "anthropic", "model": "claude-sonnet-4-6"},
        "hard":           {"provider": "anthropic", "model": "claude-opus-4-7"},
        "consilium_bio":  {"provider": "anthropic", "model": "claude-opus-4-7"},
        "audit_external": {"provider": "pal",       "model": "gpt-5.5"},
        "lead":           {"provider": "anthropic", "model": "claude-opus-4-7"},
        "high_blast_radius": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "applies_to": [
                "auth", "security", "secrets",
                "db_migrations", "financial_dml", "infra_config",
            ],
        },
    },
}

# Known routing categories (for validation)
_KNOWN_CATEGORIES = set(DEFAULTS["routing"].keys())

# ---------------------------------------------------------------------------
# Module-level cache (populated lazily on first get_routing / current_decision)
# ---------------------------------------------------------------------------

_cached_decision: dict | None = None


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _today_utc() -> str:
    """Return today's date in UTC as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _valid_until_str() -> str:
    """Return tomorrow midnight UTC as ISO8601 with Z suffix."""
    from datetime import timedelta
    tomorrow = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    return tomorrow.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_file() -> dict | None:
    """Load and parse the balancer JSON. Returns None on missing/parse error."""
    if not _BALANCER_PATH.exists():
        return None
    try:
        return json.loads(_BALANCER_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _backup_current(prior_date: str | None) -> None:
    """Copy current file to a dated backup; rotate to keep at most _MAX_BACKUPS."""
    if not _BALANCER_PATH.exists():
        return
    suffix = prior_date if prior_date else (_today_utc() + "-unknown")
    backup_path = _BALANCER_PATH.parent / f"model_balancer.json.bak.{suffix}"
    shutil.copy2(_BALANCER_PATH, backup_path)

    # Rotate — collect all .bak.* siblings, sort by name, delete oldest
    bak_dir = _BALANCER_PATH.parent
    bak_files = sorted(
        bak_dir.glob("model_balancer.json.bak.*"),
        key=lambda p: p.name,
    )
    while len(bak_files) > _MAX_BACKUPS:
        bak_files.pop(0).unlink(missing_ok=True)


def _write_atomic(data: dict) -> None:
    """Write *data* to the balancer path atomically via a temp file."""
    tmp = _BALANCER_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _BALANCER_PATH)


def _build_refreshed(prior: dict) -> dict:
    """
    Build a refreshed decision from *prior* file contents.
    Preserves routing shape; only updates decision_date and valid_until.
    """
    today = _today_utc()
    refreshed = dict(prior)
    refreshed["decision_date"] = today
    refreshed["valid_until"] = _valid_until_str()
    return refreshed


def _build_bootstrap() -> dict:
    """Build a brand-new decision from hardcoded DEFAULTS."""
    today = _today_utc()
    decision = dict(DEFAULTS)
    decision["decision_date"] = today
    decision["valid_until"] = _valid_until_str()
    return decision


# ---------------------------------------------------------------------------
# Core decide logic
# ---------------------------------------------------------------------------

def decide(*, force: bool = False) -> dict:
    """
    Idempotent daily refresh.

    - If today's decision exists → return it unchanged.
    - If stale or missing → back up + regenerate + write atomically.
    Returns the in-effect decision dict.
    """
    global _cached_decision
    today = _today_utc()
    prior = _load_file()

    if prior is not None and prior.get("decision_date") == today and not force:
        # Already fresh — no rewrite
        _cached_decision = prior
        return prior

    # Need to regenerate
    prior_date = prior.get("decision_date") if prior else None
    _backup_current(prior_date)

    if prior is not None:
        new_decision = _build_refreshed(prior)
    else:
        new_decision = _build_bootstrap()

    _write_atomic(new_decision)
    _cached_decision = new_decision
    return new_decision


# ---------------------------------------------------------------------------
# Public library API
# ---------------------------------------------------------------------------

def current_decision() -> dict:
    """Return in-effect decision dict (load once, cache)."""
    global _cached_decision
    if _cached_decision is None:
        _cached_decision = _load_file() or _build_bootstrap()
    return _cached_decision


def get_routing(category: str) -> dict:
    """
    Return routing dict for *category* from current decision.
    Raises KeyError if category unknown.
    """
    routing = current_decision().get("routing", {})
    if category not in routing:
        raise KeyError(f"Unknown routing category: {category!r}")
    return routing[category]


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------

def _cmd_decide(_args: argparse.Namespace) -> int:
    decision = decide()
    date = decision.get("decision_date", "?")
    source = "bootstrap" if decision.get("rationale", "").startswith("bootstrap") else "seed"
    print(f"decision_date={date}  source={source}  routing_keys={list(decision.get('routing', {}).keys())}")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    cat = args.category
    decision = current_decision()
    routing = decision.get("routing", {})
    if cat not in routing:
        print(f"error: unknown category {cat!r}. known: {sorted(routing.keys())}", file=sys.stderr)
        return 1
    print(json.dumps(routing[cat], indent=2))
    return 0


def _cmd_show(_args: argparse.Namespace) -> int:
    decision = current_decision()
    # Compact summary: schema + date + routing table
    out = {
        "schema_version": decision.get("schema_version"),
        "decision_date": decision.get("decision_date"),
        "valid_until": decision.get("valid_until"),
        "weight_profile": decision.get("weight_profile"),
        "routing": decision.get("routing", {}),
        "rationale": decision.get("rationale", ""),
    }
    print(json.dumps(out, indent=2))
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    decision = _load_file()
    if decision is None:
        print("decision_date=none, age=n/a, source=none, stale (no file)")
        return 0

    date_str = decision.get("decision_date", "")
    today = _today_utc()

    try:
        decision_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        age_hours = int((now_utc - decision_dt).total_seconds() // 3600)
        freshness = "fresh" if date_str == today else "stale"
    except ValueError:
        age_hours = -1
        freshness = "stale"

    rationale = decision.get("rationale", "")
    source = "bootstrap" if rationale.startswith("bootstrap") else "seed"
    print(f"decision_date={date_str}, age={age_hours}h, source={source}, {freshness}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="model_balancer",
        description="Daily routing decision engine for Claude Booster model_balancer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("decide", help="Idempotent daily refresh of routing decision.")

    get_p = sub.add_parser("get", help="Print routing for one category.")
    get_p.add_argument("category", help="Routing category name.")

    sub.add_parser("show", help="Print full current decision summary as JSON.")
    sub.add_parser("status", help="One-line freshness status.")

    args = parser.parse_args()

    dispatch = {
        "decide": _cmd_decide,
        "get": _cmd_get,
        "show": _cmd_show,
        "status": _cmd_status,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
