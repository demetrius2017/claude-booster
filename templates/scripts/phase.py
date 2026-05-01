#!/usr/bin/env python3
"""
phase.py — project-scoped phase state machine for Lead-Orchestrator workflow.

Contract:
  phase.py           → print current phase (alias of get)
  phase.py get       → print current phase; default RECON if unset
  phase.py set NAME  → set phase, log transition, print prev→new
  phase.py list      → list valid phases with short rule

Storage:
  <project_root>/.claude/.phase                — one phase name + newline
  <project_root>/.claude/phase_transitions.log — append-only audit

Project root = first ancestor of CWD containing .git/ or .claude/, else CWD.

Exit codes: 0 OK, 2 bad usage / invalid phase.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

VALID = ["RECON", "PLAN", "IMPLEMENT", "AUDIT", "VERIFY", "MERGE"]
DEFAULT = "RECON"

RULES = {
    "RECON":     "read-only exploration (Read/Grep/Glob/WebSearch); no Edit/Write",
    "PLAN":      "design + TaskCreate + consilium if uncertainty >30%; no code edits",
    "IMPLEMENT": "Edit/Write allowed; run tests after each change",
    "AUDIT":     "code review + PAL second opinion; no new code",
    "VERIFY":    "real curl / pytest / Chrome DevTools — collect evidence",
    "MERGE":     "git push after user acceptance; post-merge curl/console check",
}


def _project_root() -> Path:
    try:
        cwd = Path(os.getcwd())
    except (FileNotFoundError, OSError):
        return Path.home()
    for p in [cwd, *cwd.parents]:
        if (p / ".git").exists() or (p / ".claude").exists():
            return p
    return cwd


def _phase_file(root: Path) -> Path:
    return root / ".claude" / ".phase"


def get_phase() -> str:
    f = _phase_file(_project_root())
    if not f.exists():
        return DEFAULT
    try:
        v = f.read_text(encoding="utf-8").strip().upper()
        return v if v in VALID else DEFAULT
    except OSError:
        return DEFAULT


def set_phase(name: str) -> int:
    name = name.strip().upper()
    if name not in VALID:
        print(f"error: invalid phase '{name}'. valid: {VALID}", file=sys.stderr)
        return 2
    root = _project_root()
    f = _phase_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    prev = get_phase()
    f.write_text(name + "\n", encoding="utf-8")
    log = f.parent / "phase_transitions.log"
    try:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.utcnow().isoformat()}Z  {prev} -> {name}  (root={root})\n")
    except OSError:
        pass
    print(f"{prev} -> {name}")
    return 0


def list_phases() -> int:
    for name in VALID:
        print(f"  {name:<9} {RULES[name]}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] == "get":
        print(get_phase())
        return 0
    cmd = argv[1]
    if cmd == "set":
        if len(argv) < 3:
            print("usage: phase.py set <PHASE>", file=sys.stderr)
            return 2
        return set_phase(argv[2])
    if cmd == "list":
        return list_phases()
    print("usage: phase.py [get|set <PHASE>|list]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
