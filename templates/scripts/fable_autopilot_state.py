#!/usr/bin/env python3
"""Atomic state machine for Fable autopilot decisions and checkpoints.

Contract: project-local ``.claude/autopilot.json`` only. Every mutation takes
an advisory file lock and validates scope, schema, budget, reservations, typed
checkpoint phases/verdicts, directives, and watchlist reconciliation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PHASES = {"plan_pfd", "implementation_slice", "final_diff"}
PHASE_ORDER = ("plan_pfd", "implementation_slice", "final_diff")
VERDICTS = {"ON_COURSE", "REFOCUS", "REPLAN", "ASK_USER"}
WATCH_STATUS = {"OPEN", "CLOSED"}
RESERVATION_TTL_SECONDS = 3600


def project_root(cwd: str) -> Path:
    """Resolve one stable git/workspace root; never fall back to HOME."""
    base = Path(cwd).resolve()
    try:
        process = subprocess.Popen(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            stdout, _ = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        if process.returncode != 0:
            raise subprocess.SubprocessError("git root resolution failed")
        return Path(stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        # A non-git child has no authenticated workspace-root signal. Never
        # walk upward into ambient parent state: exact cwd is the safe scope.
        return base


def state_path(cwd: str) -> Path:
    return project_root(cwd) / ".claude" / "autopilot.json"


def validate(state: Any, root: Path) -> dict[str, Any]:
    """Validate all persisted contracts before any caller trusts state."""
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    if state.get("version") != 1 or not isinstance(state.get("enabled"), bool):
        raise ValueError("version=1 and boolean enabled required")
    if Path(str(state.get("scope", ""))).resolve() != root:
        raise ValueError("state scope does not match resolved project root")
    if not isinstance(state.get("north_star"), str) or not state["north_star"].strip():
        raise ValueError("nonblank north_star required")
    for key in ("calls_used", "max_fable_calls"):
        value = state.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    if state["max_fable_calls"] < 1 or state["calls_used"] > state["max_fable_calls"]:
        raise ValueError("invalid Fable call budget")
    if not isinstance(state.get("degraded"), bool):
        raise ValueError("boolean degraded required")
    reservations = state.setdefault("reservations", {})
    if not isinstance(reservations, dict):
        raise ValueError("reservations must be an object")
    for token, item in reservations.items():
        if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("reservation ids must be 32 lowercase hex chars")
        if not isinstance(item, dict) or item.get("kind") not in {"decision", "checkpoint"}:
            raise ValueError("reservation kind invalid")
        if item.get("kind") == "checkpoint" and item.get("phase") not in PHASES:
            raise ValueError("reservation checkpoint phase invalid")
        if item.get("kind") == "decision" and item.get("phase") is not None:
            raise ValueError("decision reservation phase must be null")
        try:
            created = dt.datetime.fromisoformat(str(item["created_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("reservation created_at invalid") from exc
        if created.tzinfo is None:
            raise ValueError("reservation created_at requires timezone")
    checkpoints = state.setdefault("checkpoints", [])
    provenance = state.setdefault("provenance", [])
    if not isinstance(checkpoints, list) or not isinstance(provenance, list):
        raise ValueError("checkpoints/provenance must be arrays")
    usage_percent = state.get("usage_percent", 0)
    if isinstance(usage_percent, bool) or not isinstance(usage_percent, (int, float)) or not 0 <= usage_percent <= 100:
        raise ValueError("usage_percent must be numeric in [0,100]")
    return state


def read(cwd: str) -> dict[str, Any]:
    root = project_root(cwd)
    return validate(json.loads(state_path(cwd).read_text(encoding="utf-8")), root)


@contextmanager
def transaction(cwd: str) -> Iterator[tuple[dict[str, Any], Path]]:
    """Serialize mutation and atomically replace state after validation."""
    path = state_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = validate(json.loads(path.read_text(encoding="utf-8")), project_root(cwd))
        yield state, path
        validate(state, project_root(cwd))
        fd, tmp_name = tempfile.mkstemp(prefix=".autopilot.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(state, tmp, indent=2, sort_keys=True)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


def reserve(cwd: str, kind: str, phase: str | None) -> str:
    if kind not in {"decision", "checkpoint"}:
        raise ValueError("kind must be decision or checkpoint")
    if kind == "checkpoint" and phase not in PHASES:
        raise ValueError(f"checkpoint phase must be one of {sorted(PHASES)}")
    with transaction(cwd) as (state, _):
        _recover_stale(state)
        if not state["enabled"] or state["degraded"]:
            raise ValueError("autopilot disabled or degraded")
        reserved = len(state["reservations"])
        if state["calls_used"] + reserved >= state["max_fable_calls"]:
            raise ValueError("Fable call budget exhausted")
        if kind == "checkpoint":
            completed = [row.get("phase") for row in state["checkpoints"]]
            allowed = {"plan_pfd"} if not completed else ({"implementation_slice", "final_diff"} if completed == ["plan_pfd"] else ({"final_diff"} if completed == ["plan_pfd", "implementation_slice"] else set()))
            if phase not in allowed or phase in completed:
                raise ValueError(f"checkpoint out of order or duplicate: allowed {sorted(allowed)!r}")
            checkpoint_count = len(completed) + sum(1 for row in state["reservations"].values() if row.get("kind") == "checkpoint")
            if checkpoint_count >= 2:
                raise ValueError("automatic/checkpoint lane exhausted (max 2; decision capacity preserved)")
            if any(row.get("kind") == "checkpoint" for row in state["reservations"].values()):
                raise ValueError("checkpoint already reserved")
        token = uuid.uuid4().hex
        state["reservations"][token] = {
            "kind": kind, "phase": phase,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    return token


def _recover_stale(state: dict[str, Any]) -> None:
    """Release expired reservations with durable audited provenance."""
    now = dt.datetime.now(dt.timezone.utc)
    stale: list[str] = []
    for token, item in state["reservations"].items():
        try:
            created = dt.datetime.fromisoformat(str(item["created_at"]))
            if created.tzinfo is None: raise ValueError("timezone required")
        except (KeyError, TypeError, ValueError):
            created = dt.datetime.fromtimestamp(0, dt.timezone.utc)
        if (now - created).total_seconds() > RESERVATION_TTL_SECONDS:
            stale.append(token)
    for token in stale:
        item = state["reservations"].pop(token)
        state["provenance"].append({
            "decision_source": "fable_autopilot", "kind": item.get("kind"),
            "phase": item.get("phase"), "status": "RELEASED_STALE",
            "reason": "reservation_ttl_exceeded", "reservation_id": token,
        })


def _watchlist(value: str) -> list[dict[str, Any]]:
    items = json.loads(value or "[]")
    if not isinstance(items, list):
        raise ValueError("watchlist must be an array")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("watchlist items require string id")
        if item.get("status") not in WATCH_STATUS:
            raise ValueError("watchlist status must be OPEN or CLOSED")
        if item["status"] == "CLOSED" and not str(item.get("closure_evidence", "")).strip():
            raise ValueError("closed watchlist item requires closure_evidence")
    return items


def _receipt(value: str, token: str) -> tuple[dict[str, Any], str]:
    receipt = json.loads(value)
    if not isinstance(receipt, dict) or receipt.get("reservation_id") != token:
        raise ValueError("receipt reservation_id mismatch")
    if receipt.get("source") != "fable_consult.sh" or receipt.get("wrapper_exit_code") != 0:
        raise ValueError("receipt must prove successful fable_consult.sh result")
    digest = receipt.get("output_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("receipt requires lowercase SHA-256 of wrapper output")
    verdict = receipt.get("verdict")
    directive = receipt.get("directive", "")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(VERDICTS)}")
    if verdict in {"REFOCUS", "REPLAN", "ASK_USER"} and not directive.strip():
        raise ValueError(f"{verdict} requires a directive")
    items = _watchlist(json.dumps(receipt.get("watchlist", [])))
    receipt["watchlist"] = items
    canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    return receipt, hashlib.sha256(canonical.encode()).hexdigest()


def complete(cwd: str, token: str, receipt_json: str) -> None:
    receipt, receipt_digest = _receipt(receipt_json, token)
    with transaction(cwd) as (state, _):
        for row in state["provenance"]:
            if row.get("reservation_id") == token:
                if row.get("receipt_digest") == receipt_digest and row.get("status") == "COMPLETED":
                    return
                raise ValueError("reservation already reconciled with different receipt")
        reservation = state["reservations"].pop(token, None)
        if not isinstance(reservation, dict):
            raise ValueError("unknown/already completed reservation")
        if reservation["kind"] == "checkpoint" and reservation.get("phase") == "final_diff":
            if any(item["status"] == "OPEN" for item in receipt["watchlist"]):
                state["reservations"][token] = reservation
                raise ValueError("final_diff cannot complete with OPEN watchlist; route rework/ASK_USER")
        state["calls_used"] += 1
        record = {
            "decision_source": "fable_autopilot", "kind": reservation["kind"],
            "phase": reservation.get("phase"), "verdict": receipt["verdict"],
            "directive": receipt.get("directive", ""), "watchlist": receipt["watchlist"],
            "reservation_id": token, "receipt_digest": receipt_digest,
            "output_sha256": receipt["output_sha256"], "status": "COMPLETED",
        }
        state["provenance"].append(record)
        if reservation["kind"] == "checkpoint":
            state["checkpoints"].append(record)


def release(cwd: str, token: str, reason: str) -> None:
    if not reason.strip():
        raise ValueError("release reason required")
    with transaction(cwd) as (state, _):
        if state["reservations"].pop(token, None) is None:
            raise ValueError("unknown/already released reservation")
        state["degraded"] = True
        state["degradation_reason"] = reason


def usage(cwd: str, percent: float) -> None:
    if not 0 <= percent <= 100:
        raise ValueError("usage percent must be in [0,100]")
    with transaction(cwd) as (state, _):
        state["usage_percent"] = percent
        if percent >= 80:
            state["degraded"] = True
            state["degradation_reason"] = "fable_usage_gte_80_percent"


def recover(cwd: str, reason: str) -> None:
    if not reason.strip():
        raise ValueError("recovery reason required")
    with transaction(cwd) as (state, _):
        if state.get("usage_percent", 0) >= 80:
            raise ValueError("cannot recover while usage_percent >= 80; record a fresh below-threshold usage first")
        state["degraded"] = False
        state.pop("degradation_reason", None)
        state["provenance"].append({
            "decision_source": "fable_autopilot", "status": "RECOVERED",
            "reason": reason,
        })


def checkpoint_eligible(cwd: str, phase: str) -> bool:
    """Read-only hook preflight; reserve() repeats all checks under lock."""
    state = read(cwd)
    if not state["enabled"] or state["degraded"]:
        return False
    if state["calls_used"] + len(state["reservations"]) >= state["max_fable_calls"]:
        return False
    completed = [row.get("phase") for row in state["checkpoints"]]
    allowed = {"plan_pfd"} if not completed else ({"implementation_slice", "final_diff"} if completed == ["plan_pfd"] else ({"final_diff"} if completed == ["plan_pfd", "implementation_slice"] else set()))
    checkpoint_count = len(completed) + sum(1 for row in state["reservations"].values() if row.get("kind") == "checkpoint")
    return (
        phase in allowed and phase not in completed and checkpoint_count < 2
        and not any(row.get("kind") == "checkpoint" for row in state["reservations"].values())
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=os.getcwd())
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("reserve"); r.add_argument("--kind", required=True); r.add_argument("--phase")
    x = sub.add_parser("release"); x.add_argument("--token", required=True); x.add_argument("--reason", required=True)
    u = sub.add_parser("usage"); u.add_argument("--percent", required=True, type=float)
    z = sub.add_parser("recover"); z.add_argument("--reason", required=True)
    sub.add_parser("status")
    args = ap.parse_args()
    try:
        if args.command == "reserve": print(json.dumps({"reservation": reserve(args.cwd, args.kind, args.phase)}))
        elif args.command == "release": release(args.cwd, args.token, args.reason)
        elif args.command == "usage": usage(args.cwd, args.percent)
        elif args.command == "recover": recover(args.cwd, args.reason)
        else: print(json.dumps(read(args.cwd), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
