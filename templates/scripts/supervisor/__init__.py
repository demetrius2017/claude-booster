"""Claude Booster Supervisor Agent v1.2.0 — skeleton.

Consilium 2026-04-20 decisions (see reports/consilium_2026-04-20_supervisor_architecture.md):
- Q1 Path A (headless subprocess) + thin transport-adapter boundary.
- Q2 Hard baseline + Tier 0/1/2 profile system.
- Q3 Adaptive silence timeout backbone + state-machine + text-heuristic accelerator.
- Q4 SQLite-backed state (supervisor_decisions + supervisor_quota tables in rolling_memory.db).

Module layout:
- policy.py              — Tier 0/1/2 policy engine, deny-list mirror, git-scrub + curl hardening
- quota.py               — admission control, 15% supervisor reserve, closed/half_open/open states
- runtime.py             — transport-agnostic WorkerRuntime Protocol (Path A / future MCP)
- stream_json_adapter.py — Path A StreamJsonRuntime (subprocess + stream-json parser)
- detector.py            — adaptive silence timeout + FSM + text-accelerator heuristic
- persistence.py         — sqlite3 writers for supervisor_decisions / supervisor_quota
- schema.sql             — SQLite DDL
- tests/                 — unit + red-team (71 tests total; 5 ship-blockers per consilium §7 R2)

Not yet implemented:
- supervisor.py          — main entry point / CLI (Session 4)
- prompts/supervisor_v1.md — Haiku supervisor system prompt (Session 4)
- end-to-end red-team against real claude-agent-sdk worker (Session 4, gates BOOSTER_VERSION bump)
"""

__version__ = "0.2.0-runtime"
