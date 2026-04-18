---
description: "Hard-won institutional rules from consiliums, audits, and hackathons. Permanent knowledge — never auto-prune."
---

# Institutional Knowledge

> Actionable rules extracted from consilium/audit reports and hackathons.
> Each rule has a source reference. Update this file when new consiliums/audits produce actionable lessons.

## Infrastructure / Networking

- **CORS origin for upstream Gateways must be `*`**, not a specific domain, when the gateway serves JS-heavy SSO flows. Restrictive CORS blocks SSO JavaScript and surfaces as misleading "Invalid credentials" errors. *(error lesson)*
- **Alpine Linux: use `127.0.0.1` explicitly**, not `localhost`. `localhost` resolves to IPv6 `::1` which fails for IPv4-only services. *(error lesson)*
- **Docker `--cap-drop=ALL` breaks wget/curl healthchecks** inside containers. Remove cap-drop or add specific capabilities. *(error lesson)*

## Deployment / CI/CD

- **Vercel only deploys commits from the configured author** ({{GIT_AUTHOR_NAME}}). Without git config `user.name={{GIT_AUTHOR_NAME}}, user.email={{GIT_AUTHOR_EMAIL}}`, deploys silently fail. Set globally with `git config --global user.name/email`.
- **Vercel env vars: use `printf` not `echo`** for multiline values. Verify vars exist after adding them.
- **Always check `next build` before pushing** frontend changes to Vercel. Broken builds waste deploy slots.
- **Vercel Edge Cache can serve stale data.** After data-affecting changes, set `Cache-Control: no-cache, no-store, must-revalidate` on affected API routes.

## API / Data Integrity

- **Different API default periods cause data discrepancy.** Local dev vs production may use different default `days` parameters (7 vs 30). Always explicitly set period parameters, never rely on defaults.
- **WebSocket reconnect must have reset mechanism.** Hard limit (e.g. MAX_RECONNECTS=5) without a timer/visibility reset causes permanent WS death. Add a `visibilitychange` listener to reset the counter.
- **useEffect cleanup must clear ALL timers.** Missing `clearTimeout` in cleanup causes memory leaks and zombie reconnection attempts after component unmount.
- **Don't recreate WebSocket on filter change.** If the WS doesn't depend on the filter (symbol/period), separate WS lifecycle from the data-fetching useEffect.
- **Progressive rendering over blocking loading.** Never wait for ALL promises before showing UI. Show sections as data arrives (SWR pattern).

## Security / Auth

- **Validate API keys for length and non-empty** before saving. Minimum length checks prevent accidental partial-paste. Log warnings for all validation failures.
- **Never store credentials in handover/report files** in shared repos. Audit repos with VPN UUIDs, passwords, API keys for privacy level.

## Nginx / Proxy

- **Always enable HTTP/2** on nginx reverse proxies. Without `http2 on;` in the listen directive, the browser is limited to 6 parallel HTTP/1.1 connections — catastrophic for SPAs with 40+ assets.
- **`Connection 'upgrade'` on all requests kills keepalive.** Use `map $http_upgrade` for a conditional Connection header — `upgrade` only for WebSocket, empty for regular requests.
- **Cache immutable static assets at proxy level.** `/_next/static/` should be cached for 30 days in the proxy (tmpfs). Eliminates TLS handshake to upstream for repeat visits.

## PostgreSQL — VACUUM / dead tuples

- **Long-running transactions block autovacuum even when it runs.** Vacuum can only delete tuples older than the `xmin` horizon. `idle in transaction` or forgotten SELECTs hold an old xmin → vacuum sees "dead but not yet removable". Symptom: `autovacuum_count` grows, but dead tuples grow too. Fix: `pg_terminate_backend()` on zombie transactions.
- **Regular CRON cleanup jobs (weekly/monthly mass UPDATEs) create dead-tuple spikes.** This is NOT a reason to page — implement anti-flap: (a) suppress if `autovacuum_count` is growing, (b) suppress if dead tuples trend down, (c) hysteresis of 2 consecutive checks before alerting.
- **VACUUM does not delete application data.** It removes only "dead tuples" — stale MVCC row versions after UPDATE/DELETE. Real deletion is visible in `pg_stat_user_tables.n_tup_del`.

## Database / asyncpg / pgbouncer

- **asyncpg + pgbouncer transaction mode: BOTH `prepared_statement_cache_size=0` AND `statement_cache_size=0` required in connect_args.** `statement_cache_size=0` alone only disables the cache, not prepared statements themselves. pgbouncer in transaction mode cannot route prepared statements across backend connections.
- **asyncpg SSL: use `ssl=False` (Python bool), NEVER string `"require"` or `"disable"`.** SQLAlchemy ≥2.0 passes URL query params and connect_args strings as-is to `asyncpg.connect()`. asyncpg does not accept `sslmode` as a kwarg, and interprets non-empty strings as truthy (tries SSL).
- **Never put SSL/sslmode in the database URL query string with the asyncpg dialect.** SA asyncpg dialect passes URL query params as kwargs to `asyncpg.connect()`. Use `connect_args` instead.
- **`pool_pre_ping=True` is incompatible with pgbouncer transaction mode.** SA ping uses an unnamed prepared statement which pgbouncer rejects. Set `pool_pre_ping=False` for all engines routed through pgbouncer.
- **pgbouncer transaction mode: full fix stack is `NullPool` + `prepared_statement_name_func` + both cache sizes=0.** NullPool alone is NOT enough — SA asyncpg reuses prepared stmt names across backends. Need `prepared_statement_name_func=lambda: f"__asyncpg_{uuid.uuid4().hex}__"`. ALL parameters go in `connect_args` for SA 2.0+ (NOT as engine kwargs).
- **If pgbouncer prepared-statement errors persist after cache fixes, switch to `NullPool`.** Removes SA connection pooling on top of pgbouncer pooling.

## Monitoring / SRE

- **Verify network topology with SSH+curl before making routing decisions.** Phantom IPs cause weeks of wasted commits — consilium assumes a VPC address that never existed. Always confirm reachability before changing endpoint checks.
- **SRE bots that self-commit must be git-read-only (allowlist: status, diff, log, show, branch).** Bot self-commits to main cause production incidents (broken monitoring deployed automatically). Code changes must go through `github_create_branch` + `github_create_pr`.

## ArgoCD / GitOps

- **Never disable ArgoCD selfHeal without a timer/task to re-enable.** `selfHeal=false` means no drift detection — manual `kubectl` changes persist silently. Log every disable as incident + create a task for re-enable.
- **Never store credentials in ArgoCD Application Helm parameters.** Plaintext passwords are visible via `kubectl get application -o json`. Use K8s Secrets / SealedSecrets / ExternalSecrets.
- **ConfigMap code overrides are emergency-only, never permanent.** Mounting a ConfigMap over application code bypasses image versioning and creates a security gap. Always follow up with an image rebuild.

## Claude Code / Tooling

- **PAL MCP thinkdeep: use `relevant_files` for file paths, never paste file contents into `step`/`findings`.** PAL server reads files from disk itself (max 1MB/file). Content in the step field gets truncated.
- **Memory architecture: methods are global, knowledge is per-project.** Rules in `~/.claude/rules/` apply everywhere. Per-project knowledge stays in per-project `memory/` dirs. Institutional lessons from consiliums/audits are promoted to this file.
- **`paths:` works for rule file filtering, `globs:` does not.** In `.claude/rules/*.md` YAML frontmatter, use `paths: ["**/*.tsx"]` for conditional loading, not `globs:`.
- **Consilium agents must be briefed from verified code state, not reports/memory alone.** Reports decay. Always run RECON (Explore agents verifying actual code/configs) before spawning opinion agents. Present a Verified Facts Brief to the user before the consilium proceeds.
- **LLM verbatim recall ≠ lazy loading.** When debugging "is X loaded at session start?", never rely on Claude's own introspection — when asked to list loaded rules, Claude runs Read against `rules/*.md` to quote them precisely, which looks like lazy loading in the transcript but the content was already in context. Use `/memory` (official debug command) or the `InstructionsLoaded` hook as ground truth.
- **Round 2 audit is mandatory, not optional.** A second pass on the narrowed post-fix surface consistently catches issues that Round 1 (plus first-pass GPT review) missed. Always re-audit after applying round-1 fixes.
- **Read-only contracts must be enforced at the filesystem layer, not just the SQL layer.** `sqlite3.connect(path)` silently creates an empty 0-byte file on missing paths — it's a stdlib write primitive. Use `file:{path}?mode=ro` URIs via `sqlite3.connect(uri, uri=True)` for truly read-only paths.
- **CLI defaults that call `os.getcwd()` must handle `OSError`.** The shell's working directory can be deleted out from under a running process; on Linux/macOS, `os.getcwd()` raises `FileNotFoundError` in that state. Wrap in try/except with a sentinel fallback.
- **Path-based "which project is this?" heuristics: walk UP to a marker, don't walk DOWN greedy.** Deriving the project name by greedily descending from a parent root breaks the moment the caller is in a subdirectory. Correct primitive: walk ancestors until finding a marker directory (`reports/`, `.git/`, etc.), mirroring `git rev-parse --show-toplevel`.
- **Rule prose must quote `$(...)` expansions.** Raw `--scope $(pwd)` breaks on paths with spaces AND produces the wrong project root when Claude is launched from a subdirectory. Correct form: `--scope "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"`.
- **Category-bias column != scope column.** A naive `search(scope=<cwd>, include_global=True)` returns all global rows without project bias. Use `CASE WHEN category = ? THEN 0 ELSE 1 END, rank/priority DESC` ORDER BY for project-biased retrieval.
