"""Claude Booster Supervisor Agent v1.2.0 — skeleton.

Consilium 2026-04-20 decisions (see reports/consilium_2026-04-20_supervisor_architecture.md):
- Q1 Path A (headless subprocess) + thin transport-adapter boundary.
- Q2 Hard baseline + Tier 0/1/2 profile system.
- Q3 Adaptive silence timeout backbone + state-machine + text-heuristic accelerator.
- Q4 SQLite-backed state (supervisor_decisions + supervisor_quota tables in rolling_memory.db).

Module layout:
- policy.py  — Tier 0/1/2 policy engine, deny-list mirror, git-scrub + curl hardening
- quota.py   — admission control, 15% supervisor reserve, closed/half_open/open states
- runtime.py — transport-agnostic WorkerRuntime Protocol (Path A / future MCP)
- schema.sql — SQLite DDL for supervisor_decisions + supervisor_quota tables
- tests/     — unit + red-team scenarios (5 ship-blocker tests per consilium §7 R2)

Not yet implemented (next sessions):
- detector.py      — adaptive silence timeout + state machine + text accelerator
- stream_json_adapter.py — Path A implementation of WorkerRuntime
- supervisor.py    — main entry point / CLI
- prompts/supervisor_v1.md — Haiku supervisor system prompt
"""

__version__ = "0.1.0-skeleton"
