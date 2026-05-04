# Paired Verification — Lead spawns Worker AND Verifier as a pair

Dmitry's request (2026-04-30): когда Lead делегирует содержательную работу, второй агент создаёт приёмку **независимо** — параллельно или последовательно, но с собственным контекстом. Lead не оценивает результат своим суждением; он запускает тест, написанный Verifier'ом, и читает exit code.

## Why this rule exists

Эмпирически и теоретически, **single-agent ≥ multi-agent при равных compute** (arxiv 2604.02460, Anthropic engineering, Cognition). Когда Lead всё-таки делегирует, проигрыш приходит из трёх источников:

1. **Information loss at handoff** — Data Processing Inequality: ответ Worker'а информационно ограничен брифом, summary Lead'а ограничен ответом Worker'а. Каждый hop через границу агента — лосси кодек.
2. **Self-evaluation bias** — Anthropic явно: *"agents tend to confidently praise even mediocre work"*. Lead, оценивающий Worker'а, — это та же модель, которая написала бриф; она склонна видеть результат как соответствующий собственному намерению.
3. **Lead's context decay during the wait** — пока Worker работает, окно Lead'а смещается, acceptance criteria вытесняются свежими tool-results. К моменту возврата Worker'а у Lead'а уже размыто «что значит done».

Контра-мера, конвергентно рекомендуемая Adzic (Specification by Example), Toyota Jidoka (self-process completion), Anthropic eval guidance (*"separate generator from evaluator"*): **acceptance check — отдельный артефакт, executable, написанный другим агентом, на другом контексте, который Lead запускает машинно**.

## When this rule applies

- Любой `Agent` spawn, который **производит** артефакт (код, конфиг, данные, миграция, патч).
- Любой `Agent` spawn, чей выход Lead иначе бы «прочитал и одобрил».
- НЕ применяется к чисто read-only recon (Explore listing файлов, grep, summarize) — там нет артефакта против контракта.
- НЕ применяется к тривиальным механическим правкам, которые Lead делает сам без агента (опечатка, переименование, единичная конфиг-строка) — Three Nos применяется через body guards, не парный спавн.

См. §"When you can skip the pair" ниже — skip определён **отрицательно** (через перечень impact-классов, при которых skip запрещён), а не через «маленькая задача».

## RECON — mandatory architecture reading

Before writing any Artifact Contract, Lead **MUST** read `ARCHITECTURE.md` and `.claude/dep_manifest.json` (if they exist in the project):
- Consult the dependency table to populate the `Affected downstream:` field
- If the function being changed is listed as `critical: true` in dep_manifest.json, include its `feeds` array in the Artifact Contract
- If the project has no architecture docs: note it in the handover as a gap; do not block work

## Pattern A — параллельная пара (по умолчанию)

В одном сообщении — два `Agent` tool-call'а:

1. **Worker agent** получает: goal + Verified Facts Brief + scope + Artifact Contract. Делает работу, кладёт артефакт в указанный путь, возвращает ссылку на него.
2. **Verifier agent** получает: тот же goal + ту же Verified Facts Brief + тот же scope + тот же Artifact Contract. **Не видит prompt'а Worker'а.** Производит ОДИН executable acceptance test.

Оба бегут конкурентно. Lead дожидается обоих, потом запускает тест.

## Pattern B — последовательная пара

Используется только когда контракт нельзя выразить, не увидев форму артефакта (редко). Worker → Verifier → Lead запускает тест. Verifier всё равно работает в свежем контексте; всё равно не видит prompt'а Worker'а; всё равно производит executable, не суждение.

## Artifact Contract — общий и обязательный

Каждый бриф (Worker-у И Verifier-у) включает один и тот же Artifact Contract:

```
Objective: <одно предложение, что система должна делать>
Verified Facts Brief: <что существует сейчас, с file paths и evidence>
Artifact path: <куда Worker положит результат>
Invocation: <как запускается / импортируется>
Inputs: <формы и типы входных данных>
Expected observable behavior: <что внешний наблюдатель должен увидеть>
Out of scope: <что НЕ менять, какие интерфейсы НЕ ломать>
Environment constraints: <зависимости, версии, доступные ресурсы>
Acceptance emphasis: <что обязательно проверить; что не предполагать>
Affected downstream: <functions/APIs/screens that consume this artifact's output — consult dep_manifest.json>
Architecture map consulted: <yes/no — was ARCHITECTURE.md or dep_manifest.json read before writing this contract?>
Session context: <OPTIONAL — see §Session context injection below>
```

Без Artifact Contract пара не спавнится — это предусловие. Если Lead не может его сформулировать, задача недостаточно понята для делегирования (Adzic «definition of ready»).

## Verifier — что видит / не видит (явно)

### Verifier МОЖЕТ видеть
- Objective и Verified Facts Brief.
- Artifact Contract (полностью).
- Существующий публичный интерфейс: имена функций, CLI shape, endpoint paths, config keys, output paths, DB schema, expected artifact location.
- Файлы или сниппеты, нужные для понимания ожидаемого поведения.

### Verifier НЕ ДОЛЖЕН видеть
- Worker's prompt.
- Worker's plan.
- Worker's reasoning / draft / черновики.
- Lead-комментарии, намекающие на выбранную стратегию реализации.
- «Likely solution» заметки, кроме случаев когда это реальные продуктовые ограничения.

Принцип: **Verifier тестирует наблюдаемое поведение артефакта, а не выбранный Worker-ом метод реализации.**

## Verifier mandate (точная формулировка для prompt'а)

> «Произведи один executable acceptance test, который вернёт exit 0 если артефакт удовлетворяет Artifact Contract, иначе non-zero. Тестируй **наблюдаемое поведение**, не приватные детали реализации. Не реализуй задачу. Не предполагай, как Worker её решит. Если acceptance criteria неоднозначны — **fail closed**: верни отчёт об неоднозначности вместо того чтобы изобретать продуктовые решения. Тест должен печатать осмысленный stdout/stderr при failure.»

## Test Legitimacy Standard

Verifier-тест должен соответствовать всем пунктам:
- Тестирует **наблюдаемое поведение**, не приватные детали реализации (если деталь не в Artifact Contract явно).
- Минимизирует допущения, которых нет в Objective / Verified Facts Brief / Artifact Contract.
- **Детерминирован** — два запуска подряд дают одинаковый результат.
- Печатает осмысленный diagnostic output при failure (assertion, что ожидалось, что получено).
- Не зависит от network / localhost / hidden state / timing / CWD, если это не явно в scope.
- Использует tempdir / tempfile где уместно, чистит за собой.
- Возвращает non-zero на любой failure path.

### Allowed forms
- Bash-скрипт с `set -e` и явными `[[ ... ]]` / exit codes.
- pytest test-file, запускаемый как `pytest <path>`.
- `curl --fail` против реального endpoint'а + `grep` по телу + assert на HTTP code.
- `sqlite3` / `psql` запрос с проверкой rowcount или конкретного значения.
- `grep -q` по файлу с ожидаемой строкой/паттерном.
- Любая комбинация выше с явным non-zero exit на failure.
- Один executable artifact МОЖЕТ содержать несколько assertion'ов (positive case + negative case + edge case в одном файле — это нормально и желательно).

### Forbidden forms
- ❌ Прозаический checklist «проверьте что X, Y, Z».
- ❌ «Look at the output and decide» — требует LLM-суждения.
- ❌ Тест, который вызывает Claude/LLM как judge.
- ❌ `curl -s` без `--fail` / `|| true` (см. verify_gate fake-evidence patterns).
- ❌ `localhost` / `127.0.0.1` как target в проде-сценарии.
- ❌ Verifier изобретает конкретные продуктовые решения (точные exit codes, sort order, key names) когда Artifact Contract их не специфицирует — это создаёт скрытую спеку, которую Worker не видел.

## Lead's role after the pair returns

1. Worker возвращает → запомнить путь к артефакту.
2. Verifier возвращает → запомнить путь к acceptance-тесту.
3. Lead запускает тест через `Bash`. Записывает exit code и stdout/stderr.
4. **PASS (exit 0):** идём дальше — commit / next step / TaskUpdate completed.
5. **FAIL (exit ≠ 0):** не правим inline. Сначала **классифицируем failure** (см. ниже), потом действуем.

**Lead не читает код Worker'а чтобы вынести вердикт.** Единственный вход для PASS/FAIL — exit code теста + его stdout. stdout читаем для роутинга remediation, не для override'а вердикта.

## Post-VERIFY architecture update

After PASS (exit 0) and before commit, Lead checks: did this change modify any interface listed in the ARCHITECTURE.md dependency table or dep_manifest.json?

- **If YES:** spawn a background agent (`run_in_background: true`, `model: "haiku"`) that:
  1. Reads current ARCHITECTURE.md and dep_manifest.json
  2. Reads `git diff` of changes made in this session
  3. Updates the dependency table rows and dep_manifest.json entries for affected components
  4. Adds a row to the Update Log in ARCHITECTURE.md with date + commit description
  5. Bumps the `updated` date in dep_manifest.json
  This agent runs in background — Lead proceeds with commit and next steps without waiting.

- **If NO:** skip (most bug fixes don't change interfaces; skip is logged in handover)

- This is NOT a Worker+Verifier pair — it's a mechanical doc update (skip per §"When you can skip the pair": zero behavior impact, deterministic content)

## Failure classification — обязательно перед реакцией на FAIL

Lead классифицирует non-zero exit ровно в одну из четырёх категорий:

| Категория | Признак | Реакция |
|---|---|---|
| **W. Artifact wrong** | Artifact существует, ведёт себя не так, как требует Artifact Contract | Спавнить нового Worker'а с narrowed scope, передать failing test + stdout. Verifier-тест НЕ менять. |
| **V. Test invalid / over-constrained** | Тест проверяет приватную деталь не из Contract; Worker реализовал контракт корректно но иначе | Спавнить нового Verifier'а (свежий контекст), требовать revise теста против оригинального Contract. Worker НЕ менять. Lead не отменяет verification — только перевыпускает тест. |
| **A. Contract ambiguous** | Verifier явно вернул «ambiguous», или оба (Worker+Verifier) интерпретировали по-разному | Lead уточняет Artifact Contract, обновляет Verified Facts Brief, перезапускает пару (Pattern A). |
| **E. Environment** | Тест не запустился из-за зависимостей, доступа, версии, network | Чинит окружение / harness, перезапускает тот же тест. |

**Важно:** ни в одной из четырёх категорий Lead не выносит PASS «по чтению кода». Категория V — единственный путь признать тест невалидным, и он требует **regen теста**, не override.

Hard cap на retries: 3 (см. pipeline.md §Failure recovery). После 3 неудач — возврат пользователю с aggregated failure + recommended next action.

## When you can skip the pair

Skip разрешён **только** когда Lead может назвать конкретную причину, почему executable acceptance property не существует помимо прямого осмотра, **И** задача не имеет ни одного из impact-классов ниже.

### Skip ЗАПРЕЩЁН для
- Любого изменения поведения.
- Любого bug fix'а.
- Любого изменения auth / security / permissions.
- Любой data migration / schema change.
- Любого изменения concurrency / caching / error-handling.
- Любого изменения test-infrastructure (может маскировать failures).
- Любого production config / deployment изменения.
- Любого edit'а, затрагивающего несколько файлов где взаимодействие важно.
- Любого изменения после prior verification failures.
- Любой задачи, мотивированной audit / incident / handover failure'ом.

### Skip разрешён для
- Read-only investigation без изменения артефакта.
- Formatting-only edit'ов (детерминированный formatter — `ruff format`, `prettier`).
- Rename / comment / doc typo с zero behavior impact.
- Mechanical search/replace при scope малом, замене однозначной, **семантика поведения не меняется**.
- Throwaway / debug / one-off скриптов в `/tmp`.

«Small» и «obvious» **сами по себе не достаточны** для skip'а. Нужна явная причина, почему нет executable property для проверки. Если skip применён — **факт пропуска фиксируется в handover'е** одной строкой («skipped paired-verification because <reason>»), чтобы при будущем поиске «куда делась проверка» след был.

## Session context injection

Agents have no memory of the Lead's conversation. When the task depends on session history — prior decisions, failed attempts, discussed approaches — Lead injects session context into the Artifact Contract via the `Session context:` field.

### Tool

`python3 ~/.claude/scripts/session_context.py` — extracts readable conversation from the current session JSONL. Preserves code edits (Edit/Write diffs), Bash commands + results, and all dialogue. Strips hook noise, permission modes, file-history snapshots.

### When to include (decision rule)

Include session context when **any** of these is true:

| Trigger | Whose context | Invocation to use |
|---|---|---|
| **Retry/fix** — re-spawning after Worker failed | **Failed agent's** | `--agent "<Worker desc>" --tail 20 --no-thinking` |
| **Debug chain** — 2+ prior attempts at same problem | **Failed agent's** | `--agent "<prev Worker>" --grep "<symptom>" --no-thinking` |
| **Back-reference** — "как обсуждали" / "continue" | **Lead's** | `--tail 15 --no-thinking` |
| **Decision context** — *why* a choice was made | **Lead's** | `--grep "<topic>" --no-thinking` |
| **Self-audit** — reviewing session's code changes | **Lead's** | `--tools-only --grep "Edit\|Write" --no-thinking` |
| **List who did what** — orientation before retry | **Lead's** | `--subagents` |

**The critical distinction:** on retry, the new Worker needs the **failed agent's** session, not Lead's. The failed agent saw stack traces, tried approaches, hit edge cases — Lead only saw the summary. Lead's session is for discussion context (decisions, back-references); agent sessions are for execution context (what was tried, what broke).

**When NOT to include:** task is fully described by Artifact Contract + files on disk. Most first-attempt Worker+Verifier pairs fall here — the Contract is self-contained by design.

### Subagent discovery

Each Lead session stores subagent JSONLs in `<session-id>/subagents/agent-*.jsonl` with `.meta.json` files containing `agentType` and `description`. The tool supports:

```bash
# List all agents of a session (who ran, when, how big)
python3 ~/.claude/scripts/session_context.py --subagents

# Read specific agent by description keyword (picks most recent match)
python3 ~/.claude/scripts/session_context.py --agent "Worker: fix rebuilder" --tail 20 --no-thinking

# Read specific agent by ID prefix
python3 ~/.claude/scripts/session_context.py --agent "a3a5d27d" --no-thinking
```

**Note:** the tool auto-detects the project dir from CWD. If the agent runs in a worktree or different directory, pass `--project-dir ~/.claude/projects/<project-hash>` explicitly.

### How to write the field

For Lead context:
```
Session context: before starting, run:
  python3 ~/.claude/scripts/session_context.py --tail 15 --no-thinking
  Focus on: <what the agent should look for>
```

For failed agent context (retry):
```
Session context: the previous Worker failed. Read its session:
  python3 ~/.claude/scripts/session_context.py --agent "<Worker description>" --no-thinking
  Focus on: what it tried, where it got stuck, and any error messages.
  Do NOT repeat the same approach — find a different path.
```

The `Focus on:` directive is mandatory when including session context — without it the agent reads N turns and doesn't know what matters. Exception: `--tools-only` mode for self-audit, where the focus is implicit (all edits).

### What the Verifier sees

Session context goes to **Worker only**. Verifier tests observable behavior per Artifact Contract; session history is implementation context, not acceptance criteria. If a session decision changes *what* the artifact should do (not *how*), promote that decision into the Artifact Contract's `Objective` or `Expected observable behavior` fields instead.

## Anti-patterns (запрещено)

- ❌ Спавнить Worker'а, потом Lead читает код и говорит «выглядит ок» — это и есть self-evaluation bias.
- ❌ Verifier пишет «тест, который проверит, что вызвав функцию F, она работает» — где определение «работает»? Если требует LLM-суждения — не acceptance.
- ❌ Worker и Verifier живут в одном thread'е (continuation_id, sub-prompt, etc.) — контекст пересекается, независимости нет.
- ❌ Скип пары «потому что задача маленькая» — именно на маленьких bias кусает сильнее всего, потому что Lead легко убеждает себя что «и так очевидно».
- ❌ Verifier видит Worker'ов prompt в брифе («чтобы знал что проверять») — нарушение независимости. Verifier строит контракт от Objective, не от прочтения чужого решения.
- ❌ Verifier изобретает специфику (exact error code, sort order, точное имя файла) когда Artifact Contract её не задаёт — implicit decision conflict; должен возвращать «ambiguous» или писать property-style тест.
- ❌ Lead override'ит FAIL вердикт по «чтению кода» — даже когда тест over-constrained, корректный путь — regen теста (категория V), не override.

## Origin

Adzic «Specification by Example» — спецификация = executable примеры. Toyota Jidoka / Jikotei Kanketsu — каждый узел self-certifies, не передаёт брак вниз. Anthropic engineering on multi-agent: *"separate generator from evaluator"*, *"grade what was produced, not the path"*, *"agents tend to confidently praise even mediocre work"*. Cognition «Don't build multi-agents» — для write-heavy tasks single-agent выигрывает; пара Worker+Verifier — это не multi-agent в их смысле, это single-agent + independent acceptance harness.

External hardening (PAL/GPT-5.5 second-opinion 2026-04-30, continuation `27613123-a244-49a7-95ba-baeaab0dbf9a`): добавлены §"Verifier may see / may not see", §"Failure classification" (W/V/A/E), §"Test Legitimacy Standard", сужение skip carve-out, §"Artifact Contract" как обязательное предусловие.

Закрывает gap, документированный в:
- `reports/audit_2026-04-17_agent_context_dysfunction.md` — Lead's verification of Worker output is shallow; rules as prose, not blocking mechanics.
- `reports/audit_2026-04-18_startup_token_budget.md` — Framing 2 (attention saturation; rules buried at token 8000 lose to recent tool output).
- `reports/consilium_2026-04-29_temporal_causal_recon_pivot.md` — verify_gate v1.5 FP went 11 days unfixed across 6+ handovers (exact failure mode).
