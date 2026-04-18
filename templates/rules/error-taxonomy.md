---
description: Canonical categories for `error_lesson` memory rows. Source of truth for `_classify_error` in `memory_session_end.py`.
scope: global
preserve: true
---

# Error Lesson Taxonomy

Eleven canonical slugs mirror the H2 sections in `~/.claude/rules/institutional.md`. Plus `unclassified` as the fallback when no rule hits.

`memory_session_end.py::_classify_error` is a priority-ordered keyword matcher against `cmd + stderr + cwd + content` (lowercased). First match wins. Keep **specific** rules above **generic** ones — `infra-networking` is the widest net and must fire last, `unclassified` after it.

## Slugs

| Slug | Institutional.md section | One-line definition |
|---|---|---|
| `argocd-gitops` | ArgoCD / GitOps | ArgoCD/K8s/Helm drift, selfHeal, manifests. |
| `db-asyncpg` | Database / asyncpg / pgbouncer | asyncpg + pgbouncer prepared-statement, SA pool, SSL kwargs. |
| `postgres-vacuum` | PostgreSQL — VACUUM / dead tuples | vacuum, autovacuum, dead tuples, xmin horizon, cleanup jobs. |
| `nginx-proxy` | Nginx / Proxy | nginx reverse proxy, HTTP/2, keepalive, static caching. |
| `claude-tooling` | Claude Code / Tooling | PAL MCP, hooks, rolling_memory, rules, frontmatter, subagents. |
| `trading` | Financial / Trading | broker orders, fills, commissions, reconcile, NAV, VWAP (domain-specific; remove if project is not financial). |
| `monitoring-sre` | Monitoring / SRE | prometheus/grafana, alerting, oncall, SRE bot, phantom topology. |
| `deploy-cicd` | Deployment / CI/CD | Vercel, next build, env vars, CI, deploy author, edge cache. |
| `security-auth` | Security / Auth | jwt/oauth/sso, credentials, secrets in repos, api key validation. |
| `api-data` | API / Data Integrity | WebSocket reconnect, useEffect cleanup, default periods, progressive rendering. |
| `infra-networking` | Infrastructure / Networking | Docker, Alpine, IPv6, CORS, gateway, DNS, Xray, PPPoE, MikroTik, TLS handshakes. |
| `unclassified` | — | Fallback. Manual triage surfaced in `/start` context. |

## Rule ordering (priority, first-match wins)

1. `argocd-gitops` — `argocd`, `kubectl`, `helm`, `kustomize`, `kube-api`
2. `db-asyncpg` — `asyncpg`, `pgbouncer`, `prepared statement`, `sqlalchemy`, `psycopg`, `connect_args`, `nullpool`
3. `postgres-vacuum` — `vacuum`, `autovacuum`, `dead tuples`, `pg_stat_user_tables`, `xmin`, `idle in transaction`
4. `nginx-proxy` — `nginx`, `http/2`, `http2`, `proxy_pass`, `upstream`, `proxy_set_header`, `proxy_cache`
5. `claude-tooling` — `rolling_memory`, `memory_session`, `memory_post_tool`, `index_reports`, `institutional.md`, `.claude/`, `pal mcp`, `thinkdeep`, `subagent`, `frontmatter`, `consolidate(`
6. `trading` — `alpaca`, `binance`, `broker`, `commission`, `vwap`, `partial fill`, `order_id`, `reconcile`, `nav divergence`
7. `monitoring-sre` — `prometheus`, `grafana`, `oncall`, `sre bot`, `phantom ip`, `alertmanager`
8. `deploy-cicd` — `vercel`, `next build`, `deploy`, `ci/cd`, `pipeline`, `.env`, `env var`, `github actions`, `{{GIT_AUTHOR_NAME}}`, `edge cache`
9. `infra-networking` — `docker`, `container`, `healthcheck`, `cap-drop`, `alpine`, `ipv6`, `localhost`, `dns`, `keepalive`, `xray`, `socksify`, `pppoe`, `mikrotik`, `cors`, `gateway`, `tls`, `sni`, `iptables`, `reality`, `hKeepAlivePeriod`
10. `security-auth` — `jwt`, `oauth`, `sso`, `api_key`, `api key`, `credential`, `secret`, `token expir`, `opsec`, `sealedsecret`, `externalsecret`
11. `api-data` — `websocket`, `ws reconnect`, `useeffect`, `visibilitychange`, `swr`, `progressive render`, `cache-control`, `default period`, `max_reconnects`

The CORS/gateway case is deliberately matched by **`infra-networking`** (rule 9) before **`security-auth`** (rule 10), because `institutional.md` places it under Infrastructure / Networking — the fix is proxy configuration, not auth code. This reflects the one judgment call in the taxonomy.

## When to extend

- A new H2 section in `institutional.md` ⇒ add a new slug + rule + row in this file.
- A recurring `unclassified` row in `/start` context ⇒ widen the keywords of the right slug, re-tag the offending rows.
- Never add slugs on a whim — every slug must trace back to an institutional.md section so the two documents stay in sync.

## Schema note

Phase 2b does **not** add a DB column — `agent_memory.category` already exists since schema v1. This file only standardizes the values that land in that column for `memory_type='error_lesson'` rows. Other memory types (`audit`, `consilium`) keep their own `category=<project>` namespace — the two namespaces are disjoint by `memory_type`.
