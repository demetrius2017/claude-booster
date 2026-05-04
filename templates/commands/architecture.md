---
description: "Generate ARCHITECTURE.md and dep_manifest.json from codebase analysis. Multi-agent Map-Reduce: 4 parallel Explore agents + 1 Architect synthesizer."
argument-hint: "[--update]"
---

# /architecture — Codebase Architecture Generator

Produces `ARCHITECTURE.md` at the project root and `docs/dep_manifest.json` via a **Map-Reduce multi-agent pattern**: 4 parallel Haiku Explore agents each map one domain, then an Opus Architect reduces their outputs into a complete circuit-board document.

The two output files are the "circuit board" contract for this project. Every dependency connection is explicit. When code changes, these files change in the **same commit** — a PR that rewires a dependency without updating them is a defect.

---

## Pre-flight check

Before spawning any agents:

1. Check whether `ARCHITECTURE.md` already exists at the project root:
   - If it **does not exist** and `--update` was passed: warn "No existing ARCHITECTURE.md found — running full generation instead of --update."
   - If it **exists** and `--update` was NOT passed: ask once whether to overwrite or diff-update. Default (no answer within the turn) → proceed with full regeneration.
2. Count source files: `find . -type f \( -name "*.py" -o -name "*.ts" -o -name "*.tsx" -o -name "*.js" -o -name "*.go" -o -name "*.rs" \) -not -path "*/node_modules/*" -not -path "*/.git/*" | wc -l`
   - If count < 5: emit "Project too small for /architecture (< 5 source files). Skipping." and stop.
3. Identify the project's primary language/framework from file extensions + package.json / pyproject.toml / go.mod / Cargo.toml (whichever exists).
4. Build a **Verified Facts Brief**: project name, primary stack, rough file count, entry-point files. This brief goes into every agent's prompt.

---

## Phase 1 — MAP (4 parallel Haiku Explore agents)

Spawn **all four agents in a single message** (one `Agent` tool call per agent, all in the same response turn). All use `subagent_type: "Explore"`, `model: "haiku"`.

Each agent is independent — they share only the Verified Facts Brief and their specific search instructions. They do NOT share results with each other.

---

### Agent 1 — DB Analyst

**Prompt:**

```
You are DB Analyst for the /architecture command. Your job: map every persistent data structure in this codebase.

Verified Facts Brief:
<insert brief from pre-flight>

Search for ALL of the following:
- SQL: CREATE TABLE, ALTER TABLE, CREATE INDEX, FOREIGN KEY, CHECK, UNIQUE, DEFAULT
- ORM models: SQLAlchemy (class X(Base)), Django (class X(models.Model)), Prisma (model X {), TypeORM (@Entity, @Column), ActiveRecord (class X < ApplicationRecord), Ecto (schema "x" do)
- Migration files in: db/migrations/, alembic/versions/, prisma/migrations/, schema.rb, *.sql
- Pydantic models / dataclasses / TypeScript interfaces / GraphQL types that map to DB rows

For each table / model found, output:
- Table name
- File path and line number where defined
- Columns with types and constraints (NOT NULL, UNIQUE, FK, CHECK)
- Relationships (FK to which table, one-to-many, many-to-many join tables)
- Append-only markers (any trigger or comment saying INSERT-only, audit log, ledger)

Output format: plain text list, one table per block, no prose. Include file paths.
Search broadly — do not stop after the first migration directory. Check all subdirectories.
```

---

### Agent 2 — API Mapper

**Prompt:**

```
You are API Mapper for the /architecture command. Your job: map every external-facing interface.

Verified Facts Brief:
<insert brief from pre-flight>

Search for ALL of the following:
- FastAPI / Flask / Django REST: @app.get, @app.post, @router.get, @router.post, @router.put, @router.delete, @router.patch, @api_view, urlpatterns
- Express / NestJS / Next.js API routes: app.get(, app.post(, router.get(, router.post(, export default function handler, export async function GET, export async function POST, app.use(
- GraphQL: type Query {, type Mutation {, Resolver(), @Query(), @Mutation()
- WebSocket handlers: @websocket, on('message'), on('connection'), websocket.connect, WebSocket(, ws.on(
- gRPC: .proto service definitions, rpc <Method>(
- CLI entry points: @click.command, argparse.add_argument, app.command()
- Background task decorators: @celery.task, @dramatiq.actor, @huey.task, @app.on_event("startup")

For each endpoint / handler found, output:
- HTTP method + path (or WebSocket channel / gRPC method / CLI command name)
- File path and approximate line number
- Handler function name
- What it reads: DB tables, cache keys, external services (infer from function body / imports)
- What it writes: DB tables, cache keys, queues, external calls (infer from function body)
- Auth check: yes/no/unknown (look for jwt, auth, require_login, Depends(get_current_user), @login_required)

Output format: plain text list, one endpoint per block. Include file paths.
```

---

### Agent 3 — Logic Tracer

**Prompt:**

```
You are Logic Tracer for the /architecture command. Your job: map the core business logic — functions that are NOT simple CRUD wrappers.

Verified Facts Brief:
<insert brief from pre-flight>

Identify business logic by looking for:
- Functions/methods in domain/, services/, core/, lib/, utils/, workers/, jobs/, tasks/ directories
- Functions called from multiple places (high fan-in) — grep for the function name across the codebase
- Functions that do computation, reconciliation, validation, aggregation, financial math, ML inference
- Scheduled tasks: APScheduler @scheduler.scheduled_job, celery beat, cron comments, @cron, setInterval, schedule.every()
- Background workers: asyncio tasks, threading.Thread, subprocess.Popen for long-running jobs
- State machines: functions with if/elif chains on status fields
- Cache management: functions that DEL or SET cache keys

For each non-trivial function found, output:
- Function name + file path + line number
- What it reads (DB tables, cache keys, other functions called)
- What it writes (DB tables, cache keys, external side effects)
- Who calls it (grep for call sites — list callers)
- One-line description of what it does
- Any invariants it enforces (assertions, raises on invalid state)

Focus on the top 10–20 most consequential functions. Skip trivial getters/setters.
Output format: plain text list, one function per block. Include file paths.
```

---

### Agent 4 — Integration Mapper

**Prompt:**

```
You are Integration Mapper for the /architecture command. Your job: map every connection to the outside world.

Verified Facts Brief:
<insert brief from pre-flight>

Search for ALL external connections:
- HTTP clients: requests.get/post, httpx.get/post, axios.get/post, fetch(, urllib.request
- WebSocket clients: websocket.connect, websockets.connect, io.connect, new WebSocket(
- Database connections: asyncpg.create_pool, psycopg2.connect, create_engine, MongoClient, redis.Redis, Redis(
- Message queues: pika.BlockingConnection, KafkaProducer, KafkaConsumer, redis.xadd, redis.xread, boto3.client('sqs')
- Cloud services: boto3.client, google.cloud, azure.mgmt, stripe.charge, twilio.Client
- Broker / financial APIs: ibapi, alpaca, binance, ccxt, ib_insync, IB(
- Email / SMS: sendgrid, smtplib, mailgun, twilio, postmark
- File storage: s3.upload_file, open( for large data files, csv.writer, pandas.to_csv
- Auth providers: oauth2, google_auth, github_auth, auth0
- Monitoring / logging: sentry_sdk.init, datadog, statsd, prometheus_client

For each external system found, output:
- External system name and type (HTTP API, DB, queue, broker, cloud, etc.)
- File path(s) where the connection is established and where it is called
- Connection method (HTTP REST, WebSocket, TCP socket, library SDK)
- Data flowing IN: what fields/events the system sends to this codebase
- Data flowing OUT: what this codebase sends to the external system
- Retry / error handling present: yes/no (look for try/except, backoff, retry decorator)

Output format: plain text list, one external system per block. Include file paths.
```

---

## Phase 2 — REDUCE (1 Opus Architect agent)

After **all 4 MAP agents return**, collect their full outputs. Then spawn ONE agent:

- `subagent_type: "general-purpose"`
- `model: "opus"`

**Prompt:**

```
You are Architect for the /architecture command. You have received raw domain maps from 4 specialist agents. Your job: synthesize them into two output files.

## Verified Facts Brief
<insert brief from pre-flight>

## MAP outputs

### DB Analyst output:
<paste Agent 1 full output>

### API Mapper output:
<paste Agent 2 full output>

### Logic Tracer output:
<paste Agent 3 full output>

### Integration Mapper output:
<paste Agent 4 full output>

## Your task

Connect the dots across all four maps:
- Which endpoint calls which business logic function?
- Which function reads from / writes to which DB table?
- Which function calls an external integration?
- Which cron job triggers which chain of functions?
- Which Redis / cache key is written by whom and read by whom?

Then produce two files:

---

### File 1: ARCHITECTURE.md

Write to the PROJECT ROOT (not .claude/). Follow this exact structure:

```markdown
<!--
  ARCHITECTURE.md — Circuit Board Document
  Version:  1.0.0
  Date:     <today's date>
  Session:  <write "generated by /architecture — update with commit SHA">
  Author:   generated by /architecture command
  RULE: This file is the "circuit board" of the system.
  Every connection between components is explicit here.
  When code changes, this file changes in the SAME COMMIT.
  A PR that rewires a dependency without updating this doc is a defect.
-->

# Architecture: <project name>

## The Circuit Board Contract

[2-3 sentence description of what the system does and its key invariant.]

**If you are adding a new dependency:** add a row to the Dependency Table and an edge to the Container Diagram before merging.

---

## C4 Level 2 — Container Diagram

[Mermaid C4Container diagram. Include: all services/components found, their tech stack labels, all databases/caches, all external systems. Draw Rel() edges for every real dependency you found.]

---

## Dependency Table

| Component | Reads from | Writes to | Called by | Breaks if changed |
|---|---|---|---|---|
[One row per significant component — endpoint, function, worker, scheduler job, DB table, cache key.
At least 5 rows. Be specific: name the function, not just "API".]

---

## Data Flows

[One flowchart per major data pipeline. Use Mermaid flowchart TD.
Typical pipelines: ingest/event → process → persist → cache → respond.
Show decision branches (validation pass/fail, auth check, duplicate check).
Label each edge with what data flows across it.]

---

## Invariants

| ID | Invariant | Checked by | On violation |
|---|---|---|---|
[List every business invariant found: financial math rules, append-only tables, quantity bounds, type constraints.
If none found explicitly, infer from validation code and DB constraints.]

---

## Protected Paths

### Derived / Read-Only Columns
[Columns computed by a specific function — must not be patched via direct SQL.]

| Column | Owner function | How it's computed |
|---|---|---|

### Append-Only Tables
[Tables where UPDATE/DELETE is forbidden per business logic or audit requirements.]

| Table | Written by | Why append-only |
|---|---|---|

---

## Update Log

| Date | Commit | Change description | Author |
|---|---|---|---|
| <today> | generated | Initial architecture document from /architecture command | generated |
```

---

### File 2: docs/dep_manifest.json

Write to `docs/dep_manifest.json` in the project. JSON format:

```json
{
  "_comment": "Dependency manifest for <project>. Machine-readable companion to ARCHITECTURE.md. Keep in sync — same commit rule applies. Format: version 0.1.0.",
  "version": "0.1.0",
  "project": "<project name>",
  "updated": "<today's date>",

  "components": {
    "<component_id>": {
      "file": "<file_path>::<function_or_class_name>",
      "reads_from": ["db:<table>", "cache:<key_pattern>", "api:<endpoint>", "external:<system>", "domain:<function>"],
      "writes_to": ["db:<table>", "cache:<key_pattern>", "external:<system>"],
      "called_by": ["<component_id>", "<component_id>"],
      "critical": true,
      "notes": "<one sentence on the most important invariant or constraint>"
    }
  },

  "data_patches_forbidden": ["<table>.<column>"],

  "append_only_tables": ["<table>"],

  "invariants": {
    "INV-01": {
      "description": "<what must always be true>",
      "formula": "<mathematical or logical formula>",
      "enforced_in": "<file::function>",
      "on_violation": "<what the code does — raise X, reject, log>"
    }
  },

  "migration_guard_rules": {}
}
```

Include one entry per component from the Dependency Table. Include all invariants. If no append-only tables exist, leave the array empty — do not omit the key.

---

## Output requirements

1. Write ARCHITECTURE.md to the project root using the Write tool.
2. Create docs/ directory if it does not exist, then write docs/dep_manifest.json using the Write tool.
3. Return a summary: how many components in dep_manifest.json, how many rows in the Dependency Table, how many invariants found, how many Mermaid diagrams produced.
4. If you cannot determine something with confidence (e.g. the project has no DB), write "<!-- Not applicable: no database layer found -->" in that section rather than inventing data.

Do NOT hallucinate connections that are not evidenced in the MAP outputs. If the evidence is ambiguous, say so in a `<!-- NOTE: ... -->` comment in the document.
```

---

## Phase 3 — VERIFY

After the Architect agent writes both files, run these checks directly via Bash:

```bash
# 1. ARCHITECTURE.md has Mermaid blocks
grep -c '```mermaid' ARCHITECTURE.md

# 2. Dependency table has at least 5 data rows (pipes in rows, minus header/separator)
awk '/^\| Component/{found=1} found && /^\|[^-]/' ARCHITECTURE.md | grep -v 'Component\|Reads from' | wc -l

# 3. dep_manifest.json parses as valid JSON
python3 -c "import json, sys; d=json.load(open('docs/dep_manifest.json')); print(f\"OK — {len(d['components'])} components, {len(d.get('invariants',{}))} invariants\")"

# 4. At least one flowchart diagram (Data Flows section)
grep -c 'flowchart' ARCHITECTURE.md
```

**Pass criteria:**
- Mermaid block count ≥ 1
- Dependency table rows ≥ 5
- dep_manifest.json: valid JSON, `components` key present
- Flowchart count ≥ 1

If any check fails: emit the failure with the command output, then spawn a new Architect agent with narrowed scope to fix only the failing section. Do NOT patch inline as Lead.

---

## Phase 4 — COMMIT

On PASS:

```bash
git add ARCHITECTURE.md docs/dep_manifest.json
git commit -m "docs(architecture): generate ARCHITECTURE.md and dep_manifest.json via /architecture

Auto-generated by /architecture command (Map-Reduce: 4 Haiku Explore + 1 Opus Architect).
Update this file in the same commit as any dependency change."
```

Report to user: files written, verification passed, committed.

---

## --update mode

When invoked as `/architecture --update`:

1. Read the existing `ARCHITECTURE.md` and `docs/dep_manifest.json`.
2. Run only Agents 1–4 (MAP phase) as normal.
3. In the Architect prompt, add:

   ```
   ## Existing architecture (diff-update mode)
   The files below already exist. Your job is NOT to rewrite them from scratch.
   Instead:
   - Add new rows to the Dependency Table for components not yet listed.
   - Add new edges to the C4 diagram for connections not yet drawn.
   - Add new invariants to the Invariants table.
   - Add new components to dep_manifest.json.
   - Update the Update Log with today's date and a one-line description of what changed.
   - Do NOT remove existing rows unless you have clear evidence the component was deleted.

   Existing ARCHITECTURE.md:
   <paste existing file>

   Existing dep_manifest.json:
   <paste existing file>
   ```

4. Run the same VERIFY phase. Commit with message: `docs(architecture): update ARCHITECTURE.md via /architecture --update`.

---

## Integration with /start

`/start` checks for architecture files automatically. If neither `ARCHITECTURE.md` nor `docs/dep_manifest.json` exists:

- **Project has >20 source files:** `/start` **auto-spawns `/architecture` in background** — a `general-purpose` Agent with `model: "opus"` and `run_in_background: true`. The session continues in parallel; architecture generation does not block `/start`. ARCHITECTURE.md and dep_manifest.json will appear when the background agent completes.
- **Project has ≤20 source files:** `/start` notes the gap with a one-liner ("small project — /architecture optional") and moves on.

The user can still run `/architecture` manually at any time, or `/architecture --update` after major refactors.

---

## Verification summary (what Lead reports to user)

After Phase 3 passes:

```
/architecture complete.
  ARCHITECTURE.md: <N> Mermaid diagrams, <N> dependency table rows, <N> invariants, <N> protected columns
  dep_manifest.json: <N> components, <N> invariants, <N> append-only tables
  Verification: all 4 checks passed
  Committed: <commit SHA>
```

If verification partially failed and was fixed on retry, note which check failed and what the Architect changed.
