---
description: "Pipeline phases and decision format. Loaded for multi-file tasks, consilium, audit, or when orchestrating agents."
---

# Pipeline (tasks spanning 5+ files or multiple domains)

**You are the Lead. Orchestrate agents, do not write code directly.**

| Phase | Action | Mechanism |
|-------|--------|-----------|
| **PLAN** | Agent roles, scope, deliverables. No spawning before approval. | `EnterPlanMode` → plan → user approval → `ExitPlanMode` |
| **IMPLEMENT** | Spawn agents by domain. Lead resolves dependencies. | `TaskCreate` for tracking each agent |
| **VERIFY** | Real commands/curl/scripts. After deploy — curl API on prod. **Frontend: Chrome DevTools pipeline** (console + network + screenshot). Collect EVIDENCE. | `TaskUpdate` pass/fail with evidence |
| **AUDIT** | Review all code: correctness, security, performance. **Must** request second opinion from GPT via PAL MCP. | `/simplify` for <5 files. Agents for ≥5. `mcp__pal__second_opinion` or `mcp__pal__codereview` for external validation. Explicit PASS/FAIL. |
| **DELIVER** | Only when all tests + audits PASS. | `TaskUpdate` → completed |

**[CRITICAL] Phase failed — fix and re-run from that phase. Status "completed" — ONLY after all phases pass. Otherwise — "in progress — requires verification".**

# Decision Format
- "Advocate FOR / Advocate AGAINST"
- SWOT / Decision Scoring when multiple paths exist
- 1-2 line "Conclusion:" with recommendation
