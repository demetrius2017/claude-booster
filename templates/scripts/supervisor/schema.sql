-- Claude Booster Supervisor Agent v1.2.0 — SQLite schema delta.
--
-- Applied idempotently to ~/.claude/rolling_memory.db alongside the
-- existing agent_memory / consolidation / FTS tables. When this module
-- is wired into rolling_memory.py SCHEMA_VERSION will bump; until
-- then the CREATE IF NOT EXISTS statements below are safe to execute
-- on demand from supervisor.py.
--
-- Consilium §5/Q4: SQLite chosen over in-memory / JSON because the
-- §3.5 OAuth quota fact elevates state from convenience to governance
-- (supervisor must survive crashes and reconstruct circuit state on
-- restart). WAL already set project-wide; no pragma here.

CREATE TABLE IF NOT EXISTS supervisor_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    ts            TEXT NOT NULL,
    tool          TEXT NOT NULL,
    args_digest   TEXT NOT NULL,
    decision      TEXT NOT NULL CHECK (decision IN ('approve','escalate','deny')),
    tier          INTEGER,
    rationale     TEXT,
    approved_by   TEXT CHECK (approved_by IN ('regex','haiku','dmitry')),
    outcome       TEXT
);

CREATE INDEX IF NOT EXISTS idx_sup_dec_session_ts
    ON supervisor_decisions (session_id, ts);

CREATE INDEX IF NOT EXISTS idx_sup_dec_args_loop
    ON supervisor_decisions (args_digest, ts);


CREATE TABLE IF NOT EXISTS supervisor_quota (
    session_id         TEXT PRIMARY KEY,
    started_at         TEXT NOT NULL,
    window_end         TEXT NOT NULL,
    supervisor_tokens  INTEGER NOT NULL DEFAULT 0,
    worker_tokens      INTEGER NOT NULL DEFAULT 0,
    circuit_state      TEXT NOT NULL DEFAULT 'closed'
        CHECK (circuit_state IN ('closed','half_open','open')),
    updated_at         TEXT NOT NULL
);
