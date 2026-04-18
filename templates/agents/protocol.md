# Agent Teams Protocol v2 (compact)

> Built-in Agent Teams handles: task lifecycle, SendMessage, worktree isolation, session registry.
> This file contains ONLY project-specific rules not covered by built-in features.

## Roles & Models

| Role | Model | Use for |
|------|-------|---------|
| lead | opus | Coordination, user communication, architecture |
| backend | sonnet/opus | API, DB, services |
| frontend | sonnet/opus | React, CSS, UI components |
| devops | sonnet | Docker, CI/CD, infra |
| research | haiku/sonnet | Codebase exploration, docs |

## UI Design Gate (BEFORE coding)

Tasks with UI require HTML mockup approval before implementation:
1. Agent creates `state/designs/{task_id}_mockup.html` — self-contained, all screens
2. Lead presents to user for review
3. Approved → agent starts coding. Rejected → revise mockup.
- Static HTML + inline CSS, no frameworks
- All states: empty, loaded, error, mobile + desktop
- Realistic data, no placeholders

## UI Acceptance Gate (AFTER coding)

Frontend tasks need user approval on staging before merge to main.
Rejection → new task with feedback linked to original.

## Ownership & Contracts

- Agent edits ONLY files in its ownership zone (`.claude/agents/ownership.json`)
- API/schema changes → publish contract to `state/contracts/`
- Consumer reads contract, never modifies — creates blocker if needs change

## Readiness Checklist

Before starting, agent verifies:
- [ ] Dependencies resolved (depends_on tasks completed)
- [ ] Contracts available (if consuming API)
- [ ] No active blockers
- [ ] Ownership confirmed (files within zone)

If unchecked → write blocker, notify lead. Do NOT start.

## Lead Rules

- Lead is ONLY agent that talks to user
- MUST stop for: design review, UI acceptance, architecture decisions, blockers needing credentials
- MUST NOT stop for: routine task completion, dependency resolution, roadmap updates
- Lookahead before spawning: scan unblocked tasks, batch independent ones in ONE message

## Roadmap

Every project has `roadmap.md` or `roadmap.html` at root.
Statuses: TODO → DESIGN → IN PROGRESS → REVIEW → DONE.
Lead reads at `!start`, updates at `!handover`.

## Anti-patterns

- Agent passes knowledge through chat (use state/ files)
- Agent edits outside ownership zone
- Two agents work on same file
- Lead spawns agent for <5 min task
- Lead waits for user when it can decide autonomously
