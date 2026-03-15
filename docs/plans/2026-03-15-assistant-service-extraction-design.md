# Assistant Service Extraction Design

Date: 2026-03-15

## Context

The audit lineage in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` and the recent continuation docs have already reduced `apps/api/src/noa_api/api/routes/assistant.py` by extracting repository, command, streaming, error-parsing, tool-result shaping, and orchestration helpers.

The largest remaining backend hotspot is now the `AssistantService` that still lives in `apps/api/src/noa_api/api/routes/assistant.py`. Its remaining `add_tool_result(...)`, `approve_action(...)`, and `deny_action(...)` flows still mix:

- assistant-domain validation
- action-request and tool-run ownership checks
- persistence sequencing
- audit-log writes
- success-path structured logging
- tool lookup and execution
- FastAPI-shaped error construction

That makes the module harder to test and keeps route-facing HTTP shaping too close to assistant-domain behavior.

## Scope For This Pass

This pass intentionally covers only the first two recommended next steps from the audit:

1. Extract the remaining assistant approval, tool-result, and approved-tool-execution flows from `apps/api/src/noa_api/api/routes/assistant.py`.
2. Tighten assistant-domain error translation so less HTTP shaping lives directly in `apps/api/src/noa_api/api/routes/assistant.py`.

The broader backend follow-up items remain explicitly deferred:

- wider structured logging adoption across more success paths
- remaining non-assistant `error_code` normalization gaps
- telemetry decisions after the log/event field set stabilizes

## Goals

- Make `apps/api/src/noa_api/api/routes/assistant.py` materially smaller again.
- Move assistant-domain validation and operation flow out of the route module.
- Keep current assistant HTTP and SSE contracts stable, including `detail`, `error_code`, and fallback behavior.
- Create smaller seams that are easier to test directly than the current monolithic `AssistantService` branches.

## Non-goals

- Broad repo-wide logging cleanup.
- App-wide `error_code` catalog expansion outside assistant-adjacent work.
- Telemetry vendor adoption, tracing, or metrics.
- A full assistant package redesign.

## Approaches Considered

### 1) Keep `AssistantService` in place and only split private helpers

Pros:

- smallest file churn
- low rename risk

Cons:

- preserves most of the module coupling
- still leaves FastAPI-shaped errors inside the service layer
- does not create a clear seam for future assistant work

### 2) Extract assistant domain operations into focused helper modules (chosen)

Pros:

- directly attacks the remaining hotspot
- fits the current incremental extraction pattern in the repo
- allows assistant-specific tests to move closer to the extracted behavior
- creates a thinner HTTP translation boundary

Cons:

- touches multiple files and tests in one pass
- leaves broader backend logging and non-assistant cleanup for later

### 3) Replace the remaining assistant route/service shape with a larger application layer now

Pros:

- cleanest long-term architecture

Cons:

- too much churn for the current continuation
- higher review and regression risk
- mixes architectural redesign with operational cleanup goals

## Proposed Design

### 1) Keep `assistant.py` as the transport boundary and dependency assembly point

`apps/api/src/noa_api/api/routes/assistant.py` should keep only the responsibilities that are inherently route-facing:

- request and response models
- FastAPI dependency wiring
- route handlers and SSE startup
- thin wrappers around domain/application services
- top-level exception boundaries for HTTP and streaming behavior

The file should stop being the home for most assistant-domain validation and tool-execution branching.

### 2) Introduce extracted assistant operation modules for the remaining service flows

Add one or two focused modules adjacent to the existing helpers. The split should follow behavior, not framework boundaries:

- `assistant_action_operations.py` for action-request lookup, ownership validation, approval/denial transitions, audit-log writes, assistant message persistence, and approved-tool execution orchestration
- `assistant_tool_result_operations.py` for tool-call ID parsing, tool-run validation, completion, audit-log writes, and tool-result message persistence

If the code stays smaller and clearer in a single extracted module instead of two files, that is acceptable. The key requirement is that these flows no longer live inline inside `AssistantService` in `assistant.py`.

### 3) Replace route-local HTTP shaping inside assistant flows with assistant-domain exceptions

The extracted layer should stop constructing `assistant_http_error(...)` deep inside the flow. Instead it should:

- raise assistant-specific exceptions or small typed failure objects for domain cases such as missing thread ownership, missing action request, replayed decision, stale tool run, or unauthorized tool access
- keep assistant ID parsing centralized through the existing `assistant_errors.py` helpers or move those helpers to return domain-safe exceptions consistently
- translate those failures to the existing `detail` and `error_code` values at one thin boundary close to the route or service facade

This keeps FastAPI concerns from leaking back into the domain helpers while preserving the external contract exactly.

### 4) Separate approved-tool execution from approval-state transitions

`approve_action(...)` currently validates the request, marks it approved, starts a tool run, emits an assistant tool-call message, executes the tool, sanitizes failures, persists the tool-result message, and writes multiple audit records.

That should be broken into two layers:

- action approval orchestration: validate, approve, start run, persist the assistant tool-call message, bind operation context
- approved tool execution helper: resolve the tool, verify `ToolRisk.CHANGE`, execute with session injection when supported, sanitize failures, persist tool-result payloads, and write success/failure audit entries

This split mirrors the actual lifecycle and makes the error cases easier to pin with focused tests.

### 5) Bind logging context once per assistant operation

This pass does not aim for broader backend logging expansion, but the extracted assistant helpers should bind consistent context at the operation seam instead of repeating large `extra={...}` payloads in every branch.

The important fields remain:

- `user_id`
- `thread_id`
- `action_request_id`
- `tool_name`
- `tool_run_id`

Success-path logs such as `assistant_action_approved`, `assistant_action_denied`, and `assistant_tool_result_recorded` should keep their current event names and fields.

### 6) Preserve existing behavior boundaries

This pass is structural. It must preserve:

- stable assistant `detail` strings
- stable assistant `error_code` values
- the current request/response schema
- pre-stream HTTP failure behavior
- in-stream fallback behavior
- `asyncio.CancelledError` passthrough behavior
- sanitized tool failure persistence and user-visible tool-result payloads

## Module Shape

One pragmatic target shape is:

- `apps/api/src/noa_api/api/routes/assistant.py`
  - route handlers
  - `AssistantService` reduced to load-state, add-message, run-agent-turn, and thin delegation methods
- `apps/api/src/noa_api/api/routes/assistant_action_operations.py`
  - assistant action validation
  - approve/deny orchestration
  - approved tool execution path
- `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py`
  - tool-result validation and completion flow
- `apps/api/src/noa_api/api/routes/assistant_errors.py`
  - stable translation helpers between assistant-domain failures and HTTP exceptions

The exact filenames can vary if a single extracted module is cleaner, but the ownership boundaries should remain the same.

## Data Flow

### Tool-result flow

1. Parse `toolCallId`.
2. Load the tool run.
3. Validate ownership, thread match, and `STARTED` status.
4. Complete the tool run.
5. Persist the `tool` message using `build_tool_result_part(...)`.
6. Write the `tool_completed` audit log.
7. Emit the existing structured success log.

### Approve flow

1. Validate active user, parse `actionRequestId`, and load the action request.
2. Validate ownership, pending status, `ToolRisk.CHANGE`, and tool authorization.
3. Approve the action request and start the tool run.
4. Persist the assistant `tool-call` message.
5. Write `action_approved` and `tool_started` audit logs.
6. Execute the approved tool through the extracted helper.
7. Persist the resulting success or sanitized failure `tool-result` message and audit event.

### Deny flow

1. Parse `actionRequestId` and load the action request.
2. Validate ownership and pending status.
3. Deny the action request.
4. Persist the assistant denial text message.
5. Write the `action_denied` audit log.
6. Emit the existing structured success log.

## Error Handling

- Keep malformed or missing assistant IDs mapped to the current assistant error codes.
- Keep ownership mismatches mapped to the same not-found behavior as today.
- Keep replayed approval or denial decisions mapped to the same conflict behavior.
- Keep approved-tool execution failures sanitized through `sanitize_tool_error(...)` and internal exception logging.
- Avoid returning raw exception strings in persisted or user-visible payloads.

## Testing Strategy

- Expand helper-level coverage around the extracted assistant operation module(s) instead of growing only route tests.
- Keep route-level characterization tests only for transport guarantees and stable HTTP/SSE contracts.
- Preserve the current focused service tests, but move assertions toward extracted helper seams where possible.
- Verify both success-path structured logging and sanitized failure behavior after extraction.

## Acceptance Criteria

- `apps/api/src/noa_api/api/routes/assistant.py` is materially smaller and no longer contains the large approval/result branches inline.
- Remaining assistant-domain error translation is centralized behind assistant-focused helpers rather than scattered through the route module.
- Existing assistant `detail`, `error_code`, and SSE fallback behavior remain unchanged.
- Focused tests cover extracted assistant operation seams directly.
- Wider backend logging and non-assistant `error_code` work remains explicitly documented as a later follow-up, not mixed into this pass.

## Follow-up After This Pass

Once the assistant seam stabilizes, the next backend-only follow-up should cover:

1. broader `log_context(...)` adoption across more backend success paths
2. the remaining selective non-assistant `error_code` gaps
3. telemetry reconsideration only after the structured log/event field set is stable
