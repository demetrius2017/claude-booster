---
description: "Track and resolve session debts. /debt list shows inventory, /debt work picks highest priority, /debt review formats for handover."
argument-hint: "[list|add|work|resolve|review] [args]"
---

Parse `$ARGUMENTS` and route to the matching mode below. No argument or `list` → run **LIST** mode. Otherwise match the first word.

---

## MODE: list (default)

Produce a debt inventory for the current session. Do all four scans, then merge and deduplicate before printing.

**Scan 1 — Task tool:**
Run `TaskList`. For every task whose status is NOT `completed`, record: task id, title, status (`in_progress` / `blocked` / `not_started`).

**Scan 2 — Session transcript patterns:**
Run:
```bash
python3 ~/.claude/scripts/session_context.py --tools-only --grep "TODO\|FIXME\|HACK\|next session\|следующая сессия\|deferred\|Tier 2\|Tier 3" --no-thinking 2>/dev/null | tail -60
```
For each hit, extract the surrounding sentence and classify:
- `TODO` / `FIXME` / `HACK` in an Edit or Write call → likely open code debt.
- "next session" / "следующая сессия" / "deferred" in an assistant message → explicitly deferred work.
- "Tier 2" / "Tier 3" → backlog items that were consciously postponed.

Also run:
```bash
python3 ~/.claude/scripts/session_context.py --tools-only --grep "test.*fail\|exit code [^0]\|FAILED\|ERROR" --no-thinking 2>/dev/null | tail -40
```
Any failing test that has no subsequent passing run = HIGH-priority debt.

**Scan 3 — Git state:**
```bash
git -C "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" status --short 2>/dev/null
```
Uncommitted changes or untracked files in non-allowlisted paths (not `reports/`, `docs/`, `*.md`) → HIGH-priority debt.

**Scan 4 — Manual debts file:**
Read `.claude/.session_debts.json` (if it exists). Include every item with `status: "open"`.

**Merge and classify:**

| Priority | Condition |
|----------|-----------|
| HIGH | Failing tests not re-run; uncommitted non-doc changes; blocked tasks |
| MED | In-progress tasks without evidence of completion; deferred Tier 1 items; open `.session_debts.json` entries |
| LOW | Tier 2/3 items from transcript; nice-to-haves; documentation gaps |

**Output format:**
```
## Session Debt Inventory

[1] HIGH  Uncommitted changes in src/foo.py (git status)
[2] HIGH  Task "fix rebuilder edge case" is blocked (task-id: abc123)
[3] MED   "deferred to next session: dep_guard.py implementation" (transcript)
[4] LOW   Tier 3 — ADR adoption not yet started (transcript)

Total: 4 items (2 HIGH, 1 MED, 1 LOW)
```

If the inventory is empty: print `No open debts — session clean.` and stop.

---

## MODE: add

Parse: `add <description>`. If no description, print usage and exit.

Read `.claude/.session_debts.json` (return `[]` if file absent or unreadable).

Append a new entry:
```json
{"id": <next integer>, "description": "<description>", "priority": "MED", "added_at": "<ISO-8601 timestamp>", "status": "open"}
```

Write back to `.claude/.session_debts.json`. Print:
```
Added debt #<id>: "<description>" [priority: MED]
To change priority: edit .claude/.session_debts.json directly.
```

---

## MODE: work (no number)

1. Run LIST mode internally (do not reprint the full output — just build the inventory).
2. Select the first HIGH-priority item. If no HIGH items, select the first MED-priority item. If only LOW, select the first LOW item. If none, print `No open debts to work on.` and stop.
3. Print: `Working on debt [<N>] <priority>: <description>`
4. Create a task via `TaskCreate` with title = `debt[<N>]: <description>`.
5. Begin implementation. For code changes (Edit/Write to ≥2 files, or any behavior change), follow the paired Worker+Verifier pattern from `paired-verification.md`. For trivial changes (<2 files, formatting only, typo), proceed directly.

---

## MODE: work N

Parse `work <N>` where N is an integer.

Run LIST mode internally to get the current inventory. Find item `[N]`. If not found, print `Debt item [N] not found. Run /debt list to see current inventory.` and stop.

Print: `Working on debt [<N>] <priority>: <description>`

Create a task via `TaskCreate` with title = `debt[<N>]: <description>`.

Begin implementation per the same Worker+Verifier rules as MODE: work above.

---

## MODE: resolve N

Parse `resolve <N>` where N is an integer.

**Check TaskList:** if there is a task matching `debt[N]:`, update it to `completed` via `TaskUpdate`.

**Check `.session_debts.json`:** read the file. If an item with matching `id` exists, set its `status` to `"resolved"` and add `"resolved_at": "<ISO-8601 timestamp>"`. Write back.

Print: `Resolved debt [<N>]. Item marked closed.`

If item N was not found in either source, print: `Debt item [N] not found. It may have been from a transcript scan (not stored). Run /debt list to verify current state.`

---

## MODE: review

Generate a formatted debt summary suitable for pasting into a handover report.

1. Run LIST mode internally to build the full inventory.
2. Format output as:

```markdown
## Outstanding Debts

| # | Priority | Description | Status |
|---|----------|-------------|--------|
| 1 | HIGH | Tests not passing for require_task.py edge case | open |
| 2 | MED | dep_guard.py not yet implemented (Tier 2) | deferred |
| 3 | LOW | ADR practice not yet adopted in any project | backlog |

Carried from: <session JSONL path or "current session">
```

3. If the inventory is empty, output:
```markdown
## Outstanding Debts

No outstanding debts — session clean.
```

4. Print the block to stdout. Do NOT write it to a file — the caller (handover or user) will copy-paste it.

---

## Integration note for /handover

When running `/handover`, invoke `/debt review` before finalising the report and insert its output as the `## Outstanding Debts` section. Place it after `## Problems / Solutions` and before `## Required reading`. If the section is already present in a prior handover template, replace it with the fresh output.
