---
description: "Core rules: anti-loop, work principles, prohibited actions. Always loaded."
---

# [CRITICAL] Anti-Loop & Debugging
- Approach failed twice — STOP, explain, ask for direction. Do not retry with minor variations.
- Do not re-read the same file >2 times per task.
- Diagnose BEFORE fixing: root cause with evidence first, then code changes.
- Frontend bug: check API first (curl), then frontend via Chrome DevTools (see Frontend Debug Pipeline section).
- **Performance complaint ("slow", "laggy", "takes forever"):** HAR/network data first (Step 0), then DevTools metrics. Do NOT poke UI like a user — look under the hood.
- After deploy: curl API endpoints on prod — confirm they work.
- Config files (docker-compose, Dockerfile, YAML) — Edit tool only, not sed.

# [CRITICAL] Shell hygiene — zsh nomatch + parallel-cancel cascade
The default Claude Code shell on macOS is zsh, which has `nomatch` enabled by default: a glob with no match (`ls roadmap.*` when no roadmap exists) aborts the command at parse time **before** redirects apply, so `2>/dev/null` does not silence it. **And** when one tool call in a parallel-tool-call block exits non-zero, the harness cancels every sibling call in the same block — one stray glob can void 5 unrelated probes.
- **Use `Read` for "does file X exist" probes**, not Bash globs. `Read missing.md` returns a clean error; the harness does not cancel siblings on a Read miss.
- If you must glob in Bash, use `(N)` qualifier on zsh: `ls /path/roadmap.*(N) 2>/dev/null` — `(N)` makes a non-matching glob expand to nothing instead of erroring.
- Or list candidates explicitly: `ls roadmap.html roadmap.md 2>/dev/null; true` (no glob, exit code suppressed).
- **Never group fragile probes with critical telemetry in one parallel block** (`/start` canary, `check_review_ages`, `telemetry_agent_health`). Run telemetry in a separate, no-glob block so a typo in an unrelated `ls` cannot kill it.

# Work Principles
- **[CRITICAL] 51% Rule — do not ask clarifying questions you can answer yourself.**
  If you estimate ≥51% confidence in the answer from available context (code, memory, prior session, reports, obvious defaults), **act on your best guess** and state the assumption in one line ("Assuming X because Y — correct if wrong"). Do NOT interrupt with "which option do you want?" / "should I proceed?" / "did you mean X or Y?" when the evidence already points to an answer.
  - **Applies to:** routing/clarification questions, path/file guessing, interpretation of short commands, choosing between equivalent approaches, inferring user intent from context.
  - **Does NOT apply to:** genuinely destructive / irreversible / externally-visible actions that can't be un-done by another commit — `rm -rf` of user data, `git push --force` to main/master, DB **schema** migrations (`ALTER`/`DROP`), sending external messages (Slack/email/Telegram/Gmail), deleting branches/PRs, hard-deleting records. Those still need explicit confirmation.
  - **DOES apply to** (i.e. do NOT ask, just do) — code edits, apply+push of a fix to prod/beta, `pip install` / `npm install`, container restart, config tweaks, running migrations that are already written+reviewed, `curl` to your own prod for verification. The user pre-approves the **entire research → recommendation → apply → verify → commit → push** chain at the moment they gave the task. "Apply patch now?" / "Proceed with fix?" / "Deploy?" questions are **forbidden** — if the user wanted a dry-run they would have said "dry-run". Default is to execute.
  - **Ambiguity about risk ≠ ambiguity about intent.** If an action is clearly what the user meant, do it; don't second-guess the **scope** they already chose.
  - **Why:** the user's primary frustration is Claude stalling on questions that any attentive reader of the context could answer. Asking burns a round-trip and signals laziness, not diligence.
  - **Fail-safe:** if you acted on a guess and it was wrong, the user will course-correct — that is cheaper than blocking him on every decision.
- **[CRITICAL] Think Two Steps Ahead — Pre-Edit Impact Analysis:**
  Before ANY edit, answer three questions (mentally, not in output unless high-risk):
  1. **What depends on this?** — callers, imports, consumers, downstream services, DB schema, API contracts, CI/CD, deploy configs.
  2. **What breaks if this is wrong?** — data loss, downtime, broken deploys, auth failures, race conditions, state corruption.
  3. **Is this reversible?** — can I `git revert` cleanly, or does this touch migrations/infra/external state?

  **DO NOT start editing if:**
  - You haven't traced all callers/consumers of the code you're changing (Grep first).
  - The change touches a shared contract (API schema, DB migration, env vars, Docker config) and you haven't mapped all dependents.
  - You're unsure about the interaction between 2+ systems (e.g., frontend + API + DB + cache).
  - The change is irreversible (migration, data deletion, infra config) and there's no rollback plan.

  **[CRITICAL] Auto-Consilium Trigger — launch WITHOUT asking the user when risk is HIGH:**
  Risk is HIGH when the change hits **2+ of these**:
  - Production data or DB schema (migrations, seeds, data transforms)
  - Auth/security layer (tokens, permissions, encryption, CORS, secrets)
  - Infrastructure (Docker, nginx, DNS, CI/CD pipelines, deploy scripts)
  - Multi-service boundary (API contract change that affects 2+ services)
  - Financial/billing logic (payments, subscriptions, broker API calls)
  - Irreversible external side effects (emails sent, orders placed, records deleted)

  When triggered: run `consilium` (3-5 agents + GPT via PAL) focused on the specific change. Present synthesis to the user BEFORE editing. One-line changes that are obviously safe (typo fix, log message) are exempt.
- **[CRITICAL] Recon before code:** BEFORE writing a new function/method/utility — Grep/Glob the codebase for existing implementations. Search by keywords, method names, patterns. Duplication = bug. Found an analogue → use/extend it, do NOT rewrite from scratch.
- System logs ≠ user decisions.
- File >500 lines — split into modules.
- Do NOT generate reports unless the user explicitly requests — save tokens.

# Prohibited
- Demo versions, simplified files, stubs instead of real implementations.
