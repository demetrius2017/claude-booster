# Agent Lifecycle (compact)

> Built-in Agent Teams handles session states, heartbeat, auto-cleanup.
> This file: project-specific additions only.

## Graceful Stop (mandatory sequence)

1. Commit current changes (even partial)
2. Write handoff to `state/handoffs/{role}_{task_id}_{timestamp}.md`
3. Update task status
4. Push branch

Skipping handoff = data loss for next session.

## Resume

1. Read last handoff for this role/task
2. Read task status + active blockers + contracts
3. Continue from where handoff left off
4. NEVER rely on in-memory context from previous session
