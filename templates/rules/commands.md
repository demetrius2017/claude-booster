---
description: "Git config (Vercel deploy author), Python docstring policy, agent teams & worktree safety. Always loaded."
---

# [CRITICAL] Git Configuration
`user.name={{GIT_AUTHOR_NAME}}`, `email={{GIT_AUTHOR_EMAIL}}`
Vercel only deploys commits from this author. Without this config, deploy will fail.

# Docstring Policy (Python)
Every Python file — up-to-date module docstring: Purpose, Contract (inputs/outputs), CLI/Examples, Limitations, ENV/Files.

# Agent Teams & Worktree
Rules: `~/.claude/agents/protocol.md` (ownership, gates, state).
Frontend tasks: UI acceptance from the user before merge. Use `/frontend-design` for UI tasks.

**Worktree Safety:** stay in worktree dir, integrate only via `git merge` / `gh pr create` / `git cherry-pick`. Before commit: `pwd` + `git branch --show-current`. Full: `~/.claude/agents/worktree_rules.md`.
