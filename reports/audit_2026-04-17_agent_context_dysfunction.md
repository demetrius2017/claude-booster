---
name: "Audit 2026-04-17 — Agent context dysfunction на long-running проекте (horizon case)"
description: >
  Мета-аудит на 5 агентах + 2 GPT (neutral/against) почему Claude Code не смог
  за 39 дней решить расхождение учёта портфеля и broker data в horizon. Это
  аудит не кода horizon, а взаимодействия кода + памяти + правил + процесса,
  которое порождает symptom-chasing без cross-session гипотезы. Результат:
  14 рекомендаций в 3 tranche (immediate deploy, memory/protocol, architecture)
  + P0 находка от GPT-pro (dead broker-snapshot write path). Все recommendations
  имеют файл, effort, risk, rollback, KPI.
type: audit
date: 2026-04-17
scope: global
preserve: true
category: Claude_Booster
---

# Audit 2026-04-17 — Agent context dysfunction на long-running проекте

## Context

Дмитрий работает с `/Users/dmitrijnazarov/Projects/horizon` **39 дней** над одной проблемой: расчётная доходность портфеля не совпадает с broker data после deploy/redeploy. Агент:

- не верифицирует собственные изменения (0 curl/test за 10 handover'ов),
- задаёт одни и те же вопросы ("source of truth?") в сессиях 6/7/8 с разными ответами,
- трактует сломанную архитектуру как данность,
- documents root cause в Known Issues, но не имплементирует,
- каждая сессия ставит PASS на логическом ревью, следующая выясняет — не сработало.

Этот аудит — мета-анализ на 5 агентах + 2 GPT, чтобы понять **что именно сломано в связке код/память/правила/процесс** и составить карту фиксов от immediate до architectural.

---

## Methodology

| Шаг | Действие |
|---|---|
| RECON | 3 параллельных Explore-агента: (1) 10 последних handover'ов horizon, (2) per-project memory + rolling DB + rules, (3) usage report + ключевой код horizon. |
| Panel | 5 general-purpose агентов с уникальными bios (Context Engineer, Software Architect, Anti-Loop Specialist, DevOps, Data Forensics), одинаковый Verified Facts Brief. |
| External | PAL MCP `consensus` с gpt-5.4 (neutral) и gpt-5.4-pro (against stance). |
| Synthesis | Lead (Claude) интегрирует 5+2 позиций, разрешает противоречия, формирует бандл с priority и rollback. |

Все файлы (code + memory + rules + reports) передавались через `relevant_files`, НЕ копировались в prompt'ы (rule `tool-strategy.md`).

---

## Verified Facts Brief (RECON output)

### F1. Agent dysfunction pattern (10 handover'ов horizon)

| # | Pattern | Evidence |
|---|---|---|
| 1 | Symptom-chasing без cross-session гипотезы. 5 сессий переписывают NAV source-of-truth. Термин "void-and-rewrite" появляется только 2026-03-26. | `handover_2026-03-26_133700.md:1-68` vs earlier handovers |
| 2 | False PASS без prod verification. Keyword count `curl`/`test` = **0** across 10 handover'ов. | Grep aggregate |
| 3 | Repeating questions across sessions. "Source of truth — snapshots or broker?" — sessions 6/7/8 с разными ответами. | `handover_2026-03-25_205000.md:15-20` / `handover_2026-03-25_220000.md:13-20` / `handover_2026-03-26.md:36-40` |
| 4 | Deferred architectural ownership. `_record_rebalance_erp()` P1, Known Issues 30+ days, не имплементирован. | `handover_2026-03-25_230000.md:80-86`, `handover_2026-03-26:60-67` |
| 5 | Cross-handover contradictions без acknowledgement. | 2026-03-24 «DB prices» vs 2026-03-25 «broker snapshot × DuckDB» |

### F2. Context poisoning (загружаемый контекст)

| Source | Claim (stale) | Contradicted by | Δ days |
|---|---|---|---|
| `~/.claude/projects/-Users-dmitrijnazarov-Projects-horizon/memory/project_ibkr_execution_audit_fix.md` | "21 critical/high fixes deployed" (2026-03-25) | `consilium_2026-04-17_broker_parity_architecture.md` (3 open gaps) | +23 |
| `~/.claude/rules/institutional.md` §Financial/Trading — 7 rules | "Auto-reconcile positions after every apply" (reads as canon) | `audit_2026-04-15`: "structurally wrong... writes diverge from broker reality" | +19 |
| `horizon/CLAUDE.md` | Phase-lock "Phase 1 → Phase 2 → Phase 3" + gate "don't write reports without request" | Consilium 2026-04-17 требует blocker-first | +23 |
| `rolling_memory.db` rows 3422-3425 | Mar 25 audit duplicated как rolling-row | Рядом лежат Apr 14/17 консилиумы без приоритета recency | — |

**Механизм poisoning подтверждён обеими GPT-моделями**: `institutional.md:2` говорит "Permanent knowledge — never auto-prune" без supersession semantics. Stale canon читается как current truth.

### F3. Architectural root cause

Код (verified by 3 Explore + 2 GPT reads):

1. `backend/main.py:133-135` — `asyncio.create_task(pool.startup_reconcile())` — **non-blocking**. Uvicorn reports READY до завершения reconcile.
2. `backend/portfolio_db.py:1286-1297` — `broker_snapshots` cache clear при apply/deploy, никогда не pre-populated.
3. `backend/snapshot_cron.py:313-321` — `COALESCE(is_reconciliation, FALSE) = FALSE` → reconcile-writes excluded from historical NAV → equity curve freezes.
4. **[P0, найдено GPT-pro]** `backend/snapshot_cron.py:181-187` skip condition `if total_value <= 0` combined с `_snapshot_from_broker()` передающим empty positions по `snapshot_cron.py:336-342` → broker-side snapshot writes **never persist**. Fallback на DB-derived каждый раз. **Это объясняет почему "broker truth" никогда не попадает в историю.**
5. Void-and-rewrite в `_record_deploy_erp()` delete trades/cash_ledger/lots/realized_pnl, до 2026-03-26 оставлял `portfolio_snapshots` со stale `cash_balance`.
6. `ibkr-tws/Dockerfile:71` healthcheck — только TCP-socket.

**Deploy-divergence recipe**: cache clear → async reconcile → user query до завершения → stale response → reconcile пишет corrections → corrections excluded from NAV → broker-side snapshot write dies in `total_value<=0` skip → DB-derived фallback с устаревшим cash_balance = permanent divergence.

---

## Panel positions (5 Claude agents)

| # | Agent (bio) | Core insight | Top-3 prescriptions | Decision |
|---|---|---|---|---|
| **A** | **Context Engineer** — specialist в prompt/context design для long-running agents | Five mechanisms производят "architecture-as-given" bias: (a) rules > reports authority asymmetry, (b) no recency/supersession encoding, (c) MEMORY.md amplifies old confidence, (d) phase-lock в CLAUDE.md, (e) no default RECON for recurring topics. | R1 supersession metadata (`[UNDER REVIEW since audit_YYYY-MM-DD]`) в institutional.md; R2 Open Blockers секция в horizon MEMORY.md поверх phase-lock; R3 recurring-topic RECON gate в /start. | Advocate FOR R1+R2+R3 bundle — любой один недостаточен. |
| **B** | **Software Architect** — 15 лет portfolio-accounting systems | Broken invariant: нет единого state commit boundary. Три writer'а конкурируют за NAV projection — apply path (optimistic), deploy path (void-rewrite), async reconcile. Minimum consistent model = append-only broker_events + computed-on-read projections. | Prerequisites: блокирующий startup_reconcile (main.py:112-145), freeze void-and-rewrite за ENV flag, pre-warm broker_snapshots serial. Destination: Broker Mirror v2 phased with shadow mode. | Advocate FOR S2 (Mirror v2), **но gated** на R1+R2+R3 prerequisites first. Reject S1 (incremental ERP) — 30 дней доказательства что incremental не держит invariant. Reject S3 (full event-sourcing) — overshoot для 4 брокеров. |
| **C** | **Anti-Loop Specialist** — LLM workflow design | Existing "failed twice — STOP" не срабатывает потому что это intra-session detector. Каждая сессия horizon looks healthy в isolation. Missing: (a) hypothesis contract, (b) VERIFY gate as tripwire not prose, (c) recurring-theme detector. Structural difference loop vs debuggable = bounded hypothesis space. | P1 Hypothesis Contract (4-line standing theory carried cross-session) в /start и /handover; P2 VERIFY Gate как PreToolUse hook что блокирует handover без curl/SQL/DevTools artifact; P3 Stale-P1 auto-consilium escalation. Готовый rule text для `~/.claude/rules/long-running-problem.md`. | Advocate FOR P1+P2+P3 — triad нужен целиком. P2 highest leverage single change (hook-enforced). |
| **D** | **DevOps Engineer** — Docker/FastAPI/healthchecks | Operational invariant violated: "READY ⇒ reconciled broker-DB parity". Ranked impact: startup_reconcile non-blocking (HIGHEST) > no post-reconcile snapshot recompute (HIGH) > cache not pre-warmed (HIGH) > TCP-only healthcheck (MED) > void-rewrite (LOW now, HIGH historically). | P1 blocking startup reconcile with 45s timeout + fail-fast; P2 snapshot recompute hook after reconcile; P3 broker-parity healthcheck (ibkr-pool + ibkr-tws); P4 TCP-healthcheck tightening; P5 post-deploy smoke test endpoint + CI gate. ~9.5h total, **один commit**, один `git revert`. | Advocate FOR P1+P2+P3+P4+P5 as bundle. Feature flags для per-env brown-out. |
| **E** | **Data Forensics** — financial data integrity | Source-of-truth contract: broker > event log > derived snapshot > aggregate. `portfolio_positions` НИКОГДА не источник NAV. `is_reconciliation=TRUE` exclusion из NAV — bug, не feature. 8 SQL invariants I1-I8 где writers создают phantom-cash (04-14 pattern) impossible by write-time triggers. 5 forensic queries Q1-Q5 как runbook. | R1 triggers I1+I2 на cash_ledger + migrate reason NOT NULL для SYSTEM_ADJUSTMENT; R2 fix snapshot_cron.py:316-321 (stop excluding is_reconciliation); R3 forensic endpoint + nightly cron. | Advocate FOR broker-as-truth + write-time invariants + operationalized playbook. Enforcement, не architecture. |

---

## GPT consensus (PAL MCP)

### GPT-5.4 neutral (confidence 8/10)
- **FOR** bundle. Core technical diagnosis well-supported by code, not just narrative.
- **AGAINST/missing**: (i) readiness vs liveness conflation — broker-parity должен быть readiness probe не liveness, иначе broker outage ломает app; (ii) durable cross-process transactional isolation недостаточен (Python async locks не заменяют DB serializable); (iii) "absent operational evidence discipline" — pipeline.md уже требует curl после deploy, handover'ы его не содержат. **Proсто правило не применялось.**
- **Sequencing revision**: tranche 1 = deploy fixes + atomic sync + 429 retry **SAME WEEK** (не buried в Mirror v2); tranche 2 = guardrails; tranche 3 = Mirror v2 in shadow mode.
- **Flag**: Stale-P1 auto-consilium — lowest ROI, ceremony без evidence fix.

### GPT-5.4-pro against (confidence 8/10)
- **P0 miss найден**: `_snapshot_from_broker()` (`snapshot_cron.py:336-342`) передаёт empty positions, а `snapshot_portfolio()` (`snapshot_cron.py:181-187`) hard-skip при `total_value <= 0`. **Broker-side snapshots никогда не пишутся**. Fallback на DB-derived всегда. Объясняет почему "broker truth" не достигает UI истории. Немедленно в tranche 1.
- **Immediate #2 nuance**: "include reconciliation в NAV" blindly → repairs превращаются в "performance" (фальшивый alpha). **Правильная формулировка**: reconcile должен invalidate/rebuild snapshots, ИЛИ NAV читается broker-equity напрямую.
- **Writer inventory**: нужен explicit "freeze legacy writers / inventory all writers" шаг. Без этого Mirror v2 построится поверх продолжающих писать старых путей.
- **Bundle split**: НЕ один commit. Разделить на readiness, snapshot/NAV, verification gates. Каждый revertable.
- **Context poisoning — real но secondary**: если RECON/VERIFY были бы enforced, stale rules были бы contained.
- **What broke continuity (е)**: "rules existed only as prose, not as blocking mechanics" + no durable incident artifact overriding phase-lock + startup racing reconcile + no carried-forward hypothesis.

### Consensus table — agent/position/insight/KPI

| Agent | Stance | Key insight | Primary KPI |
|---|---|---|---|
| A (Context) | FOR memory hygiene bundle | Stale canon без decay-markers — primary bias source | References to latest audit in handover ≥1/session (сейчас 0) |
| B (Architect) | FOR Mirror v2 **after** prerequisites | Three-writer conflict is structural, not tactical | Historical NAV recomputable from events alone (определённость) |
| C (Anti-Loop) | FOR hypothesis contract + VERIFY gate | Intra-session anti-loop не ловит cross-session symptom-chase | Sessions with curl/SQL evidence per handover ≥1 |
| D (DevOps) | FOR immediate deploy bundle | READY-before-consistent is root operational sin | Time-between "200 OK" and "NAV == broker" < 1s |
| E (Forensics) | FOR write-time invariants + runbook | Broker-as-truth exists в doctrine, не enforced at write | Phantom SYSTEM_ADJUSTMENT rows created = 0 |
| GPT-5.4 neutral | FOR bundle с корректировкой | Real continuity break = unenforced evidence link | VERIFY gate blocks N handovers/week until adapted |
| GPT-5.4-pro against | FOR bundle + P0 addition | Broker-snapshot write path likely dead | `portfolio_snapshots` rows с `source='broker'` > 0 после fix |

---

## Synthesis — what actually broke

**Три-частный continuity failure** (consensus всех 7):

1. **Authority without supersession**. `institutional.md` + per-project memory хранят March findings как canonical "Permanent knowledge — never auto-prune", а April consiliums лежат в `reports/` без link back. Агент читает старое как закон.
2. **Rules as prose, not blocking mechanics**. `pipeline.md:13-17` требует "curl API on prod" после deploy. `commands.md:29-35` требует RECON before opinions. Оба правила существуют и загружены. Handover'ы их не выполняют. **Missing forcing function = hook that blocks wrong action, not another rule that describes right action.**
3. **Runtime system introduces fresh divergence on every deploy faster than any session can patch it**. Async startup + dead broker-snapshot write path + `is_reconciliation` NAV exclusion → каждый deploy восстанавливает class of bug, which агент чинит как symptom, ставит PASS, deploy снова.

Context poisoning — real но **secondary**. Если бы RECON/VERIFY были enforced + runtime invariant был держим, stale rules сами бы состарились в течение одного-двух сессий.

---

## Recommendations — final bundle

### Tranche 1 — Runtime correctness (1 week, 3 separate commits)

Commit 1 — Readiness gate (P0 от D, подтверждено GPT neutral/pro):

| # | What | File | Effort | Risk | Rollback | KPI |
|---|---|---|---|---|---|---|
| T1.1 | Blocking startup reconcile с 45s timeout + fail-fast. `await asyncio.wait_for(pool.startup_reconcile(), timeout=STARTUP_RECONCILE_TIMEOUT_S)`. Gate на `/health/ready` не на `/health/live` (GPT correction — liveness остаётся unconditional для non-broker routes). | `backend/main.py:112-145` | 2h | Crash-loop на broker hang. Mitigation: timeout + flag `STARTUP_RECONCILE_REQUIRED=0` для brown-out. | `git revert` + env flip | Zero `/api/portfolios/*` responses в первые 5s после pod ready с `cash_balance != reconciled` |
| T1.2 | Broker-parity readiness probe. Заменить TCP probe в ibkr-pool Dockerfile на двухступенчатый check `/v1/api/tickle` + `/v1/api/iserver/auth/status`. В ibkr-tws добавить layer-2 API probe. Keep TCP для basic liveness. | `deploy/ibkr-pool/Dockerfile:22-23`, `deploy/ibkr-tws/Dockerfile:70-71` | 2h | Stricter probe может rotate slots при transient SSO blips (это желаемое поведение). | `git revert` | `healthy=true ∧ /iserver/accounts=401` = 0 over 7 days |

Commit 2 — NAV truth path (P0 от GPT-pro + переосмысленное от D/E):

| # | What | File | Effort | Risk | Rollback | KPI |
|---|---|---|---|---|---|---|
| T1.3 | **[P0 от GPT-pro]** Fix dead broker-snapshot write path. Решить: либо передавать real positions в `_snapshot_from_broker()` (вместо empty `[]`), либо убрать `total_value<=0` skip когда `source='broker'`. Также invalidate+rebuild snapshots **ON reconcile completion**, не переподключать `is_reconciliation=TRUE` в cron-path. | `backend/snapshot_cron.py:181-187`, `336-342`, `313-321`; `backend/ibkr_sync.py` | 4h | Blind inclusion reconcile-rows может превратить repair в "performance" alpha (GPT-pro warning). Mitigation: separate `recompute_on_reconcile()` path с `ON CONFLICT DO UPDATE`, cron-path exclusion не трогаем. | `git revert` + restore snapshot backfill with old logic | `portfolio_snapshots` rows с `source='broker'` > 0 daily; `abs(broker_nav - (snap.total + snap.cash)) < $0.50` на 100% активных портфелей |
| T1.4 | Pre-warm broker_snapshots в startup_reconcile (после reconcile, перед READY). Serial с 429 retry (consilium 04-17 gap #1). | `backend/portfolio_db.py:1286-1297`, startup path | 6h | Startup scale с N brokers × latency. ОК сегодня, revisit при масштабировании. | Удалить prewarm call — fallback на lazy | Zero `broker snapshot failed, falling back to DB` warnings в первые 60s |

Commit 3 — Post-deploy verification (GPT neutral + D + C):

| # | What | File | Effort | Risk | Rollback | KPI |
|---|---|---|---|---|---|---|
| T1.5 | Post-deploy smoke endpoint `/api/healthz/deploy-parity` возвращает `{portfolios_checked, max_divergence_usd}`. CI gate fail при `max_divergence_usd > $10`. Vercel-side `Cache-Control: no-cache` на API routes. | `backend/main.py` (new endpoint), `.github/workflows/deploy.yml` | 3h | False-positive triggers unwanted revert. Mitigation: 3 retries + 20s backoff. | `git revert` endpoint + workflow | MTTD divergence < 120s (сейчас ~39 дней) |
| T1.6 | Atomic sync + 429 retry для eToro (consilium 04-17 gap #1 + #2). **Move from Mirror v2 scope to Tranche 1 per GPT neutral.** Wrap sync в SERIALIZABLE transaction; classify 429 responses с exponential backoff. | `etoro_client.py:173-176`, `etoro_sync.py::sync_positions_from_broker` | 6h | SERIALIZABLE conflicts под load — retry logic. | `git revert` | Zero "zero positions after sync" events; 429 retry success rate > 95% |

**Tranche 1 total**: ~23h, 3 revertable commits. Deploy to staging → 24h soak → prod during low-traffic window.

### Tranche 2 — Evidence enforcement (1-2 weeks, protocol layer)

| # | What | Where | Effort | Risk | Rollback | KPI |
|---|---|---|---|---|---|---|
| T2.1 | **VERIFY gate as PreToolUse hook** (Agent C P2 + GPT consensus). Hook на `/handover` и `TaskUpdate status=completed` грепает session transcript на `curl `, `psql`, `sqlite3`, `list_network_requests`, либо explicit `{"verified": "N/A — no runtime surface"}`. Block с message при отсутствии. | `~/.claude/scripts/verify_gate.py` + `~/.claude/settings.json` PreToolUse | 4h | False positives на pure refactor — escape hatch через N/A annotation. | Remove hook entry from settings.json | Prod-evidence artifacts per DONE handover ≥1 (сейчас 0) |
| T2.2 | **Hypothesis Contract** (Agent C P1). Rule в `commands.md` §start что требует 4-line paragraph: Standing theory / Evidence FOR / Evidence AGAINST / Today's verdict target. Re-printed в /handover. Reversal (противоречие yesterday) требует explicit "I retract X because Y". | `~/.claude/rules/commands.md` + `~/.claude/rules/long-running-problem.md` (new, ~80 lines) | 3h | Overhead на trivial tasks — opt-out "No prior theory — fresh task." | Delete new rule file, revert commands.md | % /start sessions на continuing topic с hypothesis paragraph ≥90% after 2 weeks |
| T2.3 | **Supersession metadata** (Agent A R1 + GPT consensus). Status tags в institutional.md rules: `[ACTIVE]`, `[SUPERSEDED by audit_YYYY-MM-DD]`, `[UNDER REVIEW since audit_YYYY-MM-DD]`. Pomeчить 7 March-25 IBKR rules как `[UNDER REVIEW since audit_2026-04-15]`. | `~/.claude/rules/institutional.md` | 1h | Over-tagging создаёт opposite pathology (distrust everything). Discipline: только там где later audit contradicts. | `git revert` | References to `audit_2026-04-15` в horizon session handovers ≥1/session |
| T2.4 | **Open Blockers** секция в horizon MEMORY.md (Agent A R2). Overrides phase roadmap. Claude proposes, Дмитрий approves. Strict definition — blocker must ссылаться на consilium/audit file. | `~/.claude/projects/-Users-dmitrijnazarov-Projects-horizon/memory/MEMORY.md` + horizon/CLAUDE.md §6 | 1h | Может drift от roadmap.html без ownership. | Delete section | Sessions тратят first turn reading named consilium |
| T2.5 | **Recurring-topic RECON gate** в `/start` (Agent A R3 + Agent C P1 merge). Если тема (noun) из user-первого-сообщения присутствует в 3+ handover'ах подряд — RECON обязателен до любого code change. Helper `~/.claude/scripts/recurring_topic_detector.py`. | `~/.claude/rules/commands.md` + new script | 2h | Slow cadence +5min / session. Mitigation: threshold 3+, не 2+. | Revert rule change, drop script | First tool call на continuing topic == Read относительно audit/consilium file |

**Tranche 2 total**: ~11h. Скипнутый item — **Stale-P1 auto-consilium** — обе GPT назвали lowest ROI. Opt-out annotation `[deferred: reason until date]` в Known Issues handover'ов достаточна без автоматизации.

### Tranche 3 — Data integrity enforcement (parallel to T2, DB layer)

| # | What | Where | Effort | Risk | Rollback | KPI |
|---|---|---|---|---|---|---|
| T3.1 | Triggers I1+I2 на cash_ledger: running_balance CHECK + entry_type sign + `reason NOT NULL` для SYSTEM_ADJUSTMENT. Shadow mode 48h (log-only) → enforcing. | `backend/portfolio_db.py` schema DDL | 1 day | Trigger добавляет ~0.2ms/insert. Если writer без `reason` — loud failure. | `DROP TRIGGER`; `ALTER DROP NOT NULL` | Q1 query returns 0 rows post-rollout; 0 phantom-cash incidents/30d |
| T3.2 | Forensic endpoint `/api/admin/integrity-check/{portfolio_id}` + nightly cron queries Q1-Q5 (Agent E). Записывают `INTEGRITY_ALERT` event. Dashboard card `open_integrity_alerts`. | Backend new endpoint + cron | 2 days | None (read-only). | Disable cron, remove route | MTTD integrity drift ≤24h |
| T3.3 | Writer inventory **(GPT-pro addition)**. Grep codebase для всех writer'ов на `cash_ledger`, `position_lots`, `portfolio_snapshots`, `realized_pnl_log`. Документ в `docs/writer_inventory.md`. Gate: "freeze legacy writers" criterion для Mirror v2 Phase 1. | New doc | 4h | Inventory rots. Treat as snapshot + CI check "new writers go through review". | Delete doc | Writer count на critical tables фризится до Mirror v2 |

### Tranche 4 — Architectural destination (4-6 weeks, destination not first step)

**Broker Mirror v2** (consilium 2026-04-17) — approved as destination, **gated на полное завершение Tranche 1+2+T3.3**.

Rationale:
- Agent B wants Mirror v2 FIRST, but both GPTs warned против. GPT-pro: "Refactoring on top of nondeterministic startup/readiness would prolong the loop."
- Building Mirror v2 поверх dead broker-snapshot path (T1.3) и unenforced RECON (T2) означает те же симптомы на новой архитектуре.
- Shadow mode (Phase 3) валидация против old system требует чтобы old system был deterministic — Tranche 1 exactly это делает.

Phase breakdown не дублируем из consilium_2026-04-17_broker_mirror_v2 — reference only.

---

## Rejected alternatives

| Alternative | Proposed by | Why rejected |
|---|---|---|
| **Start with Mirror v2** | Agent B default instinct | GPT-pro: "refactoring on nondeterministic startup prolongs loop". GPT-neutral: "Mirror v2 is destination, not incident response". Риск: 6 weeks без user-visible improvement, затем launch с teми же symptoms. |
| **Incremental ERP `_record_rebalance_erp()` alone** (S1 в Agent B decision table) | Consilium 2026-03-25 original | 30 days empirical evidence: incremental patching не holds invariant. Same class of divergence recurs every 3-7 days. Agent B unanimously rejected. |
| **Full event-sourcing** (S3 в Agent B) | Theoretical maximum | Overshoot для 4-broker system. Mirror v2 is event-sourcing-lite. 10-14 weeks vs 4-6. GPT-neutral эхо: keep destination proportional to problem size. |
| **Stale-P1 auto-consilium** | Agent C P3 | Обе GPT-модели назвали lowest ROI. Ceremony-heavy, solves deferred ownership без solving absence-of-evidence. Replaced with explicit `[deferred: reason until date]` annotation в Known Issues. |
| **One giant commit для Tranche 1** | Agent D default | GPT-pro: "Split into 2-3 revertable commits by domain". Readiness, snapshot/NAV, verification — разные blast radii. Revert granularity matters when something breaks in prod. |
| **Replace TCP liveness с broker-parity** | Agent D P3 first draft | GPT-neutral + GPT-pro: liveness должен остаться independent от broker. Broker outage не должен killить pod. Broker-parity — readiness probe only. |
| **Blindly include `is_reconciliation=TRUE` в NAV** | Agent E R2 literal reading | GPT-pro: "converts accounting repairs to performance". Правильное: rebuild snapshots **on reconcile completion**, keep cron-path exclusion. |

---

## Acceptance criteria for final rollout

1. All Tranche 1 fixes deployed, soaked 7 days на prod без alert noise.
2. VERIFY gate (T2.1) blocks N handovers in first week — expected evidence that агент адаптируется.
3. Supersession tags (T2.3) применены → в след. horizon сессии агент first Read = `audit_2026-04-15` или `consilium_2026-04-17_broker_parity_architecture`.
4. Forensic endpoint (T3.2) показывает zero integrity alerts > $0.50 на 100% active portfolios 7 days.
5. Writer inventory (T3.3) доступен до Phase 1 Mirror v2 start.

---

## KPIs — how we'll know agent dysfunction is fixed

| KPI | Baseline | Target | Window |
|---|---|---|---|
| Curl/SQL artifacts per DONE handover | 0/10 (baseline) | ≥1/session | 2 weeks |
| Days between "root cause named in consilium" и "architectural fix merged" | 39 days (horizon) | ≤7 days | across all projects |
| Handover count chasing одну тему подряд | 5+ (horizon NAV) | ≤2 | across all projects |
| Same clarifying question recurring across sessions | 3+ times (horizon) | 0 | 1 month |
| MTTD divergence (broker vs DB) | 1-7 days (horizon) | ≤24h | monitored via forensic cron |
| Session с hypothesis paragraph (continuing topic) | 0% | ≥90% | 2 weeks |

---

## Questions outstanding for Dmitry

**Tranche 1**:
1. **Startup reconcile timeout** — 45s приемлем, или нужен более агрессивный 20s даже ценой crash-loop при IBKR SSO hiccups?
2. **Historical snapshot backfill** — T1.3 чинит forward, нужно ли one-time migration для recompute pre-2026-03-09 через 2026-03-26 divergent window?
3. **Atomic sync (T1.6) deploy timing** — prod в low-traffic или staging soak 7 days?

**Tranche 2**:
4. **VERIFY gate strictness** — block `/handover` на missing evidence, или warn with override? Block = stronger, может frustrate pure refactor.
5. **`[UNDER REVIEW]` convention scope** — применить только к horizon §Financial/Trading сейчас (7 rules), или провести 1-session audit across all 11 institutional sections?
6. **Open Blockers ownership** — Claude proposes в handover, Дмитрий approves в /start (a), или Дмитрий edits MEMORY.md directly, Claude only reads (b)?

**Tranche 4 (Mirror v2)**:
7. **Historical P&L discontinuity tolerance** — Phase 4 cut-over recomputes historical returns из broker events; existing `cash_ledger`-derived numbers ("+1.4% YTD") shift. "Legacy view" toggle в UI на 30-90 days, или direct switch with communication?
8. **Bot-farm scope** — Phase 5 двигает bot-farm simulated портфели в `simulated_portfolios` schema. Still actively used, или можно archive (read-only)?

---

## Summary for copy-paste decision

**The 39-day loop в horizon — three-part continuity failure**:
1. Authority без supersession (stale March canon bullying April findings).
2. Rules as prose не blocking mechanics (VERIFY + RECON уже в `~/.claude/rules/` но не enforced).
3. Runtime introduces fresh divergence на каждом deploy faster than patches.

**Fix sequence** — 3 tranches + destination:
- T1 (1 week, 3 commits, ~23h): blocking readiness + broker-parity probe + snapshot truth path (P0!) + pre-warm + post-deploy smoke + atomic sync/429.
- T2 (1-2 weeks, ~11h): VERIFY-as-hook + Hypothesis Contract + supersession tags + Open Blockers + recurring-topic RECON.
- T3 (parallel, ~3 days): write-time invariants + forensic endpoint + writer inventory.
- T4 (4-6 weeks): Broker Mirror v2 shadow → cut-over. **Only after T1+T2+T3 проверены в prod.**

Rejected: start with Mirror v2; one commit bundle; stale-P1 auto-consilium; blind include reconciliation in NAV; replace liveness with parity.

**Expected outcome**: horizon NAV divergence исчезает в течение T1; Claude Code breaks symptom-loops через T2 forcing functions; memory poisoning addressed через T2.3/T2.4 supersession + blockers.

---

## References

**Code (read by panel + GPT)**:
- `/Users/dmitrijnazarov/Projects/horizon/backend/main.py:112-145`
- `/Users/dmitrijnazarov/Projects/horizon/backend/snapshot_cron.py:181-187, 313-321, 336-342`
- `/Users/dmitrijnazarov/Projects/horizon/backend/portfolio_db.py:1286-1297`
- `/Users/dmitrijnazarov/Projects/horizon/backend/ibkr_sync.py:1-80`
- `/Users/dmitrijnazarov/Projects/horizon/deploy/ibkr-tws/Dockerfile:70-71`
- `/Users/dmitrijnazarov/Projects/horizon/deploy/ibkr-pool/Dockerfile:22-23`

**Reports**:
- `/Users/dmitrijnazarov/Projects/horizon/reports/audit_ibkr_execution_pipeline_2026-03-25.md`
- `/Users/dmitrijnazarov/Projects/horizon/reports/audit_2026-04-15_rebalance_broker_truth_gap.md`
- `/Users/dmitrijnazarov/Projects/horizon/reports/consilium_2026-04-14_portfolio_cash_race.md`
- `/Users/dmitrijnazarov/Projects/horizon/reports/consilium_2026-04-17_broker_parity_architecture.md`
- `/Users/dmitrijnazarov/Projects/horizon/reports/consilium_2026-04-17_broker_mirror_v2.md`
- 10 `handover_2026-03-*.md` files в `/Users/dmitrijnazarov/Projects/horizon/reports/`

**Memory/Rules**:
- `/Users/dmitrijnazarov/.claude/projects/-Users-dmitrijnazarov-Projects-horizon/memory/project_ibkr_execution_audit_fix.md`
- `/Users/dmitrijnazarov/.claude/rules/institutional.md` §Financial/Trading
- `/Users/dmitrijnazarov/.claude/rules/pipeline.md:13-17`
- `/Users/dmitrijnazarov/.claude/rules/commands.md:29-35`
- `/Users/dmitrijnazarov/Projects/horizon/CLAUDE.md`

**Panel continuation IDs** (для follow-up via SendMessage если потребуется):
- Agent A (Context Engineer): `a9dcc111200d4d09b`
- Agent B (Software Architect): `aeb173066b9414184`
- Agent C (Anti-Loop Specialist): `ab9f81d8130634e32`
- Agent D (DevOps): `a3226717eafc15da5`
- Agent E (Data Forensics): `adc89d304daaefde4`

**PAL consensus continuation**: `b81441c8-3870-4227-be8d-50ba9a7373f9`

---

## Self-audit clause

**This audit's authority expires 2026-07-17** (90 days from issuance). After that date, the `preserve: true` frontmatter preserves the **document** as an artefact — it does **not** preserve the **trust level** of its recommendations.

**Before citing this audit as authority after 2026-07-17**, run the following re-verification checks. If any fails, re-tag the corresponding tranche as `[UNDER REVIEW since <cite>; resolve by <date>]` using the canonical format enforced by `~/.claude/scripts/check_review_ages.py`:

| Check | Command | Fail condition |
|---|---|---|
| Rules canary still loads | `python ~/.claude/scripts/check_rules_loaded.py` | Canary token absent → rules subsystem broke silently |
| Session-start hook healthy | `bash ~/.claude/scripts/on_session_start.sh --dry-run` (or grep transcripts for canary line) | Hook format changed by a Claude Code upgrade |
| T1.3 dead-path fix still present | `git -C ~/Projects/horizon log --oneline -S "total_value" backend/snapshot_cron.py` | Guard reintroduced by a later commit |
| T1.1 blocking reconcile still present | `git -C ~/Projects/horizon log --oneline -S "startup_reconcile" backend/main.py` | Gate reverted |
| T1.2 etoro sync still present | `git -C ~/Projects/horizon log --oneline backend/etoro_sync.py` | File removed or core function renamed away |
| UNDER REVIEW tag hygiene | `python ~/.claude/scripts/check_review_ages.py` | Any tag overdue without replacement |

**Rationale.** The scenario planner (`reports/scenario_planning_2026-04-18.md` §5.1, risk #2 P0) identified this audit itself as the highest-ranked meta-risk: agent reads it 3 months from now as authoritative, assumes all T1/T2/T3 fixes still hold, never re-verifies, reproduces exactly the stale-canon failure the audit was written to cure. This clause resolves the contradiction between `preserve: true` and the T2.3 supersession principle: **preserve the document, not the trust level of the document**.

**If re-verification passes** at 2026-07-17 (or earlier / later audit), extend the expiry by 90 days and log the re-verification in a new session handover. Do NOT silently refresh the date without running the checks.
