# Backend Non-Assistant Logging and Error Code Design

Date: 2026-03-15

## Context

The audit lineage in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` already established request-scoped IDs, centralized JSON error shaping, assistant/admin/threads/WHM/auth route error-code coverage for their previously touched failure paths, and structured logging for the assistant and auth boundary slices.

After the auth-boundary continuation, the remaining backend follow-up is narrower than a new route expansion. The active gaps are now:

1. broader structured success-path logging adoption outside the assistant/auth-focused flows
2. a small number of remaining non-assistant `error_code` gaps in generic validation shaping rather than in the already-refreshed route-specific business errors

This pass should stay intentionally small and additive. It should use the event vocabulary and context-binding patterns already established in the current backend slice rather than introducing a larger logging or exception redesign.

## Goals

- Add structured success-path logs across the remaining non-assistant route families in `admin.py`, `threads.py`, and `whm_admin.py`.
- Reuse `log_context(...)` so successful route events bind stable request/entity identifiers consistently with the existing auth and assistant logging model.
- Add one shared stable `error_code` for FastAPI request validation failures returned through the centralized error handler.
- Preserve current user-facing `detail` payloads and status codes.

## Non-goals

- Expanding backend logging to every helper, repository, or integration in this pass.
- Redesigning the application-wide exception taxonomy.
- Changing assistant flows.
- Adding telemetry vendors such as OpenTelemetry or Sentry.
- Normalizing every possible helper-level validation error into a large new catalog.

## Approaches Considered

### 1) Route success logging plus shared validation error code (chosen)

Add route-level success logs in the remaining non-assistant route modules and introduce a single shared `error_code` for centralized 422 validation responses.

Pros:

- directly advances both active recommendations from the audit without broadening scope
- keeps the implementation localized to the API layer
- avoids a larger helper/error taxonomy redesign
- provides a stable next-step handoff for any future repo-wide logging expansion

Cons:

- leaves some helper-level errors outside a shared catalog for now
- requires light test expansion across several route families

### 2) Route success logging only

Add only structured success logs to the remaining non-assistant route modules and defer all error-code follow-up.

Pros:

- lowest implementation risk
- very small change set

Cons:

- leaves one of the explicit audit follow-ups unresolved
- requires another continuation just to close the shared validation contract gap

### 3) Broader error-code normalization across untouched helpers

Try to inventory and normalize every remaining non-assistant helper and validation failure path in the same pass.

Pros:

- more exhaustive error-code coverage

Cons:

- pushes this pass into a larger redesign
- increases risk of accidental contract drift
- mixes route logging work with exception-taxonomy decisions that are not yet needed

## Proposed Design

### 1) Extend success-path logging in `admin.py`

Add success events to the route handlers in `apps/api/src/noa_api/api/routes/admin.py` using the existing stdlib logger plus `log_context(...)`.

Recommended event set:

- `admin_users_list_succeeded`
- `admin_user_active_updated`
- `admin_tools_list_succeeded`
- `admin_user_tools_updated`

Recommended safe fields:

- `user_id` for the acting admin
- `target_user_id` where a target user is being changed
- `user_count` and `tool_count` for list endpoints
- `is_active` for user activation changes
- `assigned_tool_count` for tool updates

These logs should describe successful outcomes only. Existing rejection logs such as `admin_access_denied`, `admin_user_not_found`, and conflict events should stay intact.

### 2) Extend success-path logging in `threads.py`

Add success events to the route handlers in `apps/api/src/noa_api/api/routes/threads.py`.

Recommended event set:

- `threads_list_succeeded`
- `thread_created`
- `thread_reused`
- `thread_retrieved`
- `thread_title_updated`
- `thread_archived`
- `thread_unarchived`
- `thread_deleted`
- `thread_title_generated`
- `thread_title_returned_existing`

Recommended safe fields:

- `user_id`
- `thread_id`
- `thread_count` for list endpoints
- `external_id_present` for create flows when useful
- `generated_title_source` with a small stable discriminator such as `request_messages`, `persisted_messages`, or `existing_title`

The create flow should distinguish between a newly created thread and an idempotent reuse, since that difference is operationally useful and already available from the current service contract.

### 3) Extend success-path logging in `whm_admin.py`

Add success events to the route handlers in `apps/api/src/noa_api/api/routes/whm_admin.py`.

Recommended event set:

- `whm_servers_list_succeeded`
- `whm_server_created`
- `whm_server_updated`
- `whm_server_deleted`
- `whm_server_validated`

Recommended safe fields:

- `user_id`
- `server_id`
- `server_name`
- `server_count` for list endpoints
- `validation_ok`
- `validation_error_code` when the validation endpoint returns a non-ok structured result

The validation route already returns a structured response instead of exceptions for expected integration failures. Success-path logging should preserve that model and log the returned validation outcome without leaking secrets such as API tokens.

### 4) Add one shared request-validation error code

Extend `apps/api/src/noa_api/api/error_codes.py` with a single shared constant for request validation failures, and have `apps/api/src/noa_api/api/error_handling.py` use it from `request_validation_exception_handler(...)`.

Recommended constant:

- `REQUEST_VALIDATION_ERROR = "request_validation_error"`

This keeps the current 422 response shape intact while making validation failures consistent with the rest of the shaped backend error envelope:

- preserve `status_code == 422`
- preserve the existing `detail` list from `exc.errors()`
- add a stable `error_code`
- continue including `request_id`

This pass should stop there. It should not attempt to individually catalog every validation subtype.

### 5) Keep the event vocabulary narrow and reusable

This pass should continue using the already-established logging model:

- middleware binds `request_id`, `request_method`, and `request_path`
- route helpers bind stable entity identifiers with `log_context(...)`
- event names are concise and outcome-oriented
- extras contain only safe scalars and small lists already returned by the route or request model

The stable field vocabulary for this continuation should remain:

- `request_id`
- `request_method`
- `request_path`
- `user_id`
- `target_user_id`
- `thread_id`
- `server_id`
- `server_name`
- `status_code`
- `error_code`

This gives later logging follow-up work a consistent shape without requiring a new helper abstraction.

## Module Shape

Target shape after this pass:

- `apps/api/src/noa_api/api/routes/admin.py`
  - existing failure logs preserved
  - additive success logs for list/update operations
- `apps/api/src/noa_api/api/routes/threads.py`
  - existing failure logs preserved
  - additive success logs for list/create/read/update/archive/delete/title flows
- `apps/api/src/noa_api/api/routes/whm_admin.py`
  - existing failure logs preserved
  - additive success logs for list/create/update/delete/validate flows
- `apps/api/src/noa_api/api/error_codes.py`
  - add `REQUEST_VALIDATION_ERROR`
- `apps/api/src/noa_api/api/error_handling.py`
  - centralized 422 responses include the new shared `error_code`

## Data Flow

### Admin success flow

1. Route resolves the acting admin through existing dependencies.
2. Route performs the requested list or mutation.
3. On success, bind any relevant route/entity identifiers if not already bound.
4. Emit a success event with counts or simple state fields.
5. Return the existing response payload unchanged.

### Threads success flow

1. Route resolves the active user.
2. Route loads or mutates thread state through `ThreadService`.
3. On success, emit a route event that distinguishes newly created, reused, existing-title, and generated-title paths where relevant.
4. Return the existing response payload unchanged.

### WHM admin success flow

1. Route resolves the acting admin.
2. Route performs the requested CRUD or validation operation.
3. On success, emit a safe event with server identifiers and validation outcome fields.
4. Return the existing response payload unchanged.

### Request validation failure flow

1. FastAPI raises `RequestValidationError` before route logic completes.
2. `request_validation_exception_handler(...)` shapes the response through the centralized JSON envelope.
3. The handler returns the existing `detail` list plus `request_id` and the new stable `error_code`.

## Error Handling

- Do not change existing route `detail` strings or status codes.
- Do not replace current business-error codes in admin, threads, or WHM admin routes.
- Only add the shared request-validation `error_code` in the centralized 422 handler.
- Do not log secrets or raw external exception text.
- Keep rejection/failure logging separate from the new success events.

## Testing Strategy

- Extend focused backend tests for at least one success-path structured log in each touched route family:
  - `apps/api/tests/test_rbac.py` for admin routes
  - `apps/api/tests/test_threads.py` for threads routes
  - `apps/api/tests/test_whm_admin_routes.py` for WHM admin routes
- Add or update one validation-envelope test to prove 422 responses now include `error_code == "request_validation_error"` while preserving the existing `detail` structure.
- Reuse the existing structured log capture approach from the auth/request-context tests instead of introducing a new capture helper unless a tiny shared test helper is clearly worthwhile.
- Run targeted backend tests for the touched route families and request-context/error-shaping coverage first, then run the full backend suite and Ruff.

## Acceptance Criteria

- Successful non-assistant admin, threads, and WHM admin route paths emit structured success events with stable safe context.
- Existing non-assistant route failure contracts remain unchanged.
- Centralized 422 validation responses now include a stable shared `error_code` while preserving the current `detail` payload.
- No secrets are added to logs.
- Telemetry remains deferred; no new vendor dependency is introduced.
