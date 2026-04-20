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
- supervisor.py          — main orchestration loop + CLI (run / status / decisions)
- prompts/supervisor_v1.md — Haiku escalation gatekeeper system prompt
- schema.sql             — SQLite DDL
- tests/                 — 90 tests (policy 16 + quota 9 + redteam 5 + adapter 16 + detector 20 + persistence 9 + supervisor integration 15)

Not yet implemented:
- HaikuEscalator with real Anthropic API wiring (caller supplies credentials; Protocol in supervisor.py)
- End-to-end red-team against the real claude-agent-sdk worker binary (gates BOOSTER_VERSION bump to 1.2.0)
- Multi-worker session pool (Session 5+)
"""

__version__ = "0.3.0-supervisor-cli"
