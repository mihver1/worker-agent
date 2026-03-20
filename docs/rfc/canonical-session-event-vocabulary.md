# Canonical session event vocabulary

## Status

Draft

## Purpose

Define one shared vocabulary for Artel session/runtime events so that:

- ACP can be the canonical external contract
- remote transports can project the same semantics without inventing a parallel model
- first-party clients can render one runtime model across local, remote, and ACP-backed flows

This document is intentionally about **runtime semantics**, not about transport details or UI rendering.

## Scope

This vocabulary covers:

- session lifecycle notifications relevant to interactive clients
- turn lifecycle events
- tool lifecycle events
- permission and run-control events
- metadata/state updates needed by clients during a session

It does not try to define:

- server-administration APIs
- UI-only layout/focus concerns
- transport framing
- persistence formats

## Principles

1. **Semantic first**: event names should describe runtime meaning, not transport implementation.
2. **Transport independent**: the same event should be representable over ACP, WebSocket, local IPC, or in-process adapters.
3. **Client useful**: each event should support real client rendering or control decisions.
4. **Composable**: the vocabulary should map cleanly to existing Artel runtime behavior.
5. **Extensible**: Artel-specific additions should not pollute the core vocabulary unless they reflect real shared semantics.

## Event groups

## 1. Session bootstrap and metadata events

### `session.available_commands`

Meaning:

- the runtime advertises commands or quick actions available for this session

Used by clients to:

- populate command menus
- offer discoverable shortcuts
- distinguish built-in capabilities from freeform prompt text

ACP mapping:

- `available_commands_update`

Remote/WebSocket mapping today:

- no equivalent today

### `session.info`

Meaning:

- session metadata changed

Fields may include:

- title
- updated timestamp

ACP mapping:

- `session_info_update`

Remote/WebSocket mapping today:

- `session_updated`

### `session.config`

Meaning:

- the set of session-scoped runtime options changed

Examples:

- mode
- model
- thinking level
- future session-scoped controls

ACP mapping:

- `config_option_update`
- `current_mode_update`
- model state in session responses

Remote/WebSocket mapping today:

- mostly fetched via REST side effects rather than streamed updates

### `session.usage`

Meaning:

- session usage or context-window state changed in a client-relevant way

Fields may include:

- used tokens in context
- total context size
- optional cost
- optional turn usage totals

ACP mapping:

- `usage_update`
- `PromptResponse.usage`

Remote/WebSocket mapping today:

- `done` payload with usage
- local-only context estimation logic in TUI

## 2. Turn lifecycle events

### `turn.started`

Meaning:

- a new user turn has been accepted and runtime processing has started

Fields may include:

- session id
- turn id if Artel adopts stable turn identifiers
- input summary or content type metadata

ACP mapping:

- not explicitly represented today; mostly implied by subsequent updates

Remote/WebSocket mapping today:

- implied by `status: thinking`

Note:

This may remain implicit if explicit turn start adds no real client value.

### `turn.user_input`

Meaning:

- user prompt content is being surfaced or replayed to the client

Used mainly for:

- history replay
- synchronized client state

ACP mapping:

- `user_message_chunk`

Remote/WebSocket mapping today:

- usually not streamed during live turns; mainly reconstructed through REST history fetches

### `turn.assistant_text_delta`

Meaning:

- assistant visible output text incrementally grew

ACP mapping:

- `agent_message_chunk`

Remote/WebSocket mapping today:

- `text_delta`

### `turn.assistant_reasoning_delta`

Meaning:

- assistant reasoning/thought stream incrementally grew

ACP mapping:

- `agent_thought_chunk`

Remote/WebSocket mapping today:

- `reasoning_delta`

### `turn.completed`

Meaning:

- the turn ended normally or reached a terminal stop condition

Fields may include:

- stop reason
- optional final turn usage

ACP mapping:

- `PromptResponse.stop_reason`
- optional `PromptResponse.usage`

Remote/WebSocket mapping today:

- `done`

### `turn.failed`

Meaning:

- the turn ended with an error that should be rendered to the client

Fields may include:

- error text
- structured error class in the future
- whether the run is terminal or recoverable

ACP mapping:

- currently surfaced as message/error-like updates plus final stop outcome

Remote/WebSocket mapping today:

- `error`

### `turn.cancelled`

Meaning:

- the active turn was intentionally interrupted

ACP mapping:

- `PromptResponse.stop_reason = cancelled`

Remote/WebSocket mapping today:

- implied through abort behavior and terminal status flow

## 3. Tool lifecycle events

### `tool.started`

Meaning:

- a tool invocation has been created and is pending or in progress

Fields:

- tool call id
- title
- kind
- raw input
- optional file locations
- initial status

ACP mapping:

- `tool_call`

Remote/WebSocket mapping today:

- `tool_call`

### `tool.updated`

Meaning:

- a tracked tool invocation changed status or content

Fields may include:

- status
- content
- raw output
- updated title

ACP mapping:

- `tool_call_update`

Remote/WebSocket mapping today:

- no distinct update event; completion is usually a separate `tool_result`

### `tool.completed`

Meaning:

- tool finished successfully

Fields may include:

- tool call id
- rendered output
- raw output

ACP mapping:

- `tool_call_update` with completed status

Remote/WebSocket mapping today:

- `tool_result` with `is_error = false`

### `tool.failed`

Meaning:

- tool finished with failure

Fields may include:

- tool call id
- error output
- raw output

ACP mapping:

- `tool_call_update` with failed status

Remote/WebSocket mapping today:

- `tool_result` with `is_error = true`

## 4. Permission and run-control events

### `permission.requested`

Meaning:

- runtime needs user/client approval before a protected action continues

Fields:

- session id
- request id or tool call id
- tool details
- available options

ACP mapping:

- `session/request_permission`

Remote/WebSocket mapping today:

- `permission_request`

### `permission.resolved`

Meaning:

- permission decision has been applied and the run may continue or stop

Fields may include:

- allow/deny
- remember/apply-to-session outcome

ACP mapping:

- currently implicit in the request/response interaction and subsequent tool status update

Remote/WebSocket mapping today:

- implicit in `approve_tool` request handling and subsequent stream behavior

### `run.status`

Meaning:

- coarse-grained activity state changed

Examples:

- thinking
- responding
- running tool X
- idle

ACP mapping:

- mostly derived from finer-grained updates today, not a dedicated standard event

Remote/WebSocket mapping today:

- `status`

Note:

This is useful for UI responsiveness, but may remain a derived/client-local concept rather than canonical protocol data.

### `run.steer_requested`

Meaning:

- user/client sent steering input to the active run

ACP mapping:

- none today

Remote/WebSocket mapping today:

- `steer`

Note:

This remains an open design area and may require an ACP extension.

## 5. Auxiliary session events

### `session.board_changed`

Meaning:

- shared tasks/notes state changed due to runtime action

ACP mapping:

- none today

Remote/WebSocket mapping today:

- `board_event`

Note:

This may remain Artel-specific and outside the minimal canonical session vocabulary unless multiple clients need it as a real-time signal.

### `session.compacted`

Meaning:

- session history was compacted automatically or manually

ACP mapping:

- currently rendered as assistant/tool text update, not a dedicated event type

Remote/WebSocket mapping today:

- custom `compact`-style runtime message behavior exists indirectly

## Canonical minimal vocabulary

For the first ACP-native convergence pass, the minimum shared vocabulary should be:

- `session.available_commands`
- `session.info`
- `session.config`
- `session.usage`
- `turn.user_input`
- `turn.assistant_text_delta`
- `turn.assistant_reasoning_delta`
- `turn.completed`
- `turn.failed`
- `turn.cancelled`
- `tool.started`
- `tool.updated`
- `tool.completed`
- `tool.failed`
- `permission.requested`

This set covers almost all of Artel's existing multi-surface runtime behavior.

## Mapping summary

| Canonical semantic event | ACP today | Remote WS today | Local TUI today |
|---|---|---|---|
| `session.available_commands` | `available_commands_update` | none | local slash-command table |
| `session.info` | `session_info_update` | `session_updated` | direct local session/store updates |
| `session.config` | `config_option_update`, `current_mode_update` | mostly REST fetch/update side effects | direct local state mutation |
| `session.usage` | `usage_update`, `PromptResponse.usage` | `done.usage` + `status` | direct local footer/context updates |
| `turn.user_input` | `user_message_chunk` (replay) | history fetch path | local restored messages |
| `turn.assistant_text_delta` | `agent_message_chunk` | `text_delta` | `AgentEventType.TEXT_DELTA` |
| `turn.assistant_reasoning_delta` | `agent_thought_chunk` | `reasoning_delta` | `AgentEventType.REASONING_DELTA` |
| `turn.completed` | `PromptResponse.stop_reason=end_turn` | `done` | `AgentEventType.DONE` |
| `turn.failed` | message/error path | `error` | `AgentEventType.ERROR` |
| `turn.cancelled` | `PromptResponse.stop_reason=cancelled` | abort flow | abort flow |
| `tool.started` | `tool_call` | `tool_call` | `AgentEventType.TOOL_CALL` |
| `tool.updated` | `tool_call_update` | none distinct | implicit local card mutation |
| `tool.completed` | `tool_call_update(status=completed)` | `tool_result is_error=false` | `AgentEventType.TOOL_RESULT` |
| `tool.failed` | `tool_call_update(status=failed)` | `tool_result is_error=true` | `AgentEventType.TOOL_RESULT` |
| `permission.requested` | `session/request_permission` | `permission_request` | direct callback/UI panel |

## Recommendations

### 1. Prefer canonical semantics over transport event names

Client code should increasingly target these semantic events rather than raw protocol names.

### 2. Treat remote `status` as advisory

`status` is useful for UX, but the durable canonical model should come from turn/tool/session semantics.

### 3. Model tool completion/failure as status transitions

ACP's model is better here than the current remote split between `tool_call` and `tool_result`.

That suggests the remote transport should move closer to the ACP shape over time.

### 4. Keep Artel-specific additions explicit

Events like steering and board updates should be named as explicit extensions rather than mixed into the core runtime vocabulary implicitly.

## Next step after this doc

Use this vocabulary to:

- normalize remote transport semantics
- guide ACP extension design where needed
- evaluate a local ACP-backed TUI prototype against one semantic contract rather than multiple runtime-specific code paths
