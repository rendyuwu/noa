# Backend Error Codes, Assistant Decomposition, and Structured Logging Design

Date: 2026-03-14

## Context

The audit in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` already drove a useful first pass on request IDs, centralized API error envelopes, shared DB engine/session access, tool-failure sanitization, and initial assistant-route extraction.

The remaining backend gaps are now narrower and clearer:

- stable `error_code` coverage still drops off outside auth and tool-failure flows
- `apps/api/src/noa_api/api/routes/assistant.py` still mixes request validation, command application, streaming-state management, orchestration, and failure recovery
- backend logging is JSON-capable, but contextual fields are not bound consistently enough to make incidents easy to trace
- backend telemetry tooling is not present, so richer structured logs are the best immediate observability win

This design is intentionally backend-only. It improves contracts and observability without adding a telemetry vendor yet.

## Goals

- Expand stable API `error_code` coverage to the next highest-value backend routes while preserving current `detail` strings.
- Keep shrinking `apps/api/src/noa_api/api/routes/assistant.py` through focused extractions instead of a large rewrite.
- Add richer structured logging context for assistant and route flows so logs consistently include request and entity identifiers.
- Update the audit report as part of the same pass so branch status remains easy to track.

## Non-goals

- Installing `Sentry`, `OpenTelemetry`, or another backend telemetry vendor in this pass.
- Converting every `HTTPException` in the repository at once.
- Replacing the current assistant service boundary with a brand-new package architecture.
- Changing user-visible `detail` prose that existing tests or frontend logic may still depend on.

## Approach Options Considered

### 1) Route-only error-code rollout

Add `error_code` values to more routes and stop there.

Pros:

- lowest implementation risk
- quick contract win for backend clients

Cons:

- leaves the assistant route oversized
- does little for production diagnosis beyond response payloads

### 2) Incremental backend hardening (chosen)

Expand route `error_code` coverage, extract assistant command/streaming helpers, and add structured logging-context helpers in the same pass.

Pros:

- best fit for the branch's existing partial foundation work
- improves contracts, maintainability, and observability together
- keeps changes incremental and testable

Cons:

- touches multiple backend areas at once
- still leaves some future assistant refactoring for later passes

### 3) Telemetry-first platform move

Adopt a backend telemetry stack now and postpone most contract/refactor work.

Pros:

- strongest long-term observability story

Cons:

- adds operational/tooling complexity before the route boundaries settle
- risks instrumenting unstable seams that will move again soon

## Proposed Design

### 1) Expand stable API error-code coverage with a shared backend catalog

Keep `ApiHTTPException` in `apps/api/src/noa_api/api/error_handling.py` as the route-facing mechanism, but add a small shared catalog module for stable backend error codes.

The catalog should start small and only cover real cases in the touched routes, for example:

- `user_pending_approval`
- `admin_access_required`
- `thread_not_found`
- `unknown_tools`
- `last_active_admin`
- `self_deactivate_admin`
- `whm_server_not_found`
- `whm_server_name_exists`
- assistant validation/action-state codes such as `message_edit_not_supported` and `invalid_add_message_role`

The migration target for this pass is:

- `apps/api/src/noa_api/api/routes/threads.py`
- `apps/api/src/noa_api/api/routes/admin.py`
- `apps/api/src/noa_api/api/routes/whm_admin.py`
- selected validation and action-state branches in `apps/api/src/noa_api/api/routes/assistant.py`

The rule is additive only: keep current `detail` text unchanged, add `error_code` alongside it.

### 2) Continue assistant-route decomposition by orchestration concern

Do not rewrite `apps/api/src/noa_api/api/routes/assistant.py` wholesale.

Instead, extract the two biggest remaining seams:

- `assistant_commands.py`
  - validate supported command shapes before execution
  - apply approved commands to `AssistantService`
  - centralize assistant-specific `ApiHTTPException` mappings for command/action errors
- `assistant_streaming.py`
  - own streaming placeholder creation/removal
  - manage safe fallback-message appends
  - centralize controller state flush logic and final state refresh behavior

After this extraction, `assistant.py` should mainly define models, dependencies, and the thin transport/controller flow.

### 3) Add structured logging context helpers and use event-style logs

The current logging setup in `apps/api/src/noa_api/core/logging.py` already renders JSON and injects `request_id`, but most call sites still use interpolated stdlib strings.

This pass should add a helper module, likely `apps/api/src/noa_api/core/logging_context.py`, that:

- binds only non-`None` context values
- resets bound fields safely after a scope exits
- gives routes/services a simple way to attach `user_id`, `thread_id`, `tool_name`, `tool_run_id`, and `action_request_id`

Use the helper first in assistant flows, then in the non-auth routes touched for `error_code` adoption where logs add operational value.

The log style should shift from string-interpolated messages to event-oriented structured logs, for example:

```python
logger.info(
    "assistant_run_failed_pre_agent",
    status_code=exc.status_code,
    thread_id=str(payload.thread_id),
    user_id=str(current_user.user_id),
)
```

This keeps JSON output queryable and consistent.

### 4) Add request/operation logging, not a telemetry vendor

Backend-only observability for this pass means richer logs, not a new platform dependency.

That includes:

- request-scoped identifiers already in place
- request/operation completion logs where useful
- assistant/tool/action failure logs with bound context
- no new backend exporter, collector, or tracing SDK yet

Telemetry vendor adoption remains explicitly deferred until the assistant split stabilizes and the desired event fields are clearer.

### 5) Keep report tracking as part of the deliverable

This pass should end by updating `docs/reports/2026-03-14-error-handling-and-logging-audit.md` so it clearly records:

- what code work was completed in the follow-up pass
- what still remains not done
- what should come next in backend-only priority order

That keeps the branch audit useful as a living status document instead of a stale snapshot.

## Rollout Order

1. add failing route tests for expanded `error_code` coverage
2. add a small shared backend error-code catalog and migrate `threads.py`, `admin.py`, and `whm_admin.py`
3. add failing tests for extracted assistant command and streaming helpers
4. extract `assistant_commands.py` and `assistant_streaming.py`, then wire `assistant.py` through them
5. add failing tests for structured logging-context helpers
6. add logging-context helpers and convert assistant/touched-route logs to event-style structured logging
7. update the audit report with done / not yet / next

## Testing Strategy

- extend route tests in `apps/api/tests/test_threads.py`, `apps/api/tests/test_rbac.py`, `apps/api/tests/test_whm_admin_routes.py`, and `apps/api/tests/test_assistant.py` to assert both `detail` and `error_code`
- ensure the test apps install centralized error handling where needed so `request_id` and shaped error responses are present
- add focused helper tests for extracted assistant modules instead of testing every new branch only through the monolithic transport callback
- add cheap, deterministic tests for logging-context binding helpers rather than snapshotting rendered log JSON
- rerun the assistant route/service tests after extraction to preserve current behavior

## Acceptance Criteria

- more intentional backend route errors return stable `error_code` values beyond auth/tool flows
- `apps/api/src/noa_api/api/routes/assistant.py` is materially smaller because command and streaming logic moved out
- assistant and related route logs consistently include structured identifiers such as `request_id`, `user_id`, and `thread_id`
- backend telemetry tooling is still deferred, but logs are rich enough to support the next production debugging pass
- `docs/reports/2026-03-14-error-handling-and-logging-audit.md` reflects the new done / not yet / next state after implementation
