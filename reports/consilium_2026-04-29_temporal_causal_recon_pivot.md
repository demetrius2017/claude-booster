---
name: "Consilium 2026-04-29 — Temporal-causal recon + stuck-loop pivot trigger"
description: >
  Five-agent consilium (4 internal Claude bios + GPT-5.5 external via PAL) on
  the gap: Claude reads memory/reports as flat 2D facts at /start, never
  reconstructs premise→action→outcome, never notices "stuck-loop" (real
  example: verify_gate v1.5 FP open 11 days, surfaced 6× without alert).
  Five-way consensus: ship a minimal stuck-loop detector with a
  normalized-keyword-set hash (label-drift-resilient) PLUS the
  Premise/Tried/Result/Open read-side prompt block this week. Defer schema
  migration, LLM problem_hash, and hard /start block to v2 conditional on
  detector earning them.
type: consilium
scope: global
preserve: true
---

# Consilium 2026-04-29 — Temporal-causal recon + stuck-loop pivot trigger

## 1. Task context

**Dmitry's framing (paraphrased):** Claude собирает контекст плоско, как 2D
память. Не реконструирует таймлайн "что было в основе → что делали → что по
итогу". Не сверяет память/отчёты с кодом критически. Иногда тупо продолжает
залипшую траекторию вместо вопроса "а проблема не в корне ли?". Нужно: каждый
recon (start/consilium/audit/делегация) реконструирует причинно-временную
цепочку И триггерит pivot-вопрос когда детектируется stuck-loop.

**Empirical anchor:** `verify_gate v1.5 newest-block-wins` FP — surfaced
2026-04-18, "First step tomorrow" в 6+ хендоверах под **разными лейблами**
("v1.5 FP", "newest-block-wins", "verify-gate hardening", "audit follow-up").
11 дней, 0 фиксов, 0 алертов. Это и есть failure mode, который должен ловиться.

**Verified Facts Brief (code-checked перед консилиумом):**

- `~/.claude/rolling_memory.db.agent_memory` schema v5 уже имеет: `created_at`,
  `status (active|under_review|superseded)`, `superseded_by_id`, `verified_at`,
  `resolve_by_date`. **Нет** `topic_key`/`parent_id`/`problem_hash`. 161 active
  rows. FTS5 индекс на content+memory_type+category+scope.
- `/start` (commands.md шаг 2) читает: README + handover Summary+First-step +
  `rolling_memory.py start-context` — flat dated list of consilium/audit
  titles. **Никакой реконструкции таймлайна.**
- `telemetry_agent_health.py` имеет 5 сигналов: evidence density, N/A ratio,
  overdue [UNDER REVIEW], stale citations, cadence. **Нет stuck-loop.**
- Прецеденты, которые агенты читали: `audit_2026-04-17_agent_context_dysfunction.md`,
  `consilium_2026-04-18_memory_rearchitecture.md`, `agent_dysfunction_flow_2026-04-18.html`.

## 2. Agent positions

| # | Bio / Model | Position (одна фраза) | Schema delta | Forcing function | KPI |
|---|---|---|---|---|---|
| **A1** | Cognitive architect / **Opus 4.7** | `topic_key` обязательный + опциональный `parent_id`; narrative derive-on-read | `ALTER TABLE` add 2 cols + index | Read-time `topic-trace` subcommand | cite-the-chain rate ≥80%; premise-shift retract ≥1/мес/topic |
| **A2** | IR/RAG engineer / **Sonnet 4.6** | Никаких write-time колонок; auto-cluster по FTS5 от First-step keyworded handover | None | Premise/Tried/Result/Open prompt-block в /start | timeline inclusion ≥80%; stale-flag ≤24h; recurrence → 0 за 4 недели |
| **A3** | Red-team / pivot detector / **Opus 4.7** | LLM-extracted `problem_hash`; **hard /start block** на stuck; Causal Re-evaluation Block как PreToolUse-gate на EnterPlanMode | Add `problem_hash` col | Hard block + 4 forced reframe-вопроса pivot-консилиума | stuck-flag ≤72h после 3-го; FP <10%; ≥40% pivot→SUPERSEDED |
| **A4** | Pragmatist / minimum delta / **Sonnet 4.6** | Только `_stuck_loop()` (+45 LOC) + `--stuck-check` (+30 LOC); первые 80 байт First-step как hash; всё остальное defer | None | Soft signal в telemetry + prose в start-context | stuck signal fires ≥1× в 10 sessions на repeating-проекте |
| **A5** | External tie-breaker / **GPT-5.5** (via PAL `thinkdeep`) | A4 sequencing верный, но A4's hash сломается на label drift; апгрейд хэша до normalized-keyword-set + ship A2 prompt-block параллельно | None (week 1) | Advisory only week 1; promotion to hard block conditional on detector earning it | 2-week и 4-week review с явными promotion-criteria |

## 3. Key tensions and how the consensus resolves them

**T1. Schema-vs-no-schema** — A1, A3 «ALTER TABLE сейчас», A2, A4, A5 «нет».
**Resolved:** нет в week 1. Пользу `topic_key` нельзя посчитать пока детектор
не покажет, что cheap-hash недостаточен. Schema-change без данных = silent
заявка с 161 NULL-значением.

**T2. Hard block vs soft surfacing** — A3 «hard», A4 «soft», A2/A5 «structured
prompt». **Resolved:** advisory only в week 1. A3 правильно говорит, что soft
banners decay в прозу, **но** hard-block без empirically-доказанного детектора
сам станет шумом и его обойдут (CLAUDE_BOOSTER_SKIP_*=1 escape hatch
прецедент). Promotion to hard block — после ≥2 confirmed-loops + ≥1 missed
case подтверждающий, что cheap-hash дошёл до потолка.

**T3. Topic detection — write-time tag vs auto-cluster vs problem-hash vs
file-level repetition.** **Resolved (это ключевой инсайт GPT-5.5):**
A4's «hash первых 80 байт First-step body» сломается на verify_gate-кейсе,
потому что лейблы дрейфовали 4 раза. **Промежуточный приём** — детерминированный
normalized-keyword-set hash:
1. lowercase, unicode-normalize, strip markdown
2. tokenize по `[a-z][a-z0-9_./:-]{2,}` (сохраняет `verify_gate`, `v1.5`, file paths)
3. drop минимальный stopword-set (the/and/issue/fix/problem/tried/working — но НЕ false/positive/gate/timeout/auth)
4. отсортировать unique tokens, взять top 16-32, sha1 от canonical-joined
5. **логировать и hash, и canonical_tokens** в telemetry — иначе debug коллизий невозможен.

Этот ход даёт ≈80% пользы A3's problem_hash без LLM, без schema, без
ALTER TABLE. Если упрётся в потолок — promote.

**T4. EnterPlanMode PreToolUse gate (A3's Causal Re-evaluation Block) и phase
machine.** **Resolved:** не ставим эту преграду в week 1. Phase machine уже
несёт `RECON → PLAN → IMPLEMENT → AUDIT → VERIFY → MERGE` — добавление ещё
одного PreToolUse-блока на EnterPlanMode даст hook-fatigue. Если и появится —
то как часть phase_gate.py для перехода RECON→PLAN, не отдельный hook.

**T5. Big-bang vs ship-and-iterate.** **Resolved:** ship-and-iterate с явным
gate'ом на promotion. См. §5.

## 4. Decision (5/5 consensus shippable plan)

### Week 1 — Ship

**S1. Normalized-keyword stuck-loop detector** (~120 LOC, замена A4's подхода
с апгрейдом из A5):

- New: `~/.claude/scripts/stuck_loop_key.py` (~50 LOC) — pure deterministic
  function `make_stuck_loop_key(text, context_anchors=()) -> {hash, tokens, canonical}`.
  Stdlib only. Unit-tested на 4 verify_gate-вариантах (label-drift fixture).
- Modified: `~/.claude/scripts/telemetry_agent_health.py` (+45 LOC) — `_stuck_loop()`
  signal: extract First-step body из last 5 handover'ов, прогнать через
  `make_stuck_loop_key()`, fire когда тот же hash ≥3 раз и нет verify_gate=pass
  evidence на этот hash в окне.
- Modified: `~/.claude/scripts/rolling_memory.py start-context` (+25 LOC) —
  `--stuck-check` flag, surfaces "STUCK LOOP CANDIDATE" prose с topN
  canonical_tokens (для debug коллизий).
- Modified: `~/.claude/rules/commands.md` /start step 2 (+8 LOC) — when stuck signal:
  Claude обязан перед EnterPlanMode прочитать связанные хендоверы и явно
  ответить на «что мы пробовали / почему не сработало / альтернативная
  гипотеза». Текстовая discipline, не hook-block.

**S2. Premise/Tried/Result/Open read-side prompt block** (~80 LOC, A2's design):

- Modified: `~/.claude/scripts/rolling_memory.py` — `build_topic_timeline()`
  внутри `_fetch_start_context`. Topic-keywords извлекаются из First-step
  + `git status` paths regex'ом (без LLM). FTS5 query на consilium/audit ROWS,
  merge с recency floor (last 21d), sort ASC by created_at, prune до 6 rows.
- Output shape (вставляется ПЕРЕД flat list в /start context):
  ```
  === TOPIC TIMELINE: "<keywords>" ===
  Premise (date1): <consilium/audit row title>
  Tried   (date2): <next row>
  Result  (date3): <…>
  Current (date4): <last row>
  OPEN: <derived from missing verify_gate=pass>
  ⚠ STALE-TOPIC if span ≥14d AND ≥3 unresolved (link to S1 detector)
  ```
- Token budget delta: +300-400 tok at /start. В пределах нормы (5K → 5.4K).

**S3. Telemetry promotion-criteria stub** (~15 LOC):

- New: `~/.claude/scripts/stuck_loop_review.py` — за 2 и 4 недели печатает
  detector_fired_count / human_confirmed / human_dismissed / missed_count
  (manual annotation в handover'е).

### Deferred to v2 (conditional)

- **A1's `topic_key`/`parent_id` schema columns** — only if S2's auto-cluster
  показывает >20% коллизий ИЛИ Dmitry хочет explicit narrative-graph для
  cross-project queries.
- **A3's LLM-extracted `problem_hash` + hard block** — only if S1's detector
  показывает (a) ≥2 confirmed loops AND (b) ≥1 missed case where cheap hash
  не сработал AND (c) advisory mode не уменьшил recurrence rate.
- **A3's Causal Re-evaluation Block as PreToolUse gate** — only after phase_gate
  refactor (если решим встроить в RECON→PLAN transition).

### Explicit promotion criteria for v2 (per A5)

Promote если ≥1:
1. Detector caught ≥2-3 genuine repeated-loop cases (Dmitry confirms in review).
2. Detector missed a high-value loop из-за semantic paraphrase, который
   cheap hashing не ловит.
3. Collision rate стал болезненным (>20% advisory-fires оказались разными
   topic'ами с тем же hash'ом).
4. Repeated failures настолько дорогие, что advisory не достаточно.

DO NOT promote если:
1. Most firings — noisy references, не loops.
2. S2 prompt-block один уменьшил recurrence (тогда детектор лишний).
3. Не можем чётко сказать, какое решение `topic_key`/`problem_hash` бы driver'ил.

## 5. Rejected alternatives (with reasons)

| Rejected | Why |
|---|---|
| A1's `ALTER TABLE` для `topic_key` сейчас | 161 NULL-значений — дешевле сначала проверить, что cheap-hash недостаточен. Backfill через `_unclassified` — debt без пользы. |
| A1's heuristic backfill script | 30 nouns vocabulary без empirical-grounding в реальные хендоверы. Сначала собрать топ-fired tokens из S1 за 2 недели — тогда vocabulary будет data-driven. |
| A3's LLM problem_hash при write | Cost (LLM call per memorize), latency, prompt drift, opaque second classifier до того, как у нас есть baseline для сравнения. |
| A3's hard /start block | Advisory заслуживается доказанным детектором. Hard block на unproven detector → escape hatch → прецедент `CLAUDE_BOOSTER_SKIP_*` подтверждает паттерн. |
| A3's Causal Re-evaluation PreToolUse gate на EnterPlanMode | Phase machine уже несёт recon-discipline. Дополнительный hook → fatigue. Если нужно — встроить в phase_gate, не плодить hook'и. |
| A4's «hash первых 80 байт First-step body» (без апгрейда) | **Сломалось бы на verify_gate-кейсе.** Лейблы дрейфовали 4 раза за 11 дней. Counter ресетнулся бы 4 раза. Это empirical proof, что naive string hash недостаточен. |
| A2's pure read-time clustering без stuck-detector | Prompt-block помогает Claude'у видеть chain, но не сигналит «мы залипли». Это complement, не substitute. |

## 6. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Normalized-keyword hash коллизии (разные topic'и → один hash) | Medium | Логируем canonical_tokens в audit log → manual review за 2 недели → если >20%, добавляем coarse anchor (project_root + memory_type) в hash material. |
| Stopword-set оказывается subjectively-skewed (выкинули важное слово) | Medium | Хранить stopwords в отдельной 1-line константе, легко править. Tests на verify_gate-fixture гарантируют, что 4 относ. drift'а коллапсятся в один hash. |
| S2's auto-cluster fragments topic под label drift (пропуск, не коллизия) | Medium | S2 берёт first-step из ПОСЛЕДНЕГО handover'а как seed. Его лейбл = текущий лейбл — не дрейфует относительно "сейчас". Старые упоминания подтянутся через FTS5. |
| Token budget at /start вылез за +400 | Low | `wc -c` в smoke-test'е, hard-cap +500 на сборку. |
| Detector не fire'ит ни разу за 4 недели → решение «нечего promote'ить» неоднозначно | Low (verify_gate уже залипший — fire'ит на week 1) | Если 4 недели тишины: либо проблема рассосалась (good), либо детектор сломан → review fixtures, проверить unit-tests. |

## 7. Implementation order (week 1)

1. **stuck_loop_key.py + unit-tests** на verify_gate-fixture (4 label-drift вариантов → один hash). ~2h.
2. **telemetry_agent_health.py** `_stuck_loop()` signal. ~1h.
3. **rolling_memory.py start-context** `--stuck-check` + `build_topic_timeline()` + Premise/Tried/Result/Open render. ~3h.
4. **commands.md /start** step 2 update — text discipline на stuck-fire. ~10 min.
5. **stuck_loop_review.py** stub. ~30 min.
6. **Smoke test:**
   ```bash
   python3 ~/.claude/scripts/telemetry_agent_health.py        # stuck_loop: 1/5 ✓ или ⚠
   python3 ~/.claude/scripts/rolling_memory.py start-context --scope . --stuck-check
   python3 -m pytest ~/.claude/scripts/tests/test_stuck_loop_key.py
   ```
7. **Verify-gate evidence block** на handover commit — exit=0 на каждом из 3 шагов выше.

Total: ~7h. Ship-able за один рабочий день.

## 8. KPI (review at 2 and 4 weeks)

| Metric | Baseline | Target (4w) | Measurement |
|---|---|---|---|
| Stuck signal fires on verify_gate-class topics | 0 (current) | ≥1 within 14d | telemetry log |
| `Premise/Tried/Result/Open` block рендерится при /start на continuing topic | 0% | ≥80% | grep `=== TOPIC TIMELINE ===` в transcript'ах |
| Same problem-hash появлений в handover First-step | 6 (verify_gate baseline) | ≤2 | hash-grep по reports/handover_*.md |
| Token delta /start | 5K | 5.0-5.5K | `wc -c` |
| FP rate (manual review) | N/A | <30% (week 1 acceptable, до promotion <10%) | weekly Dmitry annotation |
| Coverage: % of /start sessions with detector run | 0 | 100% | telemetry exit=0 |

## 9. Vote tally

- **A1 (architect, Opus):** keeps schema-edge proposal as v2-conditional. Did not block.
- **A2 (IR/RAG, Sonnet):** voted FOR — Premise/Tried/Result/Open block ships in S2.
- **A3 (red-team, Opus):** voted FOR week 1 plan, AGAINST removing pivot-questions entirely (resolved by S1 step 4 — text discipline на stuck-fire включает 4 reframe-вопроса).
- **A4 (pragmatist, Sonnet):** voted FOR — sequencing won. Hash upgrade принят как 10-LOC delta к 75 LOC.
- **A5 (GPT-5.5, external):** voted FOR — explicit promotion-criteria gate accepted. Required: log canonical_tokens, не только hash.

**Consensus: 5/5 with A5's hash upgrade adopted. No dissents.**

## 10. Next-session first step

```bash
cd ~/Projects/Claude_Booster
# Phase: RECON (advance: python3 ~/.claude/scripts/phase.py set RECON)

# 1. Создать stuck_loop_key.py + tests с verify_gate-fixture (4 варианта лейблов)
mkdir -p ~/.claude/scripts/tests
# Реализация по §7 step 1

# 2. Дальше — telemetry, start-context, commands.md как в §7
```
