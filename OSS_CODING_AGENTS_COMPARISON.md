# Сравнение текущего проекта Artel с open-source coding agents

_Дата: 2026-03-10_

## TL;DR

Текущий **Artel** уже выглядит не как просто ещё один terminal coding agent, а как **расширяемая агентная платформа** с упором на:

- много-провайдерную модельную абстракцию
- MCP runtime/config
- ACP/stdIO интеграции
- headless server + remote TUI
- расширения на Python
- rules / schedules / worktree / delegation

На фоне open-source конкурентов это даёт Artel сильную позицию как **интеграционного и orchestration-friendly ядра**.

Но по сравнению с наиболее зрелыми OSS coding agents у проекта пока есть заметные минусы:

- **основной пользовательский флоу менее отполирован**, чем у Codex / OpenCode / Aider
- **web surface фактически отсутствует в текущем checkout**
- **employee/dashboard/orchestrator vision пока реализована частично**, несмотря на backlog
- текущая orchestration/delegation модель — это в основном **in-process single-window delegation**, а не полноценная multi-agent workspace OS

Если коротко:

- **ближайший стратегический аналог**: **OpenCode**
- **лучший benchmark по git-centric coding loop**: **Aider**
- **лучший benchmark по polished CLI onboarding**: **Codex CLI**
- **лучший benchmark по ACP/editor story из сравниваемых**: **Kimi Code CLI**
- **самый широкий platform/toolkit angle**: **pi-agent**

---

## 1. Что именно сравнивалось

### Внутренние источники по Artel

Сравнение по текущему проекту опирается на код, docs и тесты в этом репозитории:

- `README.md`
- `PRODUCT_SCOPE.md`
- `docs/cli.md`
- `docs/acp.md`
- `docs/web.md`
- `packages/artel-core/src/artel_core/cli.py`
- `packages/artel-core/src/artel_core/control.py`
- `packages/artel-core/src/artel_core/config.py`
- `packages/artel-core/src/artel_core/worktree.py`
- `packages/artel-core/src/artel_core/mcp.py`
- `packages/artel-core/src/artel_core/mcp_runtime.py`
- `packages/artel-core/src/artel_core/delegation/tools.py`
- `packages/artel-core/src/artel_core/delegation/service.py`
- `packages/artel-core/src/artel_core/artel_bootstrap.py`
- `packages/artel-core/src/artel_core/builtin_capabilities.py`
- `packages/artel-core/src/artel_core/tools/web_search.py`
- `Artel implementation backlog v0.md`

Подтверждающие тесты:

- `tests/test_builtin_capabilities.py`
- `tests/test_mcp_cli_and_runtime.py`
- `tests/test_worktree.py`
- `tests/test_worktree_integration.py`
- `tests/test_delegation_tools.py`
- `tests/test_delegation_server_api.py`
- `tests/test_schedule_cli.py`
- `tests/test_schedule_server_api.py`
- `tests/test_docs_runtime_parity_cli.py`
- `tests/test_product_scope_doc.py`

### Внешние источники по OSS-конкурентам

Сравнение конкурентов основано на публичных README/docs:

- **OpenAI Codex CLI** — `openai/codex`, docs.openai.com / developers.openai.com
- **OpenCode** — `anomalyco/opencode`, `opencode.ai/docs`
- **Kimi Code CLI** — `MoonshotAI/kimi-cli`, `moonshotai.github.io/kimi-cli`
- **pi-agent** — `agentic-dev-io/pi-agent`
- **Aider** — `Aider-AI/aider`, `aider.chat`

> Важно: по внешним проектам это comparison по публичной документации и README, а не полный code audit.

---

## 2. Текущий профиль Artel

## 2.1. Что у Artel уже сильное

### 1) Широкая платформа, а не только CLI-чат
Artel уже поддерживает несколько режимов:

- local TUI: `artel`
- one-shot/print mode: `artel -p`
- headless server: `artel serve`
- remote TUI: `artel connect`
- JSON-RPC: `artel rpc`
- ACP agent: `artel acp`

Это прямо зафиксировано в `README.md`, `docs/cli.md`, `PRODUCT_SCOPE.md`, а CLI подтверждается в `packages/artel-core/src/artel_core/cli.py`.

### 2) Сильная интеграционная история
У проекта уже есть:

- **MCP config/store**: `packages/artel-core/src/artel_core/mcp.py`
- **MCP runtime**: `packages/artel-core/src/artel_core/mcp_runtime.py`
- **ACP support**: `docs/acp.md` + соответствующие тесты
- **REST control plane**: `packages/artel-core/src/artel_core/control.py`, server API tests

На фоне многих coding agents это делает Artel заметно более пригодным как **backend/agent runtime**, а не только как локальный терминальный помощник.

### 3) Очень широкая provider story
По `README.md`, `docs/providers.md` и дереву `packages/artel-ai/src/artel_ai/providers/` проект поддерживает:

- Anthropic
- OpenAI / compatible providers
- Google
- Kimi
- Azure OpenAI
- Bedrock / Vertex
- GitHub Copilot
- Ollama / LM Studio / llama.cpp
- и др.

По breadth провайдеров Artel выглядит **сильнее большинства CLI-агентов**, особенно если сравнивать не один UX, а именно платформенную широту.

### 4) Встроенные операционные primitives
В текущем checkout уже есть:

- **git worktree management**: `packages/artel-core/src/artel_core/worktree.py`
- **delegation tools**: `packages/artel-core/src/artel_core/delegation/tools.py`
- **in-process delegation runtime**: `packages/artel-core/src/artel_core/delegation/service.py`
- **rules / rule enforcement**
- **schedules**
- **web search / web fetch with prompt-injection guardrails**

Это редкая комбинация. Многие агенты умеют code edit + bash + git, но не дают одновременно **MCP + ACP + schedules + server + extension runtime + rules**.

### 5) Extension-friendly архитектура
Artel — один из немногих сравниваемых проектов, где расширяемость является **частью продукта**, а не вторичным скриптом:

- native Python extensions
- registry model
- built-in capabilities + extension-like boundary
- admin flows для extensions

См. `docs/extensions.md`, `packages/artel-core/src/artel_core/extensions_admin.py`, `packages/artel-core/src/artel_core/builtin_capabilities.py`.

---

## 2.2. Где Artel сейчас слабее рынка

### 1) User-facing product story пока фрагментирована
В `README.md` проект выглядит богато по capabilities, но `PRODUCT_SCOPE.md` одновременно фиксирует, что:

- web-first strategy вне текущего scope
- full web runtime в checkout отсутствует
- old employee/dashboard model сейчас не является core axis

Это значит, что **внутренне проект мощнее, чем выглядит как законченный продукт**, но внешний UX/story пока уступает более цельным агентам.

### 2) Web surface отсутствует в текущем checkout
`docs/web.md` и `tests/test_docs_runtime_parity_cli.py` подтверждают: `artel web` сейчас оставлен как compatibility surface и не является рабочим UI в этом checkout.

Это серьёзный минус относительно:

- OpenCode (desktop/client/server story)
- pi-agent (web UI toolkit/components)
- Kimi (видны признаки bundled web build pipeline)

### 3) Multi-agent/orchestrator ambition пока не доведена до продукта
`Artel implementation backlog v0.md` описывает сильную цель:

- dashboard
- orchestrator
- employee surfaces
- real employee lifecycle
- cmux-backed workspace model

Но текущий код показывает более раннее состояние:

- `packages/artel-core/src/artel_core/orchestration.py` — по сути re-export delegation surface
- `packages/artel-core/src/artel_core/delegation/service.py` — in-process delegated runs
- `packages/artel-core/src/artel_core/artel_bootstrap.py` — `command_requires_cmux()` возвращает `False`

То есть **архитектурный вектор есть, но finished multi-agent workspace продукта пока нет**.

### 4) Built-in capabilities ещё не выглядят как fully realized default stack
В `packages/artel-core/src/artel_core/builtin_capabilities.py` сейчас встроенные bundled capabilities — это прежде всего:

- `artel-mcp`
- `artel-lsp`

Это полезно, но всё ещё выглядит как **foundation layer**, а не как мощный набор first-party productized subsystems.

### 5) Rename/migration transition ещё заметен
В проекте ещё есть заметные compatibility следы `artel_*`:

- package paths
- internal imports
- registry URL в `config.py`
- часть test/runtime naming

Это не мешает функциональности, но проигрывает более цельным продуктам по ощущению завершённости.

---

## 3. Сравнение с конкретными OSS coding agents

## 3.1. Artel vs Codex CLI

### Что сильнее у Codex

- Более **понятный и polished primary workflow**: терминальный coding agent без лишней платформенной сложности
- Сильнее **onboarding/distribution**: npm, brew, release binaries
- Очень сильное product-positioning: локальный агент + IDE + desktop/web ecosystem
- Публично подчеркнуты security/sandbox/approval topics в документации

### Что сильнее у Artel

- Значительно шире **интеграционный стек**: ACP, JSON-RPC, server/control plane, schedules
- Намного шире **provider abstraction** и self-hosted/local runtime story
- Есть **native extension model**
- Есть **project/global rules**, task board, operator notes, worktree tool
- Есть встроенный **MCP config/runtime**, а не только core agent loop

### Вывод
Если цель — **максимально polished coding CLI для одного пользователя**, Codex выглядит сильнее как продукт.
Если цель — **расширяемая агентная платформа/ядро**, Artel интереснее и потенциально глубже.

---

## 3.2. Artel vs OpenCode

### Почему это самый близкий comparator
OpenCode публично позиционируется как:

- open source coding agent
- terminal/TUI-first
- provider-agnostic
- client/server architecture
- built-in agents
- out-of-the-box LSP
- desktop app

По набору идей это очень близко к тому, куда стремится Artel.

### Где OpenCode выглядит сильнее

- Более цельная **product narrative**
- Более убедительный **TUI-first UX**
- Яснее выраженная **multi-agent / agent modes** story (`build`, `plan`, `general`)
- Есть уже более осязаемая **desktop/client packaging story**
- Похоже, что client/server architecture у них уже является именно product-level feature, а не только internal substrate

### Где Artel выглядит сильнее

- **ACP** явно поддержан как first-party integration mode
- Есть **schedules**
- Есть **rules** как отдельная product primitive
- Есть **shared task board/operator notes**
- Python-extension ecosystem для Artel выглядит более прозрачным для кастомизации
- Есть built-in **worktree management**

### Вывод
OpenCode сейчас выглядит как **более цельный end-user product**.
Artel — как **более хакуемое и интеграционно богатое ядро**, которому ещё нужно догнать цельность UX.

---

## 3.3. Artel vs Kimi Code CLI

### Где Kimi выглядит сильнее

- Хорошо выраженный **terminal agent + shell hybrid**
- Явная **VS Code extension** story
- Явная **ACP story** для Zed/JetBrains
- Хорошо артикулированная **MCP UX**
- Продукт выглядит более собранным для daily-driver сценария

### Где Artel выглядит сильнее

- Значительно шире **provider landscape**
- Есть **server / remote TUI / control plane**, то есть больше distributed runtime story
- Есть **rules**, **schedules**, **extensions**, **worktree**, shared task board
- Более явная опора на **multi-surface architecture**, пусть и не завершённую

### Вывод
Kimi выглядит сильнее как **готовый developer tool**, особенно если нужна editor/ACP связка и shell-centric usage.
Artel сильнее как **framework-ish coding agent platform**.

---

## 3.4. Artel vs pi-agent

### Как правильно сравнивать
pi-agent по README выглядит не просто как coding CLI, а как **широкий AI agent toolkit / monorepo**:

- coding agent CLI
- unified multi-provider API
- TUI library
- web UI library
- Slack bot
- vLLM pod tooling

То есть это не совсем тот же класс продукта, что Aider или Codex.

### Где pi-agent выглядит сильнее

- Шире как **ecosystem/toolkit suite**
- Есть story вокруг **Slack bot / web UI / infra tooling**
- Может быть интереснее командам, которые хотят строить **несколько агентных продуктов на общей платформе**

### Где Artel выглядит сильнее

- Более явно оформлен как **coding-agent product**, а не только toolkit monorepo
- Есть более конкретный **ACP / MCP / remote TUI / rules / schedules** набор для dev workflow
- Локальный repo-centric сценарий выглядит более проработанным именно как coding assistant

### Вывод
pi-agent — это скорее comparator по линии **agent platform ecosystem**.
Artel — по центру между **toolkit** и **end-user coding product**.

---

## 3.5. Artel vs Aider

### Где Aider очень силён

- Лучший из сравниваемых по **git-centric workflow narrative**
- Репутация и positioning очень сфокусированы на **pair programming in your terminal**
- Сильные differentiated features:
  - repo map
  - auto commits
  - lint/test loop
  - IDE/watch mode
  - image/webpage context
  - voice workflow
- Очень ясный product message: Aider делает coding loop быстрее прямо сейчас

### Где Artel выглядит сильнее

- Намного шире как **platform/runtime**
- Лучше story для **MCP / ACP / server / remote**
- Есть встроенные **extensions**
- Есть **rules**, **schedules**, **worktrees**, delegation

### Где Artel уступает Aider

- Нет столь же чёткого **git-first developer loop** как основной product identity
- Меньше ощущение **battle-tested daily coding workflow**
- Меньше user-facing differentiation уровня repo map / auto-commit / lint-test-first identity

### Вывод
Если нужен лучший benchmark для **повседневной coding productivity**, смотреть в сторону Aider обязательно.
Если нужен **расширяемый агентный runtime**, Artel богаче как архитектура.

---

## 4. Capability matrix

Легенда:

- ✅ — явно есть и задокументировано/подтверждено
- ◑ — есть частично / не как finished product
- ? — по публичным материалам не удалось уверенно подтвердить
- ❌ — в текущем checkout отсутствует

## 4.1. Core platform

| Capability | Artel | Codex CLI | OpenCode | Kimi Code CLI | pi-agent | Aider |
|---|---|---:|---:|---:|---:|---:|
| Terminal-first agent | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Remote/server architecture | ✅ | ◑ | ✅ | ? | ◑ | ? |
| ACP support | ✅ | ? | ? | ✅ | ? | ? |
| MCP support | ✅ | ? | ✅ | ✅ | ? | ? |
| Multi-provider breadth | ✅ | ◑ | ✅ | ◑ | ✅ | ✅ |
| Native extension/plugin model | ✅ | ? | ? | ? | ✅ | ◑ |
| Optional/local model runtimes | ✅ | ? | ✅ | ? | ✅ | ✅ |

## 4.2. Product/workflow layer

| Capability | Artel | Codex CLI | OpenCode | Kimi Code CLI | pi-agent | Aider |
|---|---|---:|---:|---:|---:|---:|
| Built-in worktree workflow | ✅ | ? | ? | ? | ? | ? |
| Scheduling / recurring jobs | ✅ | ? | ? | ? | ? | ? |
| Rules / policy layer | ✅ | ? | ? | ? | ? | ? |
| Multi-agent/delegation story | ◑ | ? | ✅ | ? | ◑ | ? |
| Web/desktop story | ❌ / ◑ | ✅ | ✅ | ◑ | ✅ | ◑ |
| LSP story | ◑ | ? | ✅ | ? | ? | ? |
| Git-centric coding narrative | ◑ | ◑ | ◑ | ◑ | ? | ✅ |
| End-user UX polish | ◑ | ✅ | ✅ | ✅ | ◑ | ✅ |

### Комментарий к матрице

Главное наблюдение: **Artel выигрывает по platform breadth**, но пока не выигрывает по **single polished developer journey**.

---

## 5. На кого Artel сейчас больше всего похож

## Наиболее близкий аналог: OpenCode

Почему:

- terminal/TUI-first framing
- provider-agnostic model
- client/server direction
- LSP/MCP/agent architecture
- ambition идти дальше простого single-chat CLI

Но разница в том, что OpenCode сейчас выглядит более зрелым как **готовый продукт**, а Artel — как более ранний, но потенциально очень сильный **platform core**.

## Второй важный benchmark: Aider

Не потому что архитектура похожа, а потому что Aider задаёт стандарт по:

- понятности
- git-centric workflow
- реальной ежедневной полезности
- понятной ценности для разработчика за первые 5 минут

## Третий benchmark: Kimi Code CLI

Особенно полезен как benchmark по:

- ACP story
- editor integration messaging
- shell + agent UX

---

## 6. Где Artel объективно выигрывает уже сейчас

## 6.1. Если смотреть как на агентную платформу
Artel уже очень силён в комбинации:

- CLI + TUI + remote TUI + server + JSON-RPC + ACP
- provider breadth
- MCP runtime/config
- extension registry
- rules
- schedules
- worktree tooling
- delegation surface

У большинства OSS coding agents есть 2–4 из этих осей. У Artel их заметно больше.

## 6.2. Если смотреть как на базу для кастомного internal tool
Artel может быть особенно хорош для команд, которым нужно не просто “чат в терминале”, а:

- интегрировать агента в свою инфраструктуру
- добавлять custom tools/registries/extensions
- запускать headless/server mode
- строить automation вокруг prompts/schedules
- подключать editor/IDE клиентов через ACP

В этом сегменте Artel выглядит конкурентоспособно уже сейчас.

---

## 7. Где Artel проигрывает прямо сейчас

## 7.1. Product cohesion
По сравнению с Codex / OpenCode / Aider:

- слабее единая история “вот как этим пользоваться каждый день”
- слабее polished onboarding
- слабее packaging/distribution impression
- слишком заметен разрыв между architecture ambition и shipped UX

## 7.2. Orchestration promise vs implementation reality
Backlog обещает сильную differentiator-ось:

- dashboard
- orchestrator
- employee lifecycle
- cmux workspace model

Но текущий checkout этого ещё не воплощает как finished product. Это делает позиционирование менее убедительным относительно заявленной vision.

## 7.3. Web/desktop surface
Рынок уже привык, что coding agent либо:

- очень силён в terminal-only flow
- либо имеет понятную desktop/web/editor историю

У Artel web surface пока фактически выключена, а desktop story отсутствует.

---

## 8. Практические выводы для позиционирования проекта

## Если позиционировать Artel как “ещё один coding CLI”
Это будет слабее, чем у лидеров.

Почему:

- Codex сильнее по polish/onboarding
- Aider сильнее по developer loop identity
- OpenCode сильнее по цельности продукта
- Kimi сильнее по editor/ACP-facing simplicity

## Если позиционировать Artel как “extensible agent runtime for coding workflows”
Это уже намного сильнее.

Такой positioning лучше раскрывает реальные преимущества проекта:

- extensibility
- protocol support
- remote/server operation
- policy/rules
- automation/schedules
- MCP + ACP together
- multi-surface direction

## Если позиционировать Artel как “orchestrator for coding employees”
Пока рано.

Причина: backlog в эту сторону идёт, но текущий checkout ещё не даёт finished employee/dashboard product.

---

## 9. Что стоит сделать, чтобы реально выиграть у этих OSS tools

### Приоритет 1. Сжать platform power в один killer workflow
Нужен один главный answer на вопрос:

> “Почему разработчик должен взять Artel вместо Codex/OpenCode/Aider уже сегодня?”

Сейчас есть много сильных кирпичей, но нет одного максимально ясного флоу уровня:

- “лучший orchestration-first coding runtime”
- или “лучший remote-capable coding agent for teams”
- или “лучший extensible ACP+MCP coding platform”

### Приоритет 2. Довести orchestration story до продукта
Если это реальный differentiator, нужно завершить:

- employee lifecycle
- dashboard/orchestrator surfaces
- control-plane visibility
- surface focus/control
- real multi-agent execution model

Иначе эта ось остаётся обещанием, а не преимуществом.

### Приоритет 3. Улучшить end-user packaging
Benchmark: Codex / OpenCode / Kimi.

Нужны:

- более простой first-run
- более чёткий getting started
- более цельный messaging
- ясное разделение: local mode / remote mode / ACP / extensions / automation

### Приоритет 4. Усилить git/repo workflow identity
Benchmark: Aider.

Artel уже имеет worktree primitive, но можно сильнее упаковать:

- repo-aware planning
- tighter test/lint loop
- git-first summary/status UX
- clearer diff/review workflows

### Приоритет 5. Выбрать судьбу web/desktop surfaces
Сейчас это серое поле. Лучше либо:

- честно сфокусироваться на terminal/server/ACP product
- либо вернуть web как реальную first-class surface

Полу-состояние ухудшает восприятие.

---

## 10. Финальный вердикт

**Artel не проигрывает open-source coding agents по архитектурной глубине.**
Во многих аспектах он даже **богаче**:

- протоколы
- интеграции
- расширяемость
- серверность
- automation primitives

Но **Artel пока проигрывает лучшим OSS агентам по product finish**.

### Короткая оценка

- **Как платформа/ядро:** сильный проект, выше среднего по рынку
- **Как готовый end-user coding agent:** пока уступает лидерам
- **Как база для кастомных агентных workflows:** очень перспективен
- **Как orchestrator-first coding OS:** пока ещё в стадии становления

### Самая точная формулировка на сегодня

> **Artel сейчас сильнее как extensible coding-agent platform, чем как законченный polished coding-agent product.**

Именно это отличает его от Codex, OpenCode, Kimi CLI и Aider — и именно в этом его шанс занять свою нишу.
