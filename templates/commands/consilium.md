---
description: "Run consilium (multi-agent debate) or audit. RECON first, spawn 3-5 bio-specific agents + GPT via PAL MCP, synthesize, save report."
argument-hint: <topic for consilium/audit>
---

1. **[CRITICAL] RECON before opinions — verify current state against code, not memory:**
   - Spawn Explore agents to read actual code/configs relevant to the topic (Grep for key functions, Read configs, check deploy state)
   - Cross-reference findings with reports/memory — flag discrepancies ("report says X, code shows Y")
   - Build a **Verified Facts Brief**: what exists now, what works, what doesn't — with file paths and evidence
   - Present brief to the user before proceeding. If facts contradict the premise — reframe the question
   - **Never brief consilium agents from reports alone. Reports decay. Code is truth.**
2. Spawn 3-5 agents with different Bios (architect, security, product, devops, data engineer — task-specific). **Each agent receives the Verified Facts Brief, not raw report excerpts.**
3. Each independently: analysis, KPIs, decision
4. **[MANDATORY] GPT as external expert:** use PAL MCP for independent opinion:
   - `mcp__pal__ask` — request GPT analysis/opinion on a specific question
   - `mcp__pal__thinkdeep` — deep GPT reasoning on architectural decisions
   - `mcp__pal__consensus` — Claude vs GPT debate for controversial decisions
   - `mcp__pal__second_opinion` — GPT second opinion on a finished Claude solution
   - `mcp__pal__codereview` — code review via GPT
5. Lead: synthesis + table "agent / position / key insight / KPI" (including GPT agent)
6. **[CRITICAL] Save results to file:**
   - Consilium → `reports/consilium_YYYY-MM-DD_<topic>.md`
   - Audit → `reports/audit_YYYY-MM-DD_<topic>.md`
   - Format: title, task context, agent positions (table), decision made, rejected alternatives with reasons, risks, implementation recommendations.
   - Git add + commit. These reports are the project's knowledge base, read during `start`.
