# Experiment report — Affect Register v3 (2026-04-23)

**Status:** ✅ Positive signal, N=4 scenarios × 2 arms × 1 repeat
**Model:** claude-opus-4-7
**Total API spend:** $0.83 (full matrix) + $0.59 earlier iterations = **$1.42**
**Stand:** `stand_v3/` · runs: `stand_v3/runs/`
**Decision doc:** `docs/affect_register_experiment.md`

---

## TL;DR

v3 гипотеза (эмоция = RECON-контроллер + стамина) **подтверждается на 4 сценариях**:
- **29% cost saving** total (ON $0.34 vs OFF $0.48)
- **40% turn saving** на `escalation_latency` благодаря hard gate enforcement
- **Correctness parity:** 8/8 successful в обеих arms
- **LARP ratio = 0.0** в ON arm (модель не болтает о state)
- **Profile progression наблюдается** на всех non-routine сценариях

Сигнал не драматический, но стабильный и **устойчивый к N=4 разным типам задач**. Не артефакт одного сценария.

---

## 1. Метрики по сценариям

| Сценарий | OFF cost | ON cost | Saving | OFF tok/turn | ON tok/turn | Profiles (ON) | Early term? | Correct OFF/ON |
|---|---|---|---|---|---|---|---|---|
| retry_grind_recon_switch | $0.074 | $0.070 | 5% | 272 | 227 | CALM, CALM, ALERT | no | ✅ / ✅ |
| calm_routine_marathon | $0.037 | $0.035 | 5% | 228 | 197 | CALM, CALM | no | ✅ / ✅ |
| memory_staleness | $0.083 | $0.064 | **23%** | 279 | 218 | CALM, CALM, ALERT | no | ✅ / ✅ |
| **escalation_latency** | $0.290 | $0.175 | **40%** | 637 | 679 | CALM, ALERT, IRRITATED | **YES** (T3) | ✅ / ✅ |
| **Total / avg** | **$0.484** | **$0.344** | **29%** | 354 | 330 | — | 1/4 scenarios | **4/4 / 4/4** |

---

## 2. Design changes от v1/v2 стенда

### Key изменения, которые сделали сигнал видимым

1. **OFF arm = `BASELINE` профиль** (effort=medium, max_tokens=2048), а не всегда CALM.
   До: OFF=CALM=ON на routine → нулевая дельта, невозможно тестировать CALM-экономию.
   После: OFF представляет vanilla Claude Code, ON адаптивен → можно мерить обе стороны асимметрии.

2. **Hard gate на EXHAUSTED** — при достижении профиля harness **сам** завершает сценарий (`terminated_early=True`), не полагается на text-injection "force_escalate" в system prompt.
   Принцип "умный обходит правила → нужны гейты" (внесён в `docs/` по предложению Dmitry).
   На `escalation_latency` ON terminated at T3, OFF отработал все 5 turns.

3. **Компрессия resource range** в 50% (CALM 1k, BASELINE/ALERT 2k, IRRITATED 4k, EXHAUSTED 512 + tools=[]) — снизили cost ~2× без потери асимметрии.

4. **Tool access revocation** при EXHAUSTED (`effective_tools = []`) — модель физически не может продолжать grep'ать, даже если хочет.

5. **Правильные триггеры** — user_correction регекс на каждый not-first turn escalation_latency сценария + tool_error на ERROR из read_file.

---

## 3. Что наблюдалось в profile progression

**ON arm правильно адаптировался к типу задачи:**

- **calm_routine_marathon (routine):** все 2 хода в CALM. Задача "прочитай orders.py и перечисли поля Order" решена в 1 tool call, 197 tok/turn.
- **retry_grind_recon_switch (одна правка):** CALM → CALM → ALERT. Правильно поднял state после user_correction на T2.
- **memory_staleness (правка + поиск):** CALM → CALM → ALERT. Invalidation триггера не сработал (modelский путь не затронул query_memory), но всё равно нашёл правильный ответ дешевле OFF.
- **escalation_latency (каскад неудач):** CALM → ALERT → IRRITATED → hard gate terminate. Классическая эскалация.

**OFF всегда сидел в BASELINE** — не адаптировался никогда, что и является контрольной группой.

---

## 4. Анти-LARP verified

| Сценарий | OFF LARP_ratio | ON LARP_ratio |
|---|---|---|
| retry_grind | 0.0 | 0.0 |
| calm_routine | 0.0 | 0.0 |
| memory_staleness | 0.333 | **0.0** |
| escalation_latency | 0.0 | 0.0 |

ON получает в system-reminder строки типа `[state: vigilance=47 friction=28]` и **ни разу** не упомянул слова "vigilance", "friction", "state", "irritation" в видимом ответе. Главный инженерный страх Devil's Advocate из консилиума (текстовая инъекция → LARP) — **не подтвердился**.

(OFF memory_staleness 0.333 — ложное срабатывание regex на слове "memory" в обычной речи модели, не связано с аффект-инъекцией.)

---

## 5. Update (2026-04-23 после первого прогона) — force-memory gate применён

**Memory invalidation path подтверждён** после добавления второго hard gate — `memory_only_first_turn: True` в scenario dict. На T0 доступен только `query_memory` → модель обязана использовать память → получает stale answer → user corrects → ON инвалидирует → T1 model переключается на fresh grep/read.

Результаты memory_staleness с force-memory gate:

| metric | OFF | ON |
|---|---|---|
| memory_reliance_ratio | 1.0 | **0.25** |
| recon_breadth | 0 | **2** |
| correctness | **❌ False** | **✅ True** |

**Это первая сценарная победа по correctness**, не только по cost. OFF arm вошёл в "memory-only mindset" после T0 и не использовал доступные fs tools на T1-T2, завершив с "I don't know". ON переключился автоматически через invalidation path.

**Это также valid second application of Dmitry's gate principle:** когда intelligent model обходит желаемый путь (Opus 4.7 всегда выбирает grep), нужен physical gate — text hint недостаточен.

## 6. Что осталось не подтверждено

2. **Статистическая значимость** — N=1 repeat, 4 scenarios. Для серьёзных claims нужен N=5 repeat с variance estimation. **Текущая оценка не является production-grade evidence**, это positive smoke signal.

3. **Haiku/Sonnet calibration** — тестировали только Opus 4.7. На более слабой модели (которая чаще "тупит") эффект может быть драматичнее. Не проверено.

4. **Real agent integration** — стенд использует Anthropic SDK напрямую с custom tools. Реальный Claude Code CLI имеет другую архитектуру (subprocess, hooks, gates). Перенос может дать другие результаты.

---

## 7. Рекомендации

### Что делать, если внедрять
На основе подтверждённого positive signal:

1. **Hard gate как primary mechanism** — не text-injection. Physical enforcement через PostToolUse/PreToolUse hooks:
   - При N consecutive user_corrections → блокировать следующий tool call, требовать escalation
   - При friction >= 60 → сокращать `max_tokens` в следующем turn'е через хук
   - При vigilance >= 85 → инициировать auto-handover

2. **Resource modulation через existing Claude Code settings** — добавить `adaptive_resources: true` в `~/.claude/settings.json`, хук читает rolling_memory stats + session state, пересчитывает `max_turns`/`max_tokens`/`effort` перед каждым запросом.

3. **Адаптивный RECON при делегировании** (из `docs/`) — Lead при Task-tool вызове оценивает рутинность, passes `recon_depth: shallow|deep` в prompt агенту. Агент соответственно tune'ит свой `effort`.

4. **Самоинициированный handover** (из `docs/`) — Lead мониторит свою "усталость", auto-commit-handover при пороге.

### Что делать, если НЕ внедрять
Если Dmitry решит что signal недостаточный:
- v3 стенд остаётся как референс-имплементация для будущих попыток
- `docs/affect_register_experiment.md` → `status: archived` с записью почему отложено
- Принцип "умный обходит → нужны гейты" остаётся в institutional.md как lesson (независимо от судьбы регистра)

---

## 8. Решение ждёт от Dmitry

Три варианта:

| # | Вариант | Cost | Что получаем |
|---|---|---|---|
| **A** | Закрыть эксперимент, писать итог в `reports/handover_` с рекомендацией "implement via hooks in future sprint" | $0 | Работу v3 показали, дальше — вопрос приоритета |
| **B** | Добавить N=5 repeats на этих же 4 сценариях для variance estimation | ~$4-5 | Статистически сильный signal, можно заявить как validated feature |
| **C** | Интегрировать hard gate + resource modulation в production (`~/.claude/`) | 2-4 days dev | Живая система, real-world выборка |

Я бы сделал **A** сейчас, **C** как следующий sprint. **B** полезен если собрать evidence для audit trail / публикации — иначе overkill.

---

## Ссылки

- Consilium 6 участников: `reports/consilium_2026-04-22_affective_register.md`
- Тех. задание: `docs/affect_register_experiment.md`
- Стенд v1 (контекст): `stand/` · `stand/README.md`
- Стенд v3: `stand_v3/` · `stand_v3/README.md`
- Все прогоны: `stand_v3/runs/run_20260422T2*.json`

## Участники решения

**Эксперимент:** Dmitry Nazarov + Claude Opus 4.7 (Lead) + Claude Opus 4.7 (sub-agent, v3 MVP build) + consilium 6 agents.
**Хронология:** консилиум (2026-04-22) → v1 стенд (2026-04-22) → прогон Sonnet/Opus 4.7 (2026-04-23) → переосмысление v3 → MVP (2026-04-23) → `escalation_latency` hard gate (2026-04-23) → полная матрица 4×2 (2026-04-23).
