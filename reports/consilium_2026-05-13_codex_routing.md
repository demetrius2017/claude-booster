# Consilium: Почему Codex routing не работает на практике

**Date:** 2026-05-13  
**Lead:** Claude Opus 4.7 (1M context)  
**Topic:** Codex-cli не используется как альтернативный провайдер, несмотря на то, что model_balancer уже принимает решение о routing. Полный разбор причин и план исправления без bias в сторону Claude.

---

## Task context

v1.8.0 shipped model_balancer с daily Pareto scoring. Balancer в model_balancer.json говорит:
- `coding → codex-cli:gpt-5.3-codex`
- `hard → codex-cli:gpt-5.5`
- `trivial, recon → codex-cli:gpt-5.3-codex-spark`

В reality: **zero реальных вызовов Codex за 10 сессий.** model_metrics таблица содержит только smoke-test строки и garbage rows от сломанного regex. Claude Max budget достиг 86% к концу недели. Codex Pro quota (flat-fee) — неиспользованный ресурс.

**RECON — Verified Facts Brief:**
1. `model_metrics` table: 3 smoke-test rows + 3 garbage rows (model=`"$(cat`, `strings`, `—`) — парсер ел shell expansion из heredoc. Реальные Codex вызовы: 0.
2. `Agent` tool = всегда Anthropic API, нет возможности указать Codex как backend.
3. `delegate_gate.py`: Agent вызовы СБРАСЫВАЮТ budget (reward), Bash вызовы ПОТРЕБЛЯЮТ budget (penalty). Механически penalizes Codex, потому что единственный способ вызвать его — через Bash.
4. `tool-strategy.md`: нет рецепта как вызвать Codex. Lead читает "codex-cli" в balancer output, но не знает что с этим делать.
5. `supervisor.py`: Claude-only, нет Codex backend.
6. `dep_guard.py`, `financial_dml_guard.py`, `verify_gate.py`: срабатывают только внутри Claude Code session. Codex subprocess — непрозрачный, hooks не видят его tool calls.

---

## Agent positions

| Agent | Роль | Ключевой вывод | KPI |
|---|---|---|---|
| **Bio 1 — Platform Architect** | Системная архитектура multi-provider | Проблема структурная: Agent tool захардкожен на Anthropic. Dispatch bridge отсутствует. Рекомендует Option D (recipe + delegate_gate fix) на этой неделе + Option B (supervisor.py polymorphic) на следующей. | delegate_gate fix = "single highest-leverage change" |
| **Bio 2 — Cost Engineer** | Экономика, ROI, token budget | Core problem = "Lead знает что balancer говорит codex-cli, но нет code path для dispatch". 30-40% сокращение Claude budget при 40-50% routing на Codex. Безопасные категории: recon, trivial, analysis, новые файлы, consilium. | -1-1.5M tokens/week → +1 продуктивный день/неделю до throttling |
| **Bio 3 — DevOps/Reliability** | Safety, операционные риски | Guard bypass — критическая архитектурная граница, не workaround. Нужен `codex_sandbox_worker.sh` (rsync temp dir → Codex → diff only → Lead applies). | Без sandbox Codex нельзя использовать для implementation tasks |
| **GPT gpt-5.5 (PAL)** | Независимый внешний эксперт | Полностью согласен с sandbox-first подходом. Конкретный дизайн `codex_sandbox_worker.sh` предоставлен. delegate_gate parity fix обязателен. supervisor.py extension — правильная долгосрочная архитектура. | "Don't route to Codex without a safety shell" |

---

## Convergence (все 4 агента согласны)

1. **delegate_gate.py parity fix** — treat `codex_worker.sh` as delegation (не penalizes Bash budget). Без этого routing на Codex экономически невыгоден даже если dispatch работает.

2. **Concrete recipe в tool-strategy.md** — Lead должен знать: "когда balancer возвращает `provider=codex-cli`, вызывай `codex_worker.sh <model> < prompt.txt` через Bash". Сейчас рецепта нет.

3. **Guard boundary — правильная граница, не obstacle** — Codex безопасен для задач, где Lead проверяет output перед применением. Полный обход guard = антипаттерн.

4. **Zero actual Codex usage = потеря flat-fee ресурса** — Claude Max 86% при неиспользованном Codex Pro сигнализирует о реальных потерях, не академической проблеме.

---

## Divergence (разногласия)

| Вопрос | Bio 1 | Bio 2 | Bio 3 + GPT |
|---|---|---|---|
| Что первично: recipe или sandbox? | Recipe + delegate_gate (ship this week) | Recipe достаточно для recon/trivial/analysis прямо сейчас | Sandbox required до implementation tasks |
| Уровень срочности sandbox | "Next week" | "Немедленно для recon, sandbox for impl" | "Sandbox first, then implement" |
| Codex для multi-turn tasks | Возможно через supervisor.py | Нет — cold-start overhead per turn | Нет — архитектурно неудобен |

**Резолюция:** Bio 3 + GPT правы относительно sandbox-first для implementation. Bio 2 прав что recon/trivial/analysis можно запускать сразу (stdout-only, Lead reviews). Bio 1's timeline немного оптимистична, но последовательность верна.

---

## Decision

**Принят план из трёх фаз:**

### Фаза 1 — This week (~2-3h)
1. **delegate_gate.py fix**: `codex_worker.sh <model>` Bash вызовы = delegation (budget reset, не consume). Без этого routing неэффективен экономически.
2. **tool-strategy.md recipe**: добавить блок "Calling Codex" с конкретным примером `Bash("codex_worker.sh gpt-5.3-codex-spark", stdin=prompt)`. Категории для немедленного использования: recon, trivial, analysis, consilium bio-agents.
3. **model_metric_capture.py**: parser bug уже исправлен (fix в этой сессии, commit 21ece31). Telemetry теперь trustworthy.

### Фаза 2 — Next week (~3-4h)
4. **codex_sandbox_worker.sh**: rsync worktree → `codex exec` → capture diff → return diff only. Lead применяет diff через Edit tool (guards fire). Открывает implementation tasks для Codex.
5. **Обновить model_metric_capture.py** для capture Codex invocations через sandbox worker (новый pattern).

### Фаза 3 — Medium-term (~4-6h)
6. **supervisor.py provider extension**: Lead передаёт `--model codex-cli:gpt-5.3-codex` → supervisor spawns `codex_sandbox_worker.sh` вместо `claude -p`. Полностью polymorphic routing без ручного выбора в Lead.

---

## Rejected alternatives

| Альтернатива | Почему отклонена |
|---|---|
| **Option A: PreToolUse hook перехватывает Agent вызовы и переписывает на Codex** | Опасно: hooks run in Lead's process, могут блокировать легитимные Anthropic вызовы. Невозможно rule-based отличить "этот Agent должен быть Codex" без race conditions. |
| **Option C: переписать балансировщик на Anthropic-only** | Отклонён явно — цель сессии обратная: убрать bias в сторону Anthropic. |
| **"Fix model_metric_capture.py и ждать данных"** | Уже сделано, но само по себе не решает отсутствие dispatch path. |
| **Bypass dep_guard/verify_gate для Codex** | Все 4 агента против. Guards защищают финансовые и инфраструктурные изменения — Codex subprocess их не видит. Workaround = риск потери safety net. |

---

## Risks

| Риск | Вероятность | Митигация |
|---|---|---|
| Sandbox diff применяется без review → пропускает баг | LOW при правильной реализации sandbox | Lead always applies diff via Edit tool — guards fire. diff review = same as today's code review. |
| Codex Pro quota ограничен неизвестным ceiling | MED | debt [4]: wire Codex Pro quota live source. Пока — мониторить вручную. |
| model_balancer.py получает неверный weekly_max_pct | MED | Фикс уже в этой сессии (claude_max_tracker.py). Но weekly_tokens_cap = 0 пока пользователь не настроит. |
| delegate_gate.py fix некорректно классифицирует новые patterns | LOW | Acceptance suite covers это. Verifier agent + test suite перед deploy. |

---

## Implementation recommendations (ordered)

```
Week 1:
  1. delegate_gate.py — pattern: codex_worker.sh = delegation, budget_reset
  2. tool-strategy.md — добавить Codex recipe block для Lead
  3. Использовать Codex немедленно для: recon, trivial, consilium bio

Week 2:
  4. codex_sandbox_worker.sh — sandbox wrapper
  5. model_metric_capture.py — capture sandbox invocations
  
Month 1:
  6. supervisor.py — provider-polymorphic backend
```

**KPI success metric:** через 2 недели `model_metrics` table должен показывать ≥30% строк с `provider=codex-cli`. Если нет — delegate_gate или recipe неверны.

---

## Session artifacts

- **RECON report:** `reports/recon_2026-05-12_model_balancer.md`
- **Bio 1 output:** Consilium agent session (Platform Architect)
- **Bio 2 output:** Consilium agent session (Cost Engineer) — 34k tokens, 16 tool calls
- **Bio 3 output:** Consilium agent session (DevOps/Reliability)
- **GPT analysis:** PAL thinkdeep with gpt-5.5
- **model_metric_capture.py fix:** commit `21ece31`
- **claude_max_tracker.py:** commit `7403534` (schema v8, StopSession hook)
