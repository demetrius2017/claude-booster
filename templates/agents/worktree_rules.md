# Worktree Rules (compact)

> Built-in `--worktree` / `isolation: worktree` handles creation, branch, cleanup.
> This file: safety rules that prevent the #1 class of multi-agent bugs.

## Iron Rule: 1 task = 1 branch = 1 worktree

## Safety Rules

1. **NEVER `cd` to main repo** — stay in worktree, commit there
2. **NEVER `cp` between worktree and main** — use `git merge` or PR
3. **Before every git commit**: `pwd` + `git branch --show-current` to verify
4. **Integration ONLY through git**: merge, PR, or cherry-pick. Never file copy.
5. **Read main's file**: `git show main:path/to/file` (don't leave worktree)

## Branch Naming

Pattern: `feat/{role}-{task_id}` (e.g. `feat/backend-T-001`)
