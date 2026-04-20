# Тех-задание — Supervisor Agent (Claude Booster v1.2.0)

**Статус:** DRAFT. Следующий шаг — consilium (3–5 агентов + GPT через PAL) для валидации архитектуры.
**Автор:** Claude (Opus 4.7), сессия 2026-04-20.
**Дата:** 2026-04-20.

---

## 1. Контекст и мотивация

Dmitry в ежедневной работе с Claude Code сталкивается с двумя раздражителями, которые v1.1.0 (phase machine + hard gates) **не решает**:

1. **Approval-стопы.** Claude запрашивает подтверждение на bash-команду/edit в ситуациях, где интент ясен, и пользователь просто нажимает Enter. В Auto Mode это уже частично снимается, но не полностью.
2. **Semantic-стопы.** Claude сам остановился и задал clarifying question, хотя ответ очевиден из контекста (нарушение 51%-rule из `core.md`). Процесс физически ждёт ввода вместо того, чтобы действовать.
3. **Тихие зависания.** Процесс не двигается N секунд без явного вопроса. Нет автоматической детекции и «нудёжа».

Цель v1.2.0: **убрать человека из петли на подтверждениях и мелких решениях**, оставив его только в реально неочевидных развилках. Человек — escalation target, не клавиатура.

---

## 2. Архитектурные кандидаты

### A. Agent SDK Supervisor (рекомендуемый MVP)

Python-скрипт через `claude-agent-sdk` спавнит Claude Code как subprocess в headless-режиме (`-p` + `--output-format stream-json`), читает stream-json из stdout, пишет в stdin. Supervisor — это **отдельный Claude Code с ограниченными tools** (Read, TaskGet, `inject_user_message`, `kill_worker`, `escalate`), промпт которого: *«Ты watchdog. Цель = X. Не пиши код. Следи за worker, отвечай на approval-вопросы по whitelist, эскалируй только неочевидное»*.

**За:**
- Официальный SDK (anthropic/claude-agent-sdk-python), нулевая магия над терминалом.
- Supervisor и worker — изолированные процессы, supervisor можно заменить на Haiku (быстро + дёшево).
- Stream-json даёт структурированные события — supervisor парсит по типу сообщения, не по regexp'у bash-output.
- Встраивается в Claude Booster как `templates/scripts/supervisor.py` + slash-команда `/supervise <task>`.

**Против:**
- Headless-режим теряет TUI, теряется интерактивность для Dmitry «посмотреть что там».
- Supervisor сам по себе — ещё один контекст, который жрёт токены.

### B. Tmux Watchdog (fallback для уже-запущенных сессий)

Bash + `tmux capture-pane` + heuristics. Не спавнит новый Claude — наблюдает существующий pane. Если pane не менялся >N секунд или содержит approval-промпт из whitelist — инжектит `tmux send-keys "yes" Enter` или `"продолжай по best-guess"`.

**За:**
- Работает с нынешним TUI Dmitry без изменений.
- Нет второго контекста → нет расхода токенов.
- Готовые референсы: `Jedward23/Tmux-Orchestrator`, `awslabs/cli-agent-orchestrator`, `Dicklesworthstone/claude_code_agent_farm`.

**Против:**
- Heuristics на текст терминала ломаются при обновлении Claude Code UI.
- Нет понимания семантики — слепо отвечает «yes» на что угодно, включая «git push --force к main?».
- Требует tmux — пользователи без tmux остаются без защиты.

### C. Гибрид (long-term, v1.3+)

Supervisor (A) как основной режим для scripted/autonomous задач. Watchdog (B) как дополнительный слой для интерактивных сессий Dmitry. Оба пишут в общий `~/.claude/logs/supervisor_decisions.jsonl`.

---

## 3. Рекомендация для MVP (v1.2.0)

**Путь A, минимальный scope.**

Причина: это дисциплинированнее. Supervisor — это Claude с ограниченными tools, его решения объяснимы и логируемы. Tmux-watchdog — костыль, который хорош как быстрый win, но компаундным активом (в духе v1.0–v1.1) становится плохо.

MVP-scope:
1. `templates/scripts/supervisor.py` (~200–300 LOC)
2. Supervisor-промпт как `~/.claude/prompts/supervisor_v1.md` (whitelist approval-тем, escalation-критерии)
3. Slash-команда `/supervise <goal>` в `templates/commands/supervise.md`
4. Auto-escalation → macOS notification + запись в `~/.claude/logs/escalations.jsonl`
5. Регистрация в `install.py::BOOSTER_VERSION = "1.2.0"`
6. Dogfood-сессия — прогнать supervisor на реальной задаче (например, рутинный /handover).

Scope **не** включает:
- Multi-worker оркестрацию (agent-farm pattern) — отдельный проект.
- Tmux-watchdog — может попасть в v1.2.1 после фидбэка Dmitry.
- Auto-approve `git push` или destructive commands — остаётся за человеком всегда.

---

## 4. Whitelist авто-одобрений (первая итерация)

Supervisor **может** отвечать «yes» сам:
- Read, Glob, Grep, WebSearch, WebFetch — read-only tools.
- `git status`, `git diff`, `git log`, `git show`, `git branch`.
- `npm test`, `pytest`, `cargo test` — тесты без side effects.
- `curl GET` к публичным URL (whitelist доменов).
- Bash в `/tmp/`, `$HOME/scratch/`, sandbox dirs.
- TaskCreate, TaskUpdate (без status=completed — это ловит require_evidence).

Supervisor **обязан** эскалировать:
- `git push`, `git reset --hard`, `git rebase`.
- `rm -rf`, `mkfs`, `dd`, `docker system prune`.
- Edit в production-config файлах (detected by `permissions.deny` mirror).
- Любой запрос с упоминанием «production», «prod», «live».
- Consilium/audit решения (требуют мнения человека по определению).
- Approval-промпт, не попавший в явный whitelist — safer default.

---

## 5. Анти-риски

| Риск | Митигация |
|---|---|
| Supervisor в петлю отвечает «yes» worker'у, worker ломает прод | Hard-deny list зеркалит `permissions.deny` из v1.1.0 settings |
| Supervisor сам потребляет токены сопоставимо с worker'ом | Use Haiku 4.5 для supervisor (в 10–15× дешевле Opus); max_thinking_tokens=1000 |
| Worker вопрос не в whitelist и не в deny → тупик | Timeout 60с → escalate в macOS notification + лог |
| Headless worker не умеет в MCP tools, которые работают только в TUI | Проверить `mcp-claude-in-chrome` в headless — если нет, supervisor пропускает UI-задачи на человека |
| Supervisor-промпт drift'ит как любой промпт | Hash промпта в лог каждой сессии; раз в неделю — audit промпта как любого другого rule |

---

## 6. План работ на следующие сессии

### Сессия 1 (следующая после 2026-04-20)
- **Consilium (обязательно):** 3–5 агентов + GPT через PAL. Роли: architect, security, product, ops. Вопросы к консилиуму:
  1. Supervisor как headless subprocess vs Supervisor как MCP server — что сопровождабельнее?
  2. Whitelist авто-одобрений — достаточен ли, нет ли ловушек?
  3. Как детектить semantic-stop (Claude сам остановился без явного вопроса)? Heartbeat? Idle-detection на stream-json gap?
  4. Стоит ли делать supervisor stateful (помнит прошлые escalation'ы) или stateless (каждый запрос — чистый контекст)?
- Фиксируем решение в `reports/consilium_YYYY-MM-DD_supervisor_architecture.md`.

### Сессия 2
- Скелет `supervisor.py`: spawn + stream-json parsing + базовый whitelist. Без Claude-supervisor-промпта — просто regex-матчер на вопросы. Proof-of-subprocess.

### Сессия 3
- Замена regex-матчера на реальный Claude-supervisor (Haiku). Промпт, инструменты, first real task.

### Сессия 4
- Dogfood: supervisor ведёт реальную handover-сессию. Измеряем — сколько раз эскалировал Dmitry, сколько approve'нул сам, сколько ошибся.

### Сессия 5
- Bump v1.2.0, README, install.py, commit, push. Документируем «сколько времени экономит в среднем за день» как маркетинговый KPI.

---

## 7. KPI для v1.2.0

- **Главный:** среднее число approval-кликов Dmitry за 1 час работы с Claude Code снижается с current baseline до **≤3/час** (измерить baseline за 3 дня до v1.2.0, потом после).
- **Безопасность:** ноль случаев автоапрува destructive-команд за первый месяц.
- **False escalation:** <20% эскалаций оказываются тривиальными («надо было самому решить»).

---

## 8. Что НЕ делаем сегодня

- Сегодня только этот tech-spec. Коммит tech-spec + handover, push, и день закончен.
- Всё остальное — следующая сессия, начиная с consilium.
