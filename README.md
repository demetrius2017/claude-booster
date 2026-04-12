# Claude Booster

Advanced memory and context management system for Claude Code. Transforms the default flat memory into a structured, scoped, and self-compounding knowledge architecture.

## What This Project Does

Optimizes how Claude Code loads, stores, retrieves, and preserves knowledge across sessions and projects.

### Core Principles

1. **Methods are global, knowledge is per-project** — shared workflows (`~/.claude/rules/`) apply everywhere; domain-specific learnings stay in project memory dirs
2. **Institutional lessons are permanent** — rules extracted from consiliums/audits go to `rules/institutional.md` with `preserve: true`
3. **Load less, find better** — scoped rules via `paths:` frontmatter, deferred MCP schemas, YAML metadata for structured search

## Architecture

```
~/.claude/
├── rules/                          # Layer 1: Global methods (always/conditionally loaded)
│   ├── core.md                     # Anti-loop, work principles, prohibited (always)
│   ├── tool-strategy.md            # PAL, Browser MCP, Context7 (always)
│   ├── pipeline.md                 # PLAN/IMPLEMENT/VERIFY/AUDIT phases (description-gated)
│   ├── commands.md                 # /start, /deploy, /handover, /consilium (description-gated)
│   ├── deploy.md                   # Vercel + Docker deploy procedures (description-gated)
│   ├── frontend-debug.md           # Chrome DevTools pipeline (paths: *.tsx,*.jsx,*.css)
│   └── institutional.md            # 35 hard-won rules from consiliums/audits (always)
├── CLAUDE.md                       # Minimal pointer to rules/ (8 lines)
├── scripts/
│   ├── rolling_memory.py           # SQLite + FTS5 memory engine (648 lines)
│   ├── memory_session_start.py     # Context injection hook (4000 token budget)
│   ├── memory_session_end.py       # 3-question smart extraction + error lessons
│   ├── memory_post_tool.py         # Batch error/commit capture (<5ms)
│   └── add_frontmatter.py          # YAML frontmatter migration tool
└── projects/*/memory/              # Layer 2: Per-project knowledge (scoped)
    ├── MEMORY.md                   # Index
    ├── feedback_*.md               # Lessons learned
    ├── project_*.md                # Architecture, state
    └── reference_*.md              # External pointers

~/Projects/*/reports/               # Layer 3: Consilium/audit reports (git-tracked)
    ├── consilium_*.md              # preserve: true, scope: global
    ├── audit_*.md                  # preserve: true, scope: global
    └── handover_*.md               # Session handoffs
```

## Status

### Phase 1: DONE (2026-04-09)

| Step | Description | Status |
|------|-------------|--------|
| 1a | Split CLAUDE.md → 7 rules/ files | DONE |
| 1b | Defer MCP tool schemas | DONE (already implemented) |
| 1c | YAML frontmatter on 105 files (memory + reports) | DONE |
| 1d | 3-question session_end extraction | DONE |
| 1e | institutional.md — 35 rules from 11 reports | DONE |

### Phase 2: IN PROGRESS

| Step | Description | Effort | Status |
|------|-------------|--------|--------|
| 2a | FTS5 scope column (schema v2→v3) + report indexing (20 consilium/audit rows) | ~4h | **DONE 2026-04-12** |
| 2b | Error pattern taxonomy (manual, now feasible on 63-row corpus) | ~2h | PENDING |
| 2c | Consolidation review (`preserve: true` exempt for consilium/audit rows) | ~2h | PENDING |
| 2d | Cross-project search in `/start` command (replaces Glob+Read with `search(scope=...)`) | ~2h | PENDING (unblocked by 2a) |

## Key Findings

- `paths:` works for rule file filtering; `globs:` does NOT
- `description:` loads rules but does not filter (acceptable)
- 2605 handover reports exist across projects — too many for bulk frontmatter
- Backup: `~/claude_backup_20260409_123020.tar.gz` (440MB)

## Artifacts

- `~/hackathon_memory_system.html` — 15-slide presentation (4 agents, 3 critics, synthesis)
- `~/.claude/plans/refactored-brewing-hellman.md` — implementation plan v2 (post-audit)
- `~/.claude/scripts/add_frontmatter.py` — YAML frontmatter migration tool
