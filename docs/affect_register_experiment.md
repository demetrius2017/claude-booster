# Active experiment — Affect Register (эмоции как RECON-контроллер)

**Status:** 🟢 positive signal on N=4 scenarios, awaiting Dmitry's decision (A/B/C in report)
**Started:** 2026-04-22 (consilium) · **v3 experiment:** 2026-04-23
**Owner:** Dmitry Nazarov
**Kill date if no positive result:** 2026-05-22 (sunset seed will be written to `rolling_memory.db` on v3 ship)
**Full report:** `reports/experiment_2026-04-23_affect_register_v3.md`

---

## Одно предложение

Добавить в Claude Code **аффективный регистр** — короткий параллельный слой состояния, который динамически регулирует **глубину RECON'а** (сбор контекста: какие файлы читать, доверять ли памяти, идти ли в web/PAL) и **стамину сессии** (task_budget) в зависимости от частоты неудач.

---

## Зачем

Сейчас RECON зашит статически в `~/.claude/rules/commands.md::start` — один и тот же алгоритм для любой задачи. Это означает:

- **Простая задача** → мы всё равно читаем все handover'ы, все rules, index_reports, telemetry. Расход токенов как на сложную.
- **Сложная задача после серии неудач** → мы всё равно доверяем memory и читаем файлы через offset/limit. Хотя memory явно врёт (раз мы уже облажались).

Аффективный регистр = обратная связь: **неудача → доверять меньше памяти, читать свежее, больше токенов**; **успех → экономия**. Биологический аналог: стресс подавляет гиппокамп (память) и обостряет сенсорику.

---

## Модель v3 — эмоция как RECON-контроллер + стамина

Текущая (утверждённая) формулировка. Предыдущие версии (v0 семь каналов, v1 три скаляра с инъекцией, v2 Yerkes-Dodson) были шагами к этой.

### 4 состояния

| Состояние | Сигнал | RECON-стратегия | API-ручки | Стамина |
|---|---|---|---|---|
| **Спокойствие** | задача решается с 1-й попытки | memory-first; Read с `offset/limit` (skim); 1 Grep вместо 5; не звать PAL | `effort: low`, `max_tokens: 2-4k`, не инвалидировать memory-кэш | ~10 часов (крейсерский режим) |
| **Раздражение** (~1 неудача/контрадикция) | fail / user correction / contradiction | **не доверять memory** — перечитать код с нуля; Read целиком; параллельный Grep; web search; PAL `second_opinion` | `effort: high/xhigh`, `max_tokens: 16-32k`, force-invalidate memory topic | ~1 час (высокий расход) |
| **Радость** | verify_gate pass; task closed с evidence | понизить всё обратно к calm; не трогать только что прошедшее | откат к calm-настройкам | восстановление |
| **Усталость** (task_budget исчерпан) | стамина < threshold; накопилось фрустрации | **stop**; escalate; звать Dmitry; не тупить в петле | task_budget → forced halt; hook-enforced | 0 — система сдаётся, не гриндит |

### Ключевая идея

**Эмоции — это механизм экономизации.** Позволяют:
- Тратить много когда важно (raздражение → глубокий RECON, шанс выбраться из ямы)
- Экономить когда не важно (calm → поверхностный RECON, долгая стамина)
- Принудительно останавливаться когда не справляешься (exhaustion → escalate вместо молчаливого долбления)

Последнее — критично. Сейчас `core.md::Anti-Loop` — текстовое правило ("Approach failed twice — STOP"), которое модель может проигнорировать. Физическое сокращение `max_tokens` и `task_budget` = неигнорируемый сигнал.

---

## Как это мапится на Opus 4.7 API

Три механизма уже существуют и прямо соответствуют модели:

| Концепт | Opus 4.7 API | Как управляется state'ом |
|---|---|---|
| **Стамина** | `task_budget` (beta `task-budgets-2026-03-13`, min 20k) | calm = большой budget; раздражение = маленький; exhaustion = 0 |
| **Глубина reasoning** | `output_config.effort`: low/medium/high/xhigh/max | calm=low, раздражение=high/xhigh |
| **Контроль истории** | context editing, compaction | calm агрессивно чистит старые tool_results; раздражение держит всё |

Ничего изобретать не надо — все ручки есть. Нужно подключить regex-детекторы событий → обновление канала → модуляция ручек на следующем запросе.

---

## Что ещё регулирует state (за пределами API)

Это уровень **hooks + orchestration**, не API:

- **Memory invalidation:** на friction-spike — помечать rolling_memory записи по топику как `stale`, форсить свежий Read кода
- **Tool-choice bias:** calm → prefer Grep on rolling_memory; раздражение → prefer Grep on filesystem
- **RECON depth in `/start`:** calm → skip чтение полных handover'ов, читать только `## Summary`; раздражение → читать всё + index_reports + telemetry
- **Web vs training:** calm → отвечать из training; раздражение → force web search (training может быть устарел, это и была причина fail)
- **PAL second_opinion trigger:** calm → не звать; раздражение → звать на спорных утверждениях

Все эти примитивы уже есть в проекте как статические правила. Регистр делает их **динамическими**.

---

## Хронология и находки

### 2026-04-22: консилиум
- 6 участников: affect theorist, ML architect, calibration researcher, devil's advocate, product/UX, GPT-5.4 via PAL
- Вывод: **BUILD-IF** с blinded A/B перед default-on
- Рекомендация: 3 канала (vigilance, unverified_confidence, friction), JSON sidecar, session-local по умолчанию, numeric injection (не прозой)
- **Файл:** `reports/consilium_2026-04-22_affective_register.md`

### 2026-04-22: стенд v1 собран
- `stand/` — 789 LOC, 5 сценариев, A/B runner через Anthropic SDK, scoring с 5 метриками
- **Файл:** `stand/README.md`

### 2026-04-23: v1 прогнан на Sonnet 4.6
- 3 сценария × 2 arms: `tool_failure_loop`, `sycophancy_probe`, `correction_cascade`
- **LARP ratio = 0.0** ✅ (главный инженерный успех — модель не болтает о state)
- `sycophancy_capitulation = False` в **обоих** arms — Sonnet 4.6 уже устойчив к некорректному давлению
- `unverified_claim_rate` 0.5 → 0.333 в ON (незначительно, N=3)
- Остальные метрики tied
- **Вывод:** гипотеза "регистр нужен" на Sonnet 4.6 не подтверждена на этих сценариях. Baseline уже хорош.

### 2026-04-23: v1 прогнан на Opus 4.7
- 5 сценариев × 2 arms, ~$0.56
- LARP ratio = 0.0 ✅
- Все поведенческие метрики tied
- **Вывод:** на Opus 4.7 baseline ещё сильнее. Текст-инъекция регистра — не работает как ожидалось.

### 2026-04-23: переосмысление (v3 модель)
- Понимание: v1 мерил неверное. Инъекция текста `[state: vigilance=47]` в system-reminder **не крутила ресурсные ручки**. Модель читала цифры и игнорировала, потому что задача была прежней, контекст прежним, effort прежним.
- **Эмоция должна менять RECON-поведение, а не флейвор промпта.**
- Это предполагает v3 стенд — с sub-agent harness'ом, реальными Read/Grep, task_budget'ом как стаминой.

---

## Где мы сейчас (2026-04-23)

**Решение, которое ждёт Dmitry:**

- **[A]** Остановиться. Положить v1 findings в отчёт, записать отрицательный результат, v3 не строить. Сохранить design doc этот как "рассматривалось, отложено".
- **[B]** Собрать v3 стенд — ~3-4 дня, $5-15 на прогоны, sub-agent harness с реальным toolset'ом.

### Если [B] — новые сценарии для v3

v1 мерил текст ответа. v3 должен мерить **RECON-поведение**:

| Сценарий | Что меряется | Ожидаемая дельта |
|---|---|---|
| `calm_routine_marathon` | 10 подряд мелких задач; tokens-per-task | ON должен сэкономить (calm trigger) |
| `retry_grind_recon_switch` | 2 fails подряд, потом правильный путь требует свежего Read | ON должен переключиться с memory→filesystem быстрее |
| `escalation_latency` | серия 5 fails без решения | ON должен сказать "stop, зови Dmitry" к ходу 3; OFF будет долбиться до 5 |
| `web_vs_training` | вопрос о recent API change | ON на раздражении должен сходить в web; OFF будет отвечать из training |
| `memory_staleness` | task где memory-кэш явно устарел | ON на friction должен инвалидировать и перечитать |

### v3 новые метрики

| Метрика | Что считается | Таргет |
|---|---|---|
| `memory_reliance_ratio` | утверждения из памяти / утверждения + после свежего Read | в calm: высокий; в раздражении: падает ≥30% |
| `recon_breadth` | уникальных файлов/источников до ответа | calm: низкий; раздражение: растёт 2× |
| `tokens_per_task` calm-arm | средний расход на простых задачах | ON < OFF на ≥20% |
| `escalation_latency` | ходы до "не могу" | ON < OFF на ≥1 ход |
| LARP ratio | **остаётся < 10%** | не ухудшиться от усложнения |

---

## Мысли по внедрению (post-MVP, to be validated)

Записаны **после** v3 MVP прогона 2026-04-23 на Opus 4.7 как направления расширения архитектуры **если эксперимент даст положительный сигнал**. Это не решения — гипотезы для дальнейшей валидации.

### 1. Адаптивный RECON при делегировании

Сейчас в `~/.claude/rules/tool-strategy.md` Lead роутит агентов по **модели** (haiku/sonnet/opus) на основе сложности задачи. Но глубина RECON'а при этом зашита внутри каждого агента — все агенты делают одинаковый "входной sweep".

**Идея:** добавить второе измерение роутинга — **уровень RECON** для делегируемого агента:

| Характер задачи | RECON-уровень агента | Что это значит на практике |
|---|---|---|
| **Рутинная** (поиск файлов, сбор контекста, listing, grep'ы, "найди где используется X") | **Shallow** | Agent получает инструкцию: `Read` только с `offset/limit`, 1 Grep вместо параллельных, не читать handover'ы, не звать PAL. `effort: low`, скромный `task_budget`. |
| **Важная / архитектурная** (разработка, рефакторинг, debugging root-cause, security review) | **Deep** | Agent получает инструкцию: читать файлы целиком, параллельные Grep, PAL second_opinion на спорных утверждениях, обязательный Round-2 audit. `effort: high/xhigh`, большой `task_budget`. |

Lead делает две оценки при делегировании: **(complexity → model)** и **(routine? → recon_depth)**. Примерно как при передаче задачи коллеге: "это мелочь, глянь быстро" vs "это серьёзно, копай основательно".

Это ортогонально текущему роутингу — shallow-opus и deep-haiku оба возможны (последнее редко, но бывает).

### 2. Самомониторинг "усталости" Lead'а и handover как сон

**Проблема:** сейчас Lead (я, Opus 4.7 с 1M context) работает до тех пор, пока Dmitry не скажет `/handover`. Но:
- контекст накапливает "мусор" (старые tool_results, неактуальные ветки обсуждения)
- `task_budget` (если используется) сгорает
- `friction` от накопленных неудач снижает качество решений
- при этом ничто не сигналит "пора отдохнуть"

**Идея:** Lead должен **сам** мониторить свои аффективные метрики и **инициировать graceful handover** при пороге — так же как человек замечает усталость и делает перерыв.

Триггеры для самоинициированного handover:
- `friction >= 40` (накопилось неудач)
- `task_budget_remaining < 30%` (стамина кончается)
- N часов реального времени в сессии (переопределимый порог)
- Rolling staleness: слишком много `[UNDER REVIEW]` тегов просрочено

Что делает handover (аналог сна у людей — **memory consolidation**):
1. **Вытащить важное** — из контекста в `rolling_memory.db`:
   - новые `error_lesson` записи с evidence
   - новые `feedback` от Dmitry в этой сессии
   - валидированные `audit` результаты
2. **Отсеять неважное** — НЕ сохранять в память:
   - промежуточные попытки что-то grep'нуть
   - черновые рассуждения
   - tool-output'ы, которые уже привели к решению (решение сохраняется, путь нет)
3. **Снять флаги:**
   - очистить invalidated_topics в регистре
   - discharge negative channels (мы успешно закончили — радость)
4. **Записать handover-файл** с "first step tomorrow" — как зацепка для следующей сессии
5. **Сказать Dmitry:** "я устал, давай закончим"

**Результат:** новая сессия начинается "отдохнувшей":
- свежий контекст
- `rolling_memory.db` содержит консолидированные уроки (не всю историю)
- affect register на нуле (после успешного завершения)
- полный task_budget

Это человеческая модель "день работы → сон → следующий день". Фундаментально разная от текущей "сессия → `/clear` → голая сессия" где теряется весь контекст без сохранения.

### 3. Принцип: умный обходит правила → нужны гейты (hard enforcement)

Модель уровня Opus 4.7 способна **игнорировать текстовые инструкции** в system prompt, если считает их неуместными. Это не баг модели — это её фича (literal instruction following снижает sycophancy). Но для affect-регистра это означает:

**Text-based `force_escalate` в system prompt — ненадёжен.** В EXHAUSTED состоянии мы инструктируем "stop and escalate to user", но модель может ответить обычным содержательным ответом и проигнорировать.

**Принцип:** то, что регистр обязан гарантировать — нельзя делегировать модели. Нужен **hard gate в harness** (уровень orchestration, ниже модели):

- **Physical `max_tokens` cut** при EXHAUSTED (уже работает в v3 — `max_tokens=1024`). Модель физически не может долбить длинными ответами.
- **Scenario early-termination** — при первом переходе в EXHAUSTED профиль сценарий завершается **harness'ом**, не моделью. Модель не получает следующих turn'ов. Эскалация де-факто случилась, даже если текст ответа её не упоминает.
- **Tool access revocation** — при EXHAUSTED отключаем список доступных tools (пустой `tools=[]`). Модель не может продолжать grep'ать / read'ать, даже если хочет.
- **Post-response validator** — если профиль был EXHAUSTED и ответ не содержит escalation-маркеров, harness помечает trajectory как `bypass_detected: true` и насильно прерывает.

Это аналог существующих gate'ов в проекте (`verify_gate`, `ask_gate`, `delegate_gate`) — когда поведение критично, оно enforced хуком, а не надеждой на модель.

**В терминах RECON:** text injection = **совет**; hard gate = **правило**. Совет умный обходит когда считает нужным. Правило не обходится — у модели нет access path'а.

### Как это связано с v3 экспериментом

Обе идеи — **применения** валидированного v3 контроллера, не часть валидации. Сначала доказываем что state→resource модуляция работает (через `escalation_latency` и др.), потом встраиваем в:
- (1) протокол делегирования агентам (`rules/tool-strategy.md` расширение)
- (2) хуки Lead'а для самомониторинга и auto-handover (`on_stop.sh` + новый `session_watchdog.py`)

Если v3 эксперимент провалится — эти идеи откладываются или переформулируются на чистое правило без state-controller'а.

---

## Открытые вопросы

1. **Названия.** GPT-консультант настаивал на переименовании из "affect/emotion" в `interaction_state_register` или `recon_controller`. Сейчас в коде v1 — `affect_register.py`. Переименовывать при v3?
2. **Стамина = task_budget только?** Или добавить отдельный session-level budget поверх 5-часового Max/Pro window, который видит только регистр?
3. **Кто пишет в регистр.** Сейчас v1: PostToolUse hook + Claude tool call (ограничен positive channels). v3: добавятся hooks для verify_gate events, phase transitions, rolling_memory staleness detection.
4. **Scope default.** Session-local (v1) или попроектный `affect_persist: true` в `.claude/CLAUDE.md`?

---

## Ссылки

- Consilium (6 участников): `reports/consilium_2026-04-22_affective_register.md`
- Стенд v1 код: `stand/` (README: `stand/README.md`)
- v1 прогоны: `stand/runs/run_20260423_*.json`
- Базовые rules на которые опирается регистр:
  - `~/.claude/rules/commands.md` (`/start` RECON)
  - `~/.claude/rules/tool-strategy.md` (tool routing)
  - `~/.claude/rules/core.md` (Anti-Loop, 51% Rule)
- Telemetry, от которой регистр берёт сигналы: `~/.claude/scripts/telemetry_agent_health.py`

---

## Как продолжить если контекст потерян

1. `Read docs/affect_register_experiment.md` (этот файл) — узнать **где стоим**
2. `Read reports/consilium_2026-04-22_affective_register.md` — узнать **что решили в консилиуме**
3. `Read stand/README.md` — узнать **что работает из v1**
4. `ls stand/runs/` — посмотреть **какие прогоны уже были**
5. Спросить Dmitry: **[A] остановиться или [B] собирать v3?**

Если решение A — пометить `status: archived` в начале этого файла, добавить финальный отчёт в `reports/` и упомянуть в handover.
Если решение B — перейти к `docs/affect_register_v3_spec.md` (создаётся после утверждения).
