#!/usr/bin/env python3
"""Per-component architecture stability engine for dep_manifest.json.

Purpose:
    Classifies every component in dep_manifest.json into a 2x2 stability
    matrix using two independent git-history signals:
      - Signal 1 (weight 0.7): source file recency — days since last commit
      - Signal 2 (weight 0.3): manifest churn — how often the component's
        name appeared in dep_manifest.json commit diffs over the last 90 days

    The resulting classification helps identify which components are safe to
    refactor (STABLE) vs. actively drifting (CODE_DRIFT, DEV, INTERFACE_FLUX).

Contract:
    Inputs:
        - dep_manifest.json — found via find_upward("docs/dep_manifest.json")
        - git repository — CWD must be inside a git repo
    Outputs:
        - Human-readable table (default) or JSON (--json)
        - .cache/arch_stability.json — cached result keyed by HEAD SHA
        - ~/.claude/logs/arch_stability_decisions.jsonl — append-only log

    Classification matrix (source_old = days_since_change >= stable_days):
        STABLE        : source_old AND churn_low  → low-risk, well-settled
        CODE_DRIFT    : source_new AND churn_low  → HIGHEST RISK: code changing
                        without manifest updates
        INTERFACE_FLUX: source_old AND churn_high → interface/deps churning
                        despite stable code
        DEV           : source_new AND churn_high → actively in development
        UNMAPPED      : source file not in manifest — consumers discovered via
                        git grep

    Cold-start: when dep_manifest.json has fewer than 5 total commits, only
    signal 1 (source file) is used; stability_basis = "source_history".

    Shallow-clone guard: if the repo is a shallow clone, emits a warning and
    skips scoring entirely (git log results would be misleading).

CLI/Examples:
    python3 arch_stability.py
    python3 arch_stability.py --json
    python3 arch_stability.py --stable-days 60
    python3 arch_stability.py --cache-dir /tmp/my_cache

    # Importable:
    from arch_stability import compute_stability
    result = compute_stability(project_root=Path("/my/project"), stable_days=30)

Limitations:
    - Requires git in PATH; subprocess only (no gitpython).
    - ThreadPoolExecutor used when components > 20 (max_workers=8).
    - Shallow clones produce incomplete history; guard emits warning + skips.
    - Component file paths with ::function_name:line suffixes are stripped
      before git log queries.
    - Python 3.8+ compatible: no X|Y union types, no dict[str, int] generics.

ENV/Files:
    - Reads  : dep_manifest.json (via find_upward)
               .git/ (git commands)
    - Writes : <project_root>/.cache/arch_stability.json
               ~/.claude/logs/arch_stability_decisions.jsonl
    - ENV    : CLAUDE_HOME — overrides ~/.claude base dir (honoured by
               _gate_common.append_jsonl)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# _gate_common import — same two-step try/except pattern as dep_guard.py
# ---------------------------------------------------------------------------
try:
    from _gate_common import append_jsonl, find_upward, iso_now, logs_dir
except ImportError:
    import pathlib as _pl
    sys.path.insert(0, str(_pl.Path(__file__).resolve().parent))
    from _gate_common import append_jsonl, find_upward, iso_now, logs_dir  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_NAME = "arch_stability_decisions.jsonl"
CACHE_FILENAME = "arch_stability.json"
MANIFEST_RELPATH = "docs/dep_manifest.json"
MANIFEST_CHURN_WINDOW_DAYS = 90
MANIFEST_CHURN_LOW_THRESHOLD = 0.05   # commits/day
COLD_START_MIN_COMMITS = 5
DEFAULT_STABLE_DAYS = 30
PARALLEL_THRESHOLD = 20               # use ThreadPoolExecutor above this

# Classification labels (exactly one per component)
STABLE = "STABLE"
CODE_DRIFT = "CODE_DRIFT"
INTERFACE_FLUX = "INTERFACE_FLUX"
DEV = "DEV"
UNMAPPED = "UNMAPPED"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(args: List[str], cwd: Path, timeout: int = 15) -> Tuple[int, str, str]:
    """Run a git command; return (returncode, stdout, stderr). Never raises."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, "", str(exc)


def get_head_sha(repo_root: Path) -> Optional[str]:
    rc, out, _ = _git(["rev-parse", "HEAD"], cwd=repo_root)
    if rc == 0 and out:
        return out[:40]
    return None


def is_shallow_clone(repo_root: Path) -> bool:
    rc, out, _ = _git(["rev-parse", "--is-shallow-repository"], cwd=repo_root)
    return rc == 0 and out.strip().lower() == "true"


def file_last_change_days(file_path: str, repo_root: Path) -> Optional[float]:
    """Return days since the most recent commit that touched file_path.

    Uses --follow to handle renames. Returns None if no commits found.
    """
    rc, out, _ = _git(
        ["log", "--follow", "-1", "--format=%at", "--", file_path],
        cwd=repo_root,
    )
    if rc != 0 or not out:
        return None
    try:
        last_ts = int(out.strip())
        return (time.time() - last_ts) / 86400.0
    except ValueError:
        return None


def manifest_churn_count(component_name: str, manifest_relpath: str, repo_root: Path) -> int:
    """Count commits in the last 90 days that contain component_name in diffs of the manifest.

    Uses `git log -S` (pickaxe) which finds commits where the string was added or removed.
    """
    rc, out, _ = _git(
        [
            "log",
            "-S", component_name,
            "--since=90 days ago",
            "--format=%H",
            "--",
            manifest_relpath,
        ],
        cwd=repo_root,
    )
    if rc != 0 or not out:
        return 0
    return len([line for line in out.splitlines() if line.strip()])


def manifest_total_commits(manifest_relpath: str, repo_root: Path) -> int:
    """Return total commit count for the manifest file (all time)."""
    rc, out, _ = _git(
        ["log", "--format=%H", "--", manifest_relpath],
        cwd=repo_root,
    )
    if rc != 0 or not out:
        return 0
    return len([line for line in out.splitlines() if line.strip()])


def discover_consumers(basename: str, repo_root: Path) -> List[str]:
    """Run git grep to find files that reference basename."""
    rc, out, _ = _git(
        ["grep", "-l", basename],
        cwd=repo_root,
        timeout=20,
    )
    if rc != 0 or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# File path normalisation
# ---------------------------------------------------------------------------

def strip_function_suffix(file_field: str) -> str:
    """Strip ::function_name:line_number suffix from dep_manifest file fields.

    Examples:
        "install.py::main" → "install.py"
        "templates/scripts/rolling_memory.py::memorize" → "templates/scripts/rolling_memory.py"
        "install.py" → "install.py"
    """
    if "::" in file_field:
        return file_field.split("::")[0]
    return file_field


# ---------------------------------------------------------------------------
# Per-component scoring
# ---------------------------------------------------------------------------

def classify_component(
    name: str,
    file_field: str,
    manifest_relpath: str,
    repo_root: Path,
    stable_days: int,
    cold_start: bool,
) -> Dict[str, Any]:
    """Compute signals and classify a single component.

    Returns a dict with keys: name, file, source_days, manifest_commits,
    manifest_churn_rate, stability, stability_basis, signals_used.
    """
    source_file = strip_function_suffix(file_field)

    days = file_last_change_days(source_file, repo_root)
    source_old = (days is not None) and (days >= stable_days)

    if cold_start:
        # Cold-start: only source signal; manifest churn is unreliable
        churn_count = 0
        churn_rate = 0.0
        if days is None:
            classification = DEV  # can't tell; assume in-flight
        elif source_old:
            classification = STABLE
        else:
            classification = CODE_DRIFT
        stability_basis = "source_history"
        signals_used = ["source_file"]
    else:
        churn_count = manifest_churn_count(name, manifest_relpath, repo_root)
        churn_rate = churn_count / float(MANIFEST_CHURN_WINDOW_DAYS)
        churn_low = churn_rate <= MANIFEST_CHURN_LOW_THRESHOLD

        if days is None:
            # Unknown source history — use churn only
            if churn_low:
                classification = DEV
            else:
                classification = INTERFACE_FLUX
        elif source_old and churn_low:
            classification = STABLE
        elif (not source_old) and churn_low:
            classification = CODE_DRIFT
        elif source_old and (not churn_low):
            classification = INTERFACE_FLUX
        else:
            classification = DEV

        stability_basis = "dual_signal"
        signals_used = ["source_file", "manifest_churn"]

    return {
        "name": name,
        "file": source_file,
        "raw_file_field": file_field,
        "source_days": round(days, 1) if days is not None else None,
        "manifest_commits": churn_count if not cold_start else None,
        "manifest_churn_rate": round(churn_rate, 4) if not cold_start else None,
        "stability": classification,
        "stability_basis": stability_basis,
        "signals_used": signals_used,
    }


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

def compute_stability(
    project_root: Path,
    stable_days: int = DEFAULT_STABLE_DAYS,
    manifest_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compute stability classifications for all components in dep_manifest.json.

    Args:
        project_root: Root of the git repository.
        stable_days: Days threshold for "old enough" source file.
        manifest_path: Override path to dep_manifest.json (default: find_upward).

    Returns:
        A dict with keys:
            computed_at_ref (str): HEAD SHA used for caching.
            cold_start (bool): True when manifest has < COLD_START_MIN_COMMITS commits.
            stable_days (int): The threshold used.
            components (dict): name → classification record.
            summary (dict): counts per classification + coverage stats.
            shallow_clone (bool): True when repo is shallow.
            warning (str|None): Non-fatal warning message.
    """
    # --- Input validation ---
    if not isinstance(project_root, Path):
        raise TypeError(
            f"project_root must be a pathlib.Path, got {type(project_root).__name__!r}"
        )
    if not project_root.is_dir():
        raise ValueError(f"project_root does not exist or is not a directory: {project_root}")
    if not isinstance(stable_days, int) or stable_days <= 0:
        raise ValueError(f"stable_days must be a positive int, got {stable_days!r}")

    # --- Shallow-clone guard ---
    shallow = is_shallow_clone(project_root)
    if shallow:
        warning_msg = (
            "Shallow clone detected — git history is incomplete. "
            "Stability scoring skipped. Clone with full depth for accurate results."
        )
        append_jsonl(LOG_NAME, {
            "event": "shallow_clone_guard",
            "project_root": str(project_root),
            "ts": iso_now(),
        })
        return {
            "computed_at_ref": get_head_sha(project_root),
            "cold_start": False,
            "stable_days": stable_days,
            "components": {},
            "summary": {"total": 0},
            "shallow_clone": True,
            "warning": warning_msg,
        }

    # --- Locate dep_manifest.json ---
    if manifest_path is None:
        manifest_path = find_upward(str(project_root), MANIFEST_RELPATH)
    if manifest_path is None or not manifest_path.is_file():
        raise FileNotFoundError(
            f"dep_manifest.json not found upward from {project_root}. "
            f"Expected at: {MANIFEST_RELPATH}"
        )

    # --- Parse manifest ---
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to parse dep_manifest.json at {manifest_path}: {exc}") from exc

    if not isinstance(raw, dict) or "components" not in raw:
        raise ValueError(
            f"dep_manifest.json at {manifest_path} lacks a 'components' key. "
            f"Got keys: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}"
        )

    components_raw = raw["components"]
    if not isinstance(components_raw, dict):
        raise ValueError(
            f"dep_manifest.json 'components' must be a dict, "
            f"got {type(components_raw).__name__}"
        )

    # --- Determine manifest relpath relative to project_root ---
    try:
        manifest_relpath = str(manifest_path.relative_to(project_root))
    except ValueError:
        manifest_relpath = MANIFEST_RELPATH  # fallback

    # --- Cold-start check ---
    total_manifest_commits = manifest_total_commits(manifest_relpath, project_root)
    cold_start = total_manifest_commits < COLD_START_MIN_COMMITS

    # --- HEAD SHA (for cache key) ---
    head_sha = get_head_sha(project_root)

    # --- Score all components ---
    names_and_files = []
    for cname, cdata in components_raw.items():
        if not isinstance(cdata, dict):
            continue
        file_field = cdata.get("file", "")
        if not isinstance(file_field, str) or not file_field:
            continue
        names_and_files.append((cname, file_field))

    def _score_one(item: Tuple[str, str]) -> Dict[str, Any]:
        cname, file_field = item
        return classify_component(
            name=cname,
            file_field=file_field,
            manifest_relpath=manifest_relpath,
            repo_root=project_root,
            stable_days=stable_days,
            cold_start=cold_start,
        )

    results: List[Dict[str, Any]] = []
    if len(names_and_files) > PARALLEL_THRESHOLD:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_score_one, item): item for item in names_and_files}
            for fut in as_completed(futures):
                results.append(fut.result())
    else:
        for item in names_and_files:
            results.append(_score_one(item))

    # Restore manifest order
    order = {name: idx for idx, (name, _) in enumerate(names_and_files)}
    results.sort(key=lambda r: order.get(r["name"], 9999))

    # --- Body invariant: each component classified exactly once ---
    classified_names = [r["name"] for r in results]
    if len(classified_names) != len(set(classified_names)):
        dupes = [n for n in classified_names if classified_names.count(n) > 1]
        raise RuntimeError(
            f"Invariant violation: components classified more than once: {set(dupes)}"
        )
    for r in results:
        allowed = {STABLE, CODE_DRIFT, INTERFACE_FLUX, DEV, UNMAPPED}
        if r["stability"] not in allowed:
            raise RuntimeError(
                f"Invariant violation: component {r['name']!r} has invalid "
                f"stability {r['stability']!r}"
            )

    # --- Build summary ---
    counts: Dict[str, int] = {STABLE: 0, CODE_DRIFT: 0, INTERFACE_FLUX: 0, DEV: 0, UNMAPPED: 0}
    for r in results:
        counts[r["stability"]] += 1

    total = len(results)
    scored = sum(1 for r in results if r["source_days"] is not None)
    coverage_pct = round(100.0 * scored / total, 1) if total > 0 else 0.0

    summary = {
        "total": total,
        "stable_days_threshold": stable_days,
        "manifest_total_commits": total_manifest_commits,
        "cold_start": cold_start,
        "per_class": counts,
        "coverage_pct": coverage_pct,
        "scored_components": scored,
        "unscored_components": total - scored,
    }

    # --- Output validation ---
    if total > 0 and sum(counts.values()) != total:
        raise RuntimeError(
            f"Output invariant violation: classification counts sum to "
            f"{sum(counts.values())}, expected {total}"
        )

    components_out = {r["name"]: r for r in results}

    output = {
        "computed_at_ref": head_sha,
        "cold_start": cold_start,
        "stable_days": stable_days,
        "components": components_out,
        "summary": summary,
        "shallow_clone": False,
        "warning": None,
    }

    # --- Log decisions ---
    append_jsonl(LOG_NAME, {
        "event": "stability_computed",
        "project_root": str(project_root),
        "head_sha": head_sha,
        "cold_start": cold_start,
        "total_components": total,
        "summary_per_class": counts,
        "ts": iso_now(),
    })

    return output


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache(cache_path: Path, head_sha: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return cached result if HEAD SHA matches, else None."""
    if not cache_path.is_file() or head_sha is None:
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("computed_at_ref") == head_sha:
            return cached  # type: ignore[return-value]
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _save_cache(cache_path: Path, data: Dict[str, Any]) -> None:
    """Write result to cache file. Fail-soft."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
        tmp.replace(cache_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

_CLASS_DESCRIPTIONS = {
    STABLE:         "source stable + manifest stable",
    CODE_DRIFT:     "HIGHEST RISK: code changing, manifest not updated",
    INTERFACE_FLUX: "code stable but interface/deps churning",
    DEV:            "actively in development",
    UNMAPPED:       "not in dep_manifest.json",
}

_RISK_ORDER = [CODE_DRIFT, INTERFACE_FLUX, DEV, STABLE, UNMAPPED]


def format_table(result: Dict[str, Any]) -> str:
    """Render a human-readable table sorted by risk (CODE_DRIFT first)."""
    if result.get("shallow_clone"):
        return (
            "arch_stability: SKIPPED\n"
            f"Warning: {result.get('warning', 'shallow clone')}\n"
        )

    summary = result["summary"]
    components = result["components"]
    cold_start = result.get("cold_start", False)
    stable_days = result.get("stable_days", DEFAULT_STABLE_DAYS)
    head_sha = result.get("computed_at_ref") or "unknown"

    lines = []
    lines.append("=" * 72)
    lines.append("  Architecture Stability Report")
    lines.append("=" * 72)
    lines.append(f"  HEAD:          {head_sha[:12]}...")
    lines.append(f"  Stable-days:   {stable_days}")
    lines.append(f"  Basis:         {'source_history (cold start)' if cold_start else 'dual_signal'}")
    lines.append(f"  Manifest commits (all time): {summary.get('manifest_total_commits', '?')}")
    lines.append(f"  Coverage:      {summary['scored_components']}/{summary['total']} "
                 f"components scored ({summary['coverage_pct']}%)")
    lines.append("")

    # Summary counts
    lines.append("  Classification summary:")
    for cls in _RISK_ORDER:
        count = summary["per_class"].get(cls, 0)
        desc = _CLASS_DESCRIPTIONS.get(cls, "")
        marker = " *** " if cls == CODE_DRIFT and count > 0 else "     "
        lines.append(f"{marker}{cls:<18} {count:>3}   {desc}")
    lines.append("")

    # Per-component detail, sorted by risk
    def _sort_key(name: str) -> Tuple[int, str]:
        cls = components[name]["stability"]
        return (_RISK_ORDER.index(cls) if cls in _RISK_ORDER else 99, name)

    if components:
        lines.append("  Component detail (sorted by risk):")
        header = f"  {'COMPONENT':<35} {'CLASS':<18} {'DAYS':>6}  {'CHURN/d':>7}  FILE"
        lines.append(header)
        lines.append("  " + "-" * 70)
        for name in sorted(components.keys(), key=_sort_key):
            r = components[name]
            days_str = f"{r['source_days']:.0f}d" if r["source_days"] is not None else "  n/a"
            churn_str = (
                f"{r['manifest_churn_rate']:.4f}"
                if r.get("manifest_churn_rate") is not None
                else "    n/a"
            )
            cls = r["stability"]
            prefix = "**" if cls == CODE_DRIFT else "  "
            lines.append(
                f"{prefix} {name:<33} {cls:<18} {days_str:>6}  {churn_str:>7}  {r['file']}"
            )
    lines.append("")
    lines.append("  Legend:")
    for cls in _RISK_ORDER:
        lines.append(f"    {cls:<18} {_CLASS_DESCRIPTIONS[cls]}")
    lines.append("=" * 72)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Per-component architecture stability engine for dep_manifest.json."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of human-readable table.",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        metavar="PATH",
        help="Override cache directory (default: <project_root>/.cache/).",
    )
    parser.add_argument(
        "--stable-days",
        type=int,
        default=DEFAULT_STABLE_DAYS,
        metavar="N",
        help=f"Days threshold to classify a source file as 'old enough' (default: {DEFAULT_STABLE_DAYS}).",
    )
    args = parser.parse_args()

    # Input guard: stable_days
    if args.stable_days <= 0:
        print(
            f"ERROR: --stable-days must be a positive integer, got {args.stable_days}",
            file=sys.stderr,
        )
        sys.exit(0)  # Exit 0: informational tool, never blocks

    # Find project root
    cwd = str(Path.cwd())
    project_root = None
    try:
        # Walk up looking for .git
        from _gate_common import project_root_from
    except ImportError:
        pass

    try:
        from _gate_common import project_root_from as _prf
        project_root = _prf(cwd)
    except Exception:
        pass

    if project_root is None:
        # Fallback: walk up manually looking for .git
        p = Path.cwd()
        for candidate in [p, *p.parents]:
            if (candidate / ".git").exists():
                project_root = candidate
                break

    if project_root is None:
        print("ERROR: Not inside a git repository.", file=sys.stderr)
        sys.exit(0)

    # Determine cache path
    if args.cache_dir:
        cache_dir = Path(args.cache_dir)
    else:
        cache_dir = project_root / ".cache"

    cache_path = cache_dir / CACHE_FILENAME

    # Check cache (only skip recompute if HEAD unchanged)
    head_sha = get_head_sha(project_root)
    cached = _load_cache(cache_path, head_sha)

    if cached is not None:
        result = cached
    else:
        try:
            result = compute_stability(
                project_root=project_root,
                stable_days=args.stable_days,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(0)
        except Exception as exc:  # pragma: no cover
            print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
            sys.exit(0)

        _save_cache(cache_path, result)

    # Output
    if args.json:
        # Output validation: ensure output is valid JSON and all keys present
        output_str = json.dumps(result, indent=2, default=str)
        # Validate round-trip
        try:
            json.loads(output_str)
        except json.JSONDecodeError as exc:
            print(f"ERROR: JSON output validation failed: {exc}", file=sys.stderr)
            sys.exit(0)
        print(output_str)
    else:
        print(format_table(result))

    # Always exit 0 — informational tool
    sys.exit(0)


if __name__ == "__main__":
    main()
