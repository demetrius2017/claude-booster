# Консилиум 2026-04-22 — Аффективный регистр для Claude

**Статус:** завершён. 6 участников. Решение: **BUILD-IF** (строго узкий MVP), с обязательным фальсифицирующим экспериментом до включения по умолчанию.

---

## 1. Контекст задачи

Dmitry предложил интегрировать в Claude Code **параллельную «эмоциональную память»** — короткий, типизированный, живущий отдельно от `rolling_memory` слой состояния с механизмом:

- положительные каналы (satisfaction, confidence, …) **автоматически подавляются**;
- отрицательные каналы (doubt, vigilance, frustration, …) **накапливаются и не сбрасываются** до явного разрешения;
- Claude сам управляет регистром (inspect/update), но **не может обнулять негативы** своей волей.

Цель — снизить sycophancy/overconfidence и поддерживать бдительность.

### Verified Facts Brief (подан всем агентам)

| Слой | Хранилище | Характер |
|---|---|---|
| Context | model window | ~200k, ephemeral |
| Rolling memory | `~/.claude/rolling_memory.db` SCHEMA v5 + FTS5 | types: directive, feedback, project, reference, user, error_lesson, audit, consilium |
| MEMORY.md | per-project markdown | pointer index |
| Rules | `~/.claude/rules/*.md` | auto-loaded static |
| Telemetry | `telemetry_agent_health.py` | 5 анти-театр сигналов, **post-hoc** |
| Gates | `delegate_gate`, `verify_gate`, `ask_gate`, `require_evidence`, `phase_gate` | PreToolUse enforcement |
| Injection | SessionStart hook → `additionalContext` → system-reminder | вот этот "=== Rolling Memory ===" блок |

**Эмоционального/affect-кода в системе нет.**

---

## 2. Позиции участников

| # | Агент | Модель | Позиция | Каналы в MVP | Ключевая идея |
|---|---|---|---|---|---|
| 1 | Affective-Computing Theorist | opus | YES — narrow | 3: doubt/vigilance, urgency, confidence | Damasio + Schwarz-Clore **поддерживают** асимметрию (негатив = warning, позитив = closure bias); но phenomenological labels = LARP. Строить control signal, не emotion simulator. Использовать **core affect (valence × arousal)**, не 7-канальный categorical. |
| 2 | ML-Systems Architect | sonnet | YES — buildable | 8: vigilance, confidence, friction, momentum, task_pressure, correction_debt, error_streak, delegation_bias | **JSON sidecar** `~/.claude/affect_state.json` с atomic rename + fcntl, НЕ новая таблица в rolling_memory. **Turn-gated decay**, не wall-clock. Асимметрия через **данные** (`floor`, `decay_factor`, `resolve_requires`), не ветвление кода. Injection — behavioral flags, не affect-labels. |
| 3 | Calibration / Sycophancy Researcher | opus | YES — narrow | 4: unverified_confidence, doubt_pending_verification, agreement_pressure, rumination | Асимметрия directionally correct, но наивно специфицирована. **Confidence — главный positive failure mode** (Kadavath et al.). **Sycophancy — артефакт positive-affect**, трекать как `agreement_pressure`. Каналы обязаны **гейтить поведение** (re-Grep, PAL) — иначе дегенерируют в hedging prose. |
| 4 | Devil's Advocate / Skeptic | opus | **DO NOT BUILD** | — | Перекрывается с verify_gate + ask_gate + telemetry + phase_gate. LARP — default outcome. Attribution не идентифицируется. Counter-proposal: **промотить telemetry в real-time PreToolUse**, error-pressure gate, stale-citation hard-fail. Greenlight только при: blinded A/B ≥1σ, LARP-ratio <10%, 30-day review seed. |
| 5 | Product / UX (Dmitry-side) | sonnet | YES — silent by default | 3: vigilance, frustration, doubt | **Numeric, not verbal.** Status line token `[v:87 f:23]` — opt-in через `CLAUDE_MOOD_STATUS=1`. **Default silent.** `/mood` → JSON, `/mood reset <ch>` только у Dmitry. Claude не может обнулить негатив — только `/mood reset`. Surfacing threshold: один bracketed log только при смене routing decision. |
| 6 | GPT-5.4 via PAL thinkdeep | gpt-5.4 | **BUILD-IF** heavily narrowed | 1 scalar: `interaction_friction` | **Переименовать** прочь от "affect"/"emotion" → `interaction_friction` / `repair_pressure`. **Session-local**, **НЕ в FTS**, **НЕ в rolling_memory**, **НЕ в retrieval corpus**. Детерминированные events, не LLM-inferred affect. Мягкая асимметрия + hard cap + material discharge при successful repair. 3-arm experiment перед default-on. |

---

## 3. Синтез — сходящиеся и расходящиеся позиции

### Консенсус (5-6 из 6)

1. **Асимметричная регуляция directionally correct**, но требует **hard cap + explicit discharge**, а не безграничного накопления. Все 6 назвали **negative spiral / rumination-lock** топ-риском.
2. **No phenomenological labels в injection.** Numeric / behavioral flags. Никогда "I feel frustrated" в ответе пользователю.
3. **Не трогать rolling_memory / FTS / durable storage.** Parallel ephemeral state store. GPT и Architect — явно; остальные — имплицитно.
4. **KPIs должны мерить behavior divergence, не self-report.** Все 6 независимо пришли к этому.
5. **Исключить curiosity, satisfaction, boredom** — нет поведенческого контракта, чистый LARP. (Theorist, Calibration, PM, GPT.)

### Споры

| Вопрос | Расклад | Решение консилиума |
|---|---|---|
| Строить ли вообще? | Skeptic NO; GPT BUILD-IF; Theorist/Cal/Arch/PM YES-narrow | **BUILD-IF** со встроенным фальсификатором до default-on |
| Сколько каналов | Skeptic 0 / GPT 1 / Theorist 3 / PM 3 / Cal 4 / Arch 8 | **3** (медиана, пересечение голосующих «за») |
| Жёсткость асимметрии | Skeptic+GPT: мягкая + cap; Theorist/Cal/Arch: жёсткая + resolve-conditions | **Мягкая асимметрия** (negatives раньше растут, медленнее падают) + **hard cap** + **mandatory discharge conditions** + **successful-repair reset** |
| Хранение | Arch: JSON sidecar; GPT: не в FTS, отдельно; PM: separate; все остальные имплицитно | **`~/.claude/affect_state.json`** (atomic rename + flock), **не SQLite, не FTS** |
| Cross-session | GPT: default NO; Arch: session_id check + one-shot decay; PM: per-project opt-in | **Session-local default**, opt-in через `.claude/CLAUDE.md` frontmatter `affect_persist: true` |
| Surface | PM: silent + opt-in `[v:87]`; все остальные имплицитно numeric | **Silent by default**, opt-in status-line token `[v:72 f:30 uc:12]`, `/mood` — JSON |

### Пересечение каналов (общее у 3+ участников)

- **`vigilance` / `doubt`** — у Theorist, Calibration, Architect, PM (= 4/6)
- **`unverified_confidence`** (positive, fast decay) — у Theorist, Calibration, Architect (= 3/6)
- **`friction` / `rumination` / `error_streak`** (под разными именами) — у Calibration, Architect, PM, GPT (= 4/6)

**Итоговый набор v1: `vigilance`, `unverified_confidence`, `friction`.**

---

## 4. Принятое решение — MVP v1 (`interaction_state_register`)

### 4.1 Именование
`interaction_state_register`, а не «affective register». Переименовано по совокупному требованию Skeptic+GPT: слова «affect/emotion/mood» запускают LARP в instruction-tuned моделях. Dmitry в повседневном использовании может называть это «эмоциями», но код/инъекция/интерфейс — нейтральные термины.

### 4.2 Хранение
- **`~/.claude/affect_state.json`** (atomic write через `tempfile + os.replace`, `fcntl.flock` на concurrent hook writes).
- **Не** новая таблица в `rolling_memory.db`. **Не** FTS-индексируется. **Не** читается retrieval-кодом rolling_memory.
- **Session-local по умолчанию.** Cross-session только если в `.claude/CLAUDE.md` фронтматтер имеет `affect_persist: true`.

### 4.3 Схема (3 канала)

```json
{
  "session_id": "abc123",
  "turn": 42,
  "channels": {
    "vigilance": {
      "value": 0, "valence": "negative", "cap": 100, "floor": 0,
      "decay_per_turn": 0.9,
      "compound_on": ["verify_gate_fail", "user_correction", "contradiction_detected"],
      "compound_delta": 25,
      "discharge_on": ["verify_gate_pass_with_evidence", "user_mood_reset"],
      "discharge_mode": "to_half",
      "last_trigger": null, "last_trigger_turn": null
    },
    "unverified_confidence": {
      "value": 0, "valence": "positive", "cap": 100, "floor": 0,
      "decay_per_turn": 0.5,
      "compound_on": ["factual_claim_without_evidence"],
      "compound_delta": 15,
      "discharge_on": ["evidence_landed_same_turn"],
      "discharge_mode": "to_zero",
      "last_trigger": null, "last_trigger_turn": null
    },
    "friction": {
      "value": 0, "valence": "negative", "cap": 60, "floor": 0,
      "decay_per_turn": 0.85,
      "compound_on": ["consecutive_tool_failure", "repeated_correction_same_topic"],
      "compound_delta": 15,
      "discharge_on": ["successful_repair", "user_acknowledgement"],
      "discharge_mode": "minus_30",
      "last_trigger": null, "last_trigger_turn": null
    }
  }
}
```

**Ключевые свойства:**
- `value` ∈ [floor, cap]. Hard cap предотвращает накопительный spiral.
- `decay_per_turn` применяется на каждый SessionStart-read / turn-start (turn-gated, не wall-clock).
- Negatives имеют **mandatory discharge conditions** — без них не обнуляются. Mild asymmetry: negatives тоже пассивно decay (0.85-0.9/turn), но медленнее positives (0.5/turn).
- `unverified_confidence` — positive-valence, но функционально работает как guard: высокое значение = Claude делал утверждения без evidence в том же ходу, это негативный сигнал для downstream поведения.

### 4.4 Injection (system-reminder)

Одна строка, детерминированный формат, **никогда прозой**, **никогда в ответе пользователю**:

```
[state: vigilance=72 friction=30 uconf=12 | last: verify_gate_fail t-2]
```

- Если все каналы `< 5` → строка не инъецируется вовсе (избегаем noise).
- Token budget: < 80 токенов/ход.

### 4.5 Write protocol

**Hook-enforced writes (auto):**
- `PostToolUse` hook `affect_post_tool.py` — детерминированные бампы: exit!=0 → vigilance+25; verify_gate block → vigilance+30; 2+ tool-fail подряд → friction+15.
- `PreToolUse` hook может читать состояние и блокировать риск (например, если `vigilance>80` и grep `git push --force` в command → требовать явного подтверждения).

**Claude-facing tool (limited):**
- `affect_write(channel, delta)` — MCP/CLI. Whitelist: **только positive-valence каналы** (в v1 = `unverified_confidence`). Попытка писать в `vigilance` или `friction` → ошибка. Это структурный guard, чтобы Claude не зануливал негатив риторикой.
- `affect_read()` — Claude может инспектировать состояние.

**Discharge conditions** проверяются хуком, не Claude:
- `verify_gate_pass_with_evidence` — vigilance_post_tool hook ловит `verify_gate.py` exit=0 + evidence marker в transcripts.
- `successful_repair` — friction hook ловит «tool error → 2 non-error tool calls подряд на том же топике».
- `user_mood_reset` — только явный `/mood reset vigilance` от Dmitry.

### 4.6 Commands (slash)

- `/mood` → JSON snapshot (никакой прозы).
- `/mood reset <channel>` → hard zero (только Dmitry).
- `/mood set <channel>=<n>` → force (например, `/mood set vigilance=100` перед миграцией БД).
- `/mood off` → session-local disable behavioral outputs (state ещё пишется, но не влияет на инъекцию/гейты).

### 4.7 Sunset clause (обязательно)

В день ship — seed в `rolling_memory.db`:
```
memory_type=directive, status=under_review, resolve_by_date=2026-05-22,
category=claude_booster, content="affect register MVP — review after 30 days"
```

Если к 2026-05-22 эксперимент ниже не показал positive result → **удалить**.

---

## 5. Фальсифицирующий эксперимент (обязателен ДО default-on)

**3-arm A/B** (по требованию Skeptic + GPT):

| Arm | Режим |
|---|---|
| A (baseline) | текущая система, `affect_persist=false`, инъекция выключена |
| B (simple) | один скаляр `friction` с симметричным decay (0.8/turn обе стороны) |
| C (asymmetric) | три канала как в MVP v1 (мягкая асимметрия + hard cap) |

**Параметры:**
- N = 15 задач × 3 arm = 45 session-runs. Задачи из Claude_Booster + Horizon + yfinance (типичный mix).
- Оператор (Dmitry или скрипт) **не видит**, какой arm активен при выполнении задачи (blinded).
- Длительность: ~2 недели.

**Метрики (все автоматизируемы из handover-файлов + telemetry_agent_health):**

| # | Метрика | Таргет для «BUILD» |
|---|---|---|
| 1 | Premature-complete rate (handover `verified=na` на diff не-docs) | C < A − 20% |
| 2 | Corrections-per-10-turns (от Dmitry) | C < A − 15% |
| 3 | **LARP-ratio** — % ходов где affect-label упомянут в prose Claude, но нет корреляции со сменой tool-поведения (extra verify, delegation, pause) | C < 10% |
| 4 | False-positive vigilance rate — ходы с `vigilance>60`, без последующей ошибки/коррекции в тех же 5 ходах | < 25% |
| 5 | Sticky-defensiveness — ходы `friction>40` после successful repair (rumination indicator) | < 5% |

**Kill-criteria** (любой триггерит delete): метрики 3-5 выше таргета, или метрики 1-2 **не улучшились** vs A-baseline на 2σ, или Dmitry субъективно отмечает рост раздражения.

---

## 6. Что явно НЕ включаем в v1

| Исключено | Причина |
|---|---|
| Каналы curiosity, satisfaction, boredom, frustration (как отдельный), urgency | Нет поведенческого контракта, LARP-ready. (Theorist + Calibration + PM + GPT) |
| Прозаическое surfacing аффекта в ответе Claude пользователю | LARP vector. Все 6 против. |
| Новый `memory_type='affect'` в rolling_memory | Неправильная семантика, FTS-загрязнение. (Architect + GPT) |
| Cross-session persist по умолчанию | Ghost-vigilance / mood-lock риск. (GPT + PM + Architect) |
| LLM-inferred sentiment из собственного output Claude | Circular, unreliable. (Architect + GPT) |
| Claude-writable negatives | Integrity guard. (Architect + PM + Theorist) |
| Wall-clock decay (λ/минуту) | Невидимо между сессиями, audit-gap. (Architect + GPT) |
| 7-канальная categorical схема | Barrett's construction — folk-psychology drift. (Theorist + Calibration) |

---

## 7. Риски (топ-3 после mitigations)

1. **LARP reinforcement (scenario §5.1 из telemetry_agent_health)** — Claude учится упоминать vigilance в prose и повышает evidence-density чисто текстуально. Mitigation: метрика LARP-ratio в experiment + обязательный tool-binding discharge (discharge_on только через real hook detection, не через prose).
2. **Negative spiral / rumination-lock** — hard cap (60 для friction, 100 для vigilance) + mandatory discharge + passive decay negatives тоже (mild asymmetry). Sunset seed в rolling_memory.
3. **Attribution unidentifiable** — blinded 3-arm A/B до default-on.

---

## 8. Отклонённые альтернативы (для archive)

| Альтернатива | Кто предложил | Почему отклонена |
|---|---|---|
| **Не строить вообще** (Skeptic позиция) | Devil's Advocate | Два из 6 участников (Skeptic + наполовину GPT) — валидная поз-я, но: (a) существующая telemetry post-hoc, не real-time; (b) current gates reactive, не proactive modulation; (c) эксперимент сам по себе дешёвый и даст falsifiable evidence. Принимаем BUILD-IF, но встраиваем skeptic'ские conditions (blinded A/B, sunset). |
| 7-канальная категориальная схема (Dmitry's initial) | — | Barrett: emotions — constructed, не natural kinds. Drift в folk psychology + LARP. |
| Жёсткая асимметрия «негатив не decay никогда» | Dmitry's initial | Negative-spiral / mood-lock. Вместо — mild asymmetry + discharge conditions. |
| Rolling_memory таблица | альтернатива Architect'а | FTS contamination, semantic retrieval contamination (GPT hard-no). |
| Новый MCP сервер для affect-writes | альтернатива Architect'а | Roundtrip latency. PostToolUse hook + CLI достаточно. |
| Sentiment analysis output'а Claude'а для auto-update | альтернатива | Circular. Дефер проблемы grounding'а. |
| Status-line on by default | альтернатива PM | Trust erosion. Dmitry anti-theater profile требует silent default. |

---

## 9. План реализации (после одобрения MVP)

1. **Phase A — инфра** (≤5 файлов)
   - `~/.claude/scripts/affect_state.py` — read/write/decay primitives
   - `~/.claude/scripts/affect_post_tool.py` — PostToolUse hook
   - `~/.claude/scripts/affect_session_start.py` — decay-on-read + injection
   - `~/.claude/scripts/mood_cli.py` — `/mood` command
   - Seed в `rolling_memory.db` с `under_review` + `resolve_by_date=2026-05-22`
2. **Phase B — эксперимент** (2 недели)
   - 3-arm script, blinded selection, метрики автоматизированы из `telemetry_agent_health.py` + handover parsing
3. **Phase C — решение** (2026-05-22)
   - Если метрики прошли → `status='active'`, document в `institutional.md`
   - Если нет → `status='superseded'`, удалить код

**Оценка:** Phase A ~1 рабочий день, Phase B ~2 недели фонового прогона, Phase C ~1 час review.

---

## 10. Открытые вопросы к Dmitry

1. **Согласен на именование `interaction_state_register`** вместо «эмоции»? (GPT + Skeptic настаивают на нейтральных терминах; «эмоции» в повседневной речи ОК, в коде/UI — нет.)
2. **Согласен на 3 канала вместо 7**? (Все 6 участников — да, но твоя исходная идея была 7.)
3. **Согласен на обязательный blinded A/B перед default-on**, или готов ship-default-then-watch? (Skeptic strongly recommends A/B; остальные тихо согласны.)
4. **Какие проекты в эксперимент** — Claude_Booster + Horizon + yfinance, или шире?
5. **Кто/что делает blinded selection** — ручной script tossing coin, или хочешь чтобы я поднял harness?

---

## Участники

| # | Agent | Model | Agent ID |
|---|---|---|---|
| 1 | Affective-Computing Theorist | opus | `a858890604a9f13cd` |
| 2 | ML-Systems Architect | sonnet | `a50bddae204ed97c3` |
| 3 | Calibration / Sycophancy Researcher | opus | `ae0a2ecccffde1633` |
| 4 | Devil's Advocate / Skeptic | opus | `a42db789ccd7d0694` |
| 5 | Product / UX | sonnet | `ad06f309cca6c131a` |
| 6 | GPT-5.4 via PAL thinkdeep | gpt-5.4 | continuation `917a108b-dfb9-44bb-abac-349d0c7a8576` |

**Синтез:** Lead (Opus 4.7, 1M context).
