# Error Handling and Logging Audit (Project NOA)

Original audit date: 2026-03-14

Scope:
- Backend: `apps/api` (FastAPI + SQLAlchemy async)
- Frontend: `apps/web` (Next.js + assistant-ui)

Original 2026-03-14 audit method:
- Static inspection of the code paths that implement request handling, authentication, assistant transport/streaming, persistence, and client-side API access.
- No runtime testing, no production log sampling.

Original 2026-03-14 high-level assessment:
- Error handling: solid baseline; mostly intentional and consistent at route boundaries.
- Logging/observability: present but minimal; currently insufficient for reliable production diagnosis.
- Maintainability: moderate; biggest risk is a large multi-responsibility assistant transport route and inconsistent logging patterns.

Implementation status update (branch lineage):
- Core foundation work from this audit has been partially implemented in `feat/error-handling-logging-foundation`.
- Request-scoped IDs, centralized API error shaping, shared DB engine/session accessors, tool-failure sanitization, frontend shared error mapping, and a top-level web error boundary are now in place.
- The backend-only follow-up branch `feat/backend-error-code-assistant-logging` has now implemented the next planned pass plus a continuation cleanup: wider route-level `error_code` coverage, additional assistant transport extraction, stable assistant ID error contracts for malformed/missing `toolCallId` and `actionRequestId`, and richer structured logging context for the touched backend flows.
- The separate continuation branch `feat/assistant-route-decomposition-continuation` records the next backend-only assistant pass: assistant orchestration now has its own `assistant_operations.py` seam, helper-level tests cover the extracted flow directly, and `assistant.py` is closer to a transport coordinator than before.
- The branch `feat/assistant-service-extraction` completed the next planned assistant slice: extracted assistant action/tool-result operation seams, a thinner assistant-domain HTTP translation boundary, and refreshed verification/handoff docs for that pass.
- The current backend-only branch `feat/backend-auth-boundary-logging` completes the next deferred non-assistant slice: shared auth dependency extraction into the API layer, shared auth error-code catalog coverage across login and protected-route auth failures, structured auth boundary success/rejection logs, and refreshed verification/handoff docs for this pass.
- The remaining notable gaps now center on broader repo-wide structured logging adoption outside the auth and previously refreshed backend flows, the remaining selective non-assistant `error_code` follow-up outside the currently covered route surface, and deferred telemetry reconsideration after the current log/event field set stabilizes.
- Backend-only follow-up docs now live in `docs/plans/2026-03-14-backend-error-code-assistant-logging-design.md`, `docs/plans/2026-03-14-backend-error-code-assistant-logging-implementation-plan.md`, `docs/plans/2026-03-14-backend-error-code-assistant-logging-continuation-implementation-plan.md`, `docs/plans/2026-03-15-assistant-route-decomposition-continuation-design.md`, `docs/plans/2026-03-15-assistant-route-decomposition-continuation-implementation-plan.md`, `docs/plans/2026-03-15-assistant-service-extraction-design.md`, `docs/plans/2026-03-15-assistant-service-extraction-implementation-plan.md`, `docs/plans/2026-03-15-backend-auth-boundary-logging-design.md`, and `docs/plans/2026-03-15-backend-auth-boundary-logging-implementation-plan.md`.

## 2026-03-15 Continuation Pass: Auth Boundary Logging and Error Code Coverage

What was done in this continuation pass
- Added `apps/api/src/noa_api/api/auth_dependencies.py` so bearer-token validation, JWT subject parsing, auth-user lookup, and route-facing auth failure translation now live in the API layer instead of `apps/api/src/noa_api/core/auth/authorization.py`.
- Simplified `apps/api/src/noa_api/core/auth/authorization.py` so it keeps authorization-domain types and services while dropping the request-facing `get_current_auth_user(...)` dependency.
- Rewired `apps/api/src/noa_api/api/routes/auth.py`, `apps/api/src/noa_api/api/routes/admin.py`, `apps/api/src/noa_api/api/routes/threads.py`, `apps/api/src/noa_api/api/routes/whm_admin.py`, and `apps/api/src/noa_api/api/routes/assistant.py` to use the shared API-layer auth dependency seam.
- Expanded `apps/api/src/noa_api/api/error_codes.py` with shared auth constants for `invalid_credentials`, `authentication_service_unavailable`, `missing_bearer_token`, and `invalid_token`, and updated auth flows to consume the catalog instead of inline literals.
- Added structured auth boundary logs for `auth_login_succeeded`, `auth_login_rejected`, `auth_me_succeeded`, `auth_current_user_resolved`, and `auth_current_user_rejected` with safe request/user context and no secret-bearing fields.
- Extended focused backend coverage in `apps/api/tests/test_auth_login.py`, `apps/api/tests/test_rbac.py`, `apps/api/tests/test_request_context.py`, plus the auth-dependency import follow-up in the related protected-route suites.

What is not yet done
- Broader backend structured logging adoption is still follow-up work outside the auth boundary and the previously refreshed assistant/admin/threads/WHM slices.
- Stable `error_code` coverage is stronger across auth plus the already-covered route surfaces, but selective gaps still remain in untouched routes and generic/helper-level validation paths.
- Telemetry reconsideration remains deferred until the current structured log/event field set stabilizes.

What should come next
- Continue the backend-only follow-up by extending `log_context(...)` adoption across more non-auth success paths using the now-stabilized auth event vocabulary.
- Close the remaining selective non-assistant `error_code` gaps outside the currently covered auth/admin/threads/WHM/assistant route surface without mixing in a larger redesign.
- Use `docs/plans/2026-03-15-backend-auth-boundary-logging-design.md` and `docs/plans/2026-03-15-backend-auth-boundary-logging-implementation-plan.md` as the current handoff docs for this completed slice and its deferred backend follow-up list.
- Fresh verification for this continuation pass in `.worktrees/feat-backend-auth-boundary-logging/apps/api`:
  - `uv run pytest -q tests/test_auth_login.py tests/test_rbac.py tests/test_request_context.py` -> `46 passed`
  - `uv run pytest -q` -> `195 passed`
  - `uv run ruff check src tests` -> `All checks passed!`

## 2026-03-15 Continuation Pass: Assistant Service Extraction

What was done in this continuation pass
- Added `apps/api/src/noa_api/api/routes/assistant_action_operations.py` for pending action validation, denial handling, approval orchestration, and approved-tool execution.
- Added `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py` so tool-call ID parsing, tool-run validation, result persistence, audit writes, and success logging now live outside `apps/api/src/noa_api/api/routes/assistant.py`.
- Extended `apps/api/src/noa_api/api/routes/assistant_errors.py` with `AssistantDomainError` plus assistant-focused translation helpers so the extracted assistant flows no longer assemble route-shaped `HTTPException` branches inline.
- Rewired `apps/api/src/noa_api/api/routes/assistant.py` so `AssistantService.add_tool_result(...)`, `approve_action(...)`, and `deny_action(...)` are thin delegation wrappers around the extracted helpers while the route keeps the top-level HTTP and streaming exception boundary.
- Added and tightened focused assistant coverage in `apps/api/tests/test_assistant_service.py`, `apps/api/tests/test_assistant.py`, and the related assistant slice.

What is not yet done
- Broader backend structured logging adoption is still follow-up work outside the assistant-specific seams touched in this pass.
- Stable `error_code` coverage is still selective outside the assistant and previously refreshed backend routes.
- Telemetry reconsideration remains deferred until the current structured log/event field set stabilizes.

What should come next
- Continue the backend-only follow-up by extending `log_context(...)` adoption across more non-assistant success paths.
- Close the remaining selective non-assistant `error_code` gaps without mixing in a larger application-layer redesign.
- Use `docs/plans/2026-03-15-assistant-service-extraction-design.md` and `docs/plans/2026-03-15-assistant-service-extraction-implementation-plan.md` as the current handoff docs for this completed slice and its deferred backend follow-up list.
- Fresh verification for this continuation pass in `apps/api/.worktrees/feat-assistant-service-extraction`:
  - `uv run pytest -q tests/test_assistant_operations.py tests/test_assistant.py tests/test_assistant_service.py tests/test_assistant_commands.py tests/test_assistant_streaming.py` -> `64 passed`
  - `uv run pytest -q` -> `186 passed`
  - `uv run ruff check src tests` -> `All checks passed!`

---

## Historical 2026-03-15 Continuation Pass: Assistant Route Decomposition

What was done in this continuation pass
- Added `apps/api/src/noa_api/api/routes/assistant_operations.py` as a dedicated assistant-orchestration seam for this pass.
- Moved the pre-stream prepare flow into `prepare_assistant_transport(...)`, so command validation, state loading, command application, canonical state reload, and assistant command-type log binding happen outside the route body while preserving pre-SSE HTTP error behavior.
- Moved the in-stream agent phase into `run_agent_phase(...)`, including authorized tool resolution, guaranteed `update_workflow_todo` availability, streaming placeholder updates, safe fallback assistant-message persistence, and final-state refresh behavior.
- Rewired `apps/api/src/noa_api/api/routes/assistant.py` so the route callback mainly seeds controller state, decides whether an agent phase is needed, and stays as the transport-facing exception boundary.
- Added focused continuation coverage in `apps/api/tests/test_assistant_operations.py` and additive route/SSE regression coverage in `apps/api/tests/test_assistant.py`.

What is not yet done
- `apps/api/src/noa_api/api/routes/assistant.py` still contains a large `AssistantService` with state loading, tool-result recording, action approval/denial, tool execution, and audit-log persistence responsibilities.
- Assistant-domain failure shaping is still partly coupled to `assistant.py` instead of living behind smaller helper/service seams throughout the remaining assistant flow.
- Broader backend structured logging adoption and wider non-assistant `error_code` normalization remain follow-up work outside this continuation slice.

What should come next
- Continue the backend-only decomposition by extracting the remaining `AssistantService` approval, tool-result, and tool-execution flows out of `apps/api/src/noa_api/api/routes/assistant.py` and tightening the route-facing translation boundary around them.
- Use `docs/plans/2026-03-15-assistant-route-decomposition-continuation-design.md` and `docs/plans/2026-03-15-assistant-route-decomposition-continuation-implementation-plan.md` as the resume point for the next session.
- Fresh verification for this continuation pass in `.worktrees/feat-assistant-route-decomposition-continuation/apps/api`:
  - `uv run pytest -q tests/test_assistant_operations.py tests/test_assistant.py tests/test_assistant_service.py tests/test_assistant_commands.py tests/test_assistant_streaming.py` -> `55 passed`
  - `uv run pytest -q` -> `177 passed`
  - `uv run ruff check src tests` -> `All checks passed!`

---

## Original 2026-03-14 Audit Snapshot

The bullets in this section capture the codebase state observed during the original audit before the later implementation branches summarized in this report.

### Backend (FastAPI)

Error handling patterns
- Route-layer translation to `HTTPException` with stable `detail` strings is used consistently for auth, admin, threads, and assistant endpoints.
- Domain/service exceptions are generally typed and translated at the route layer (good separation).
- Async transaction boundaries are implemented via dependency generators that `commit` on success and `rollback` on exceptions.

Streaming resiliency (assistant)
- The assistant transport endpoint catches non-cancellation exceptions and ensures the stream continues by appending a user-visible fallback message (protects UX).
- `asyncio.CancelledError` is explicitly re-raised in multiple places (good: respects cancellations).

Integration error handling
- The WHM integration client returns structured result objects (e.g. `{ ok, error_code, message }`) rather than raising exceptions for expected failures.

### Frontend (Next.js)

Error handling patterns
- A shared client helper `fetchWithAuth()` adds Bearer tokens, enforces same-origin paths, and clears auth on 401.
- `jsonOrThrow()` converts non-2xx responses into a typed `ApiError(status, message)` using backend `detail` when present.
- Feature pages (admin, assistant runtime hydration) typically wrap API calls in `try/catch` and show user-facing error messages.

API routing
- A Next.js route handler proxies `/api/*` to the backend, filtering hop-by-hop headers and forwarding status/body.

---

## Logging State In The Original 2026-03-14 Audit

This section describes the logging posture observed at audit time, before the later branch lineage updates recorded below.

Backend
- Logging setup calls `logging.basicConfig(level=INFO)` and configures `structlog` processors.
- In practice, most modules use the stdlib logger and only the assistant transport route logs explicitly.
- There is no middleware-level request logging, no request/correlation IDs, and no consistent structured context binding.

Frontend
- Logging is limited to a couple of `console.error` statements in runtime/hydration paths.
- No telemetry library (Sentry, OpenTelemetry, etc.) is present in the frontend dependencies.

---

## Weak Spots (Plan Inputs)

Status legend:
- Done: implemented on the current branch
- Partial: improved, but follow-up work still remains
- Not yet: identified by the audit, but not implemented in this branch

### W1: Structlog configured but not operationally used
Status
- Partial

What
- `structlog` is configured, but the codebase predominantly uses stdlib `logging.getLogger()` and does not bind structured context.

Why it matters
- In production, logs tend to be the main diagnostic tool. Without consistent structured logging and context (request_id, user_id, thread_id), incident triage becomes slow and unreliable.

Primary locations
- Logging config: `apps/api/src/noa_api/core/logging.py`
- Stdlib logger usage: `apps/api/src/noa_api/api/routes/assistant.py`

What changed across the implemented branches
- Added request-context-aware logging setup in `apps/api/src/noa_api/core/logging.py`.
- Preserved safe embedding behavior by avoiding destructive root logger resets.
- Added internal exception logging in tool failure paths.
- Added `apps/api/src/noa_api/core/logging_context.py` for scoped structured context binding.
- Updated logging setup so existing root handlers are upgraded to the structlog formatter instead of silently bypassing structured output.
- Added structured event-style logs with bound request/entity fields in `apps/api/src/noa_api/api/error_handling.py`, `apps/api/src/noa_api/api/routes/assistant.py`, `apps/api/src/noa_api/api/routes/admin.py`, `apps/api/src/noa_api/api/routes/threads.py`, and `apps/api/src/noa_api/api/routes/whm_admin.py`.
- Added successful assistant action/tool logs with stable bound fields for `action_request_id`, `tool_name`, `tool_run_id`, `thread_id`, and `user_id` in the newly touched assistant service paths.
- Added structured auth boundary logs plus bound `user_id` / `user_email` context in `apps/api/src/noa_api/api/auth_dependencies.py` and `apps/api/src/noa_api/api/routes/auth.py` for login success/rejection, current-user resolution, `/auth/me`, and auth rejection paths.

What remains
- Logging is still not consistently structured/bound across the whole API surface.
- Key contextual fields such as `user_id`, `thread_id`, `tool_name`, and `tool_run_id` are now present in the touched backend flows, but not yet systematically bound everywhere else in the API.

### W2: Missing request-scoped context (no request_id / correlation)
Status
- Done

What
- No request ID is generated/propagated, and logs are not automatically enriched with request metadata.

Why it matters
- Hard to trace a single user action across proxy -> API -> DB -> tool run, especially in async/streaming flows.

What changed across the implemented branches
- Added request ID middleware and contextvar helpers in `apps/api/src/noa_api/api/error_handling.py` and `apps/api/src/noa_api/core/request_context.py`.
- Responses now include `X-Request-Id`, and error responses also include `request_id` in the JSON body.

### W3: No centralized API error response standardization
Status
- Partial

What
- Errors rely on FastAPI defaults plus per-route `HTTPException(detail=...)` strings.
- Frontend logic sometimes depends on specific `detail` strings (e.g. pending approval).

Why it matters
- String-based branching is brittle as the product grows. Adding a stable `error_code` (while keeping `detail`) improves long-term maintainability and i18n readiness.

What changed across the implemented branches
- Added centralized JSON error shaping in `apps/api/src/noa_api/api/error_handling.py`.
- Added `ApiHTTPException` support for stable `error_code` values while preserving `detail`.
- Converted auth routes to return stable auth-specific `error_code` values.
- Updated the frontend error parser to consume `error_code` and `request_id`.
- Expanded stable `error_code` coverage in `apps/api/src/noa_api/api/routes/threads.py`, `apps/api/src/noa_api/api/routes/admin.py`, `apps/api/src/noa_api/api/routes/whm_admin.py`, and key assistant transport/service paths.
- Added `apps/api/src/noa_api/api/error_codes.py` as a shared backend error-code catalog for the currently covered route set.
- Added stable assistant error codes for malformed and missing `toolCallId` / `actionRequestId` paths and preserved schema-level required fields for those commands.
- Added an API-layer auth dependency seam in `apps/api/src/noa_api/api/auth_dependencies.py` so protected-route auth failures now also return stable auth `error_code` values instead of plain `detail`-only `HTTPException` responses.
- Expanded the shared backend error-code catalog to cover auth login and protected-route bearer-token failures via shared constants.

What remains
- Stable `error_code` coverage is broader, but still selective; some routes and helper-level validation errors still rely on `detail` only.
- Validation and generic route exceptions are shaped consistently now, but they are not yet normalized into a larger app-wide error-code catalog.

### W4: Assistant transport route is large and multi-responsibility
Status
- Partial

What
- The assistant transport module mixes HTTP validation, persistence, orchestration, streaming state management, tool execution, and logging.

Why it matters
- Harder to test and refactor safely; higher chance of regressions when adding features.
- Broad exception catches are necessary for stream resilience, but they risk hiding actionable failure modes unless paired with robust telemetry.

Primary location
- `apps/api/src/noa_api/api/routes/assistant.py`

What changed across the implemented branches
- Extracted `SQLAssistantRepository` into `apps/api/src/noa_api/api/routes/assistant_repository.py`.
- Extracted shared tool-result payload shaping into `apps/api/src/noa_api/api/routes/assistant_tool_execution.py`.
- Added better failure sanitization and internal logging in assistant tool execution paths.
- Extracted command validation/application into `apps/api/src/noa_api/api/routes/assistant_commands.py`.
- Extracted streaming placeholder/fallback/flush helpers into `apps/api/src/noa_api/api/routes/assistant_streaming.py`.
- Extracted assistant-specific ID parsing/error mapping into `apps/api/src/noa_api/api/routes/assistant_errors.py` so malformed/missing assistant IDs no longer rely on ad hoc route-local helpers.
- Moved pre-agent validation/loading/command application before SSE startup so structured HTTP errors survive and pre-stream failures roll back safely.
- Added `apps/api/src/noa_api/api/routes/assistant_operations.py` for the 2026-03-15 continuation pass so pre-stream preparation and in-stream agent coordination are no longer inlined in the route callback.
- Slimmed the route callback so it now mostly seeds controller state, decides whether the agent phase should run, and delegates orchestration to helper-level seams.
- Added focused assistant helper tests around the new seams.
- Added `apps/api/src/noa_api/api/routes/assistant_action_operations.py` for assistant action validation, approval/denial orchestration, and approved-tool execution.
- Added `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py` for tool-result validation, completion, audit logging, and success-path message persistence.
- Centralized assistant-domain failure construction in `apps/api/src/noa_api/api/routes/assistant_errors.py` so extracted assistant flows raise `AssistantDomainError` values that are translated back to the stable HTTP contract at one thin boundary.
- Reduced `AssistantService` in `apps/api/src/noa_api/api/routes/assistant.py` to thin delegation wrappers for action approval, action denial, and tool-result recording.

What remains
- `apps/api/src/noa_api/api/routes/assistant.py` is materially smaller than the original audit target, but broader backend logging adoption still remains outside the assistant slice.
- Stable `error_code` coverage is still selective outside the assistant and previously touched backend routes.
- Backend telemetry/vendor follow-up is still deferred.

### W5: Multiple SQLAlchemy engines/pools instantiated across modules
Status
- Done

What
- Several modules create their own engine + session factory at import time.

Why it matters
- Multiple pools against the same database can increase connection usage and complicate configuration (timeouts, pool sizing, logging).
- Centralizing engine/session creation simplifies lifecycle management and observability.

Examples
- `apps/api/src/noa_api/api/routes/threads.py`
- `apps/api/src/noa_api/api/routes/assistant.py`
- `apps/api/src/noa_api/api/routes/whm_admin.py`
- `apps/api/src/noa_api/core/auth/deps.py`
- `apps/api/src/noa_api/core/auth/authorization.py`

What changed across the implemented branches
- Added cached `get_engine()` and `get_session_factory()` accessors in `apps/api/src/noa_api/storage/postgres/client.py`.
- Updated the listed modules to use the shared session factory instead of building their own module-local engine/session pairs.

### W6: Tool failures persist raw error strings (potential information leakage)
Status
- Done

What
- Tool execution failures store `str(exc)` as the error, and that value is persisted and also surfaced in tool-result payloads in some paths.

Why it matters
- Exceptions can contain sensitive data (tokens, URLs, internal details). Persisting/surfacing raw strings can create a security and privacy issue.
- This also makes user-facing messaging inconsistent (some errors are friendly, some are raw).

Primary locations
- Tool run persistence: `apps/api/src/noa_api/storage/postgres/action_tool_runs.py`
- Tool execution: `apps/api/src/noa_api/core/agent/runner.py`
- Approval/execution path: `apps/api/src/noa_api/api/routes/assistant.py`

What changed across the implemented branches
- Added a shared sanitizer in `apps/api/src/noa_api/core/tool_error_sanitizer.py`.
- `AgentRunner` and assistant approval execution now persist stable sanitized codes instead of raw exception strings.
- Tool-result payloads now expose safe `error` + `error_code` values.
- Raw exception detail remains available in internal logs via `logger.exception(...)` without being persisted or surfaced to users.

### W7: Frontend lacks error boundaries and duplicates error-string handling
Status
- Done

What
- No `app/**/error.tsx` route error boundaries were found.
- Several components define their own `toErrorMessage()` helpers.
- Login flow does not use the shared `jsonOrThrow()` error mapping.

Why it matters
- Unhandled rendering/runtime errors lead to generic Next.js failures instead of a controlled UX.
- Duplicated error formatting logic drifts over time.

Primary locations
- Shared helpers: `apps/web/components/lib/fetch-helper.ts`
- Login: `apps/web/app/login/page.tsx`
- Runtime/hydration: `apps/web/components/lib/runtime-provider.tsx`

What changed across the implemented branches
- Added `apps/web/app/error.tsx`.
- Expanded `ApiError` and `jsonOrThrow()` in `apps/web/components/lib/fetch-helper.ts`.
- Added shared `toUserMessage()` in `apps/web/components/lib/error-message.ts`.
- Moved login and admin flows to the shared parsing/messaging path.

### W8: Limited operational telemetry (web + api)
Status
- Partial

What
- Backend lacks request logging and structured context.
- Frontend lacks an error reporting tool.

Why it matters
- In production, issues will be discovered via user reports, and reproductions can be difficult without telemetry.

What changed across the implemented branches
- Improved local diagnostic quality through request IDs, centralized error envelopes, and internal exception logging.
- Added structured request completion and assistant/backend route event logging suitable for a log aggregator.
- Added request/entity context binding for the touched backend flows without adding a vendor dependency yet.

What remains
- No dedicated frontend error reporting tool is installed.
- No backend tracing/metrics stack is present.
- Backend request/event logging is stronger, but still not a full production telemetry solution.

---

## Current Recommendations

Updated status after the 2026-03-15 auth boundary logging and error-code pass:
- Completed on the foundation branch: request context middleware, centralized error shaping foundation, shared DB engine/session lifecycle, tool failure sanitization, frontend shared error mapping, app-level error boundary, and initial assistant extraction.
- Completed on `feat/backend-error-code-assistant-logging`: wider backend `error_code` adoption for threads/admin/WHM/assistant flows, assistant command/streaming extraction, assistant malformed/missing ID error-code coverage, request/entity structured logging context for the touched backend flows, logging-handler compatibility with preconfigured root handlers, and this report refresh.
- Completed on `feat/assistant-route-decomposition-continuation`: assistant pre-stream preparation and in-stream agent coordination extraction into `apps/api/src/noa_api/api/routes/assistant_operations.py`, focused helper/route regression coverage for the new seam, and a separate continuation handoff pointing to the 2026-03-15 design and implementation docs.
- Completed on `feat/assistant-service-extraction`: assistant action/tool-result extraction into `apps/api/src/noa_api/api/routes/assistant_action_operations.py` and `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py`, assistant-domain error translation tightening in `apps/api/src/noa_api/api/routes/assistant_errors.py`, thinner `AssistantService` delegation in `apps/api/src/noa_api/api/routes/assistant.py`, and a fresh handoff refresh anchored to the 2026-03-15 assistant service extraction design and implementation plan docs.
- Completed on `feat/backend-auth-boundary-logging`: shared auth dependency extraction into `apps/api/src/noa_api/api/auth_dependencies.py`, protected-route auth `error_code` coverage, shared auth error-code catalog constants, structured auth boundary success/rejection logs including failed-login visibility, and refreshed verification plus handoff docs for this non-assistant continuation.
- Still recommended next: continue broader structured logging adoption beyond the auth and previously refreshed route slices, close the remaining selective non-assistant `error_code` gaps, and revisit backend telemetry after the new log/event field set stabilizes.

Active next steps
1. Extend `log_context(...)` adoption across the rest of the backend surface.
2. Close the remaining selective non-assistant `error_code` gaps across the rest of the backend surface beyond auth/admin/threads/WHM/assistant.
3. Revisit telemetry only after the current structured log/event field set stabilizes.

Historical note
- The original P0/P1/P2 recommendations from the 2026-03-14 audit drove the foundation branch and the first backend continuation branches. Items such as request IDs, centralized error envelopes, DB session consolidation, tool error sanitization, frontend error boundaries, and initial assistant helper extraction are no longer active recommendations for this worktree.

---

## Branch Status Summary

Done on `feat/error-handling-logging-foundation`
- Backend request IDs and centralized error envelopes
- Stable auth `error_code` support
- Shared Postgres engine/session factory accessors
- Tool failure sanitization with preserved internal logging
- Shared frontend API error parsing and user-message mapping
- Login/admin integration with shared frontend error helpers
- Top-level web error boundary
- Initial assistant route extraction (`assistant_repository.py`, `assistant_tool_execution.py`)
- Backend-only follow-up planning docs for the next pass:
  - `docs/plans/2026-03-14-backend-error-code-assistant-logging-design.md`
  - `docs/plans/2026-03-14-backend-error-code-assistant-logging-implementation-plan.md`

Done on `feat/backend-error-code-assistant-logging`
- Stable backend `error_code` coverage expanded for threads, admin, WHM admin, and key assistant transport/service errors
- Shared backend error-code catalog added in `apps/api/src/noa_api/api/error_codes.py`
- Assistant helper extraction added in `apps/api/src/noa_api/api/routes/assistant_commands.py` and `apps/api/src/noa_api/api/routes/assistant_streaming.py`
- Assistant-specific ID parsing/error mapping extracted into `apps/api/src/noa_api/api/routes/assistant_errors.py`
- Assistant pre-agent failures now return structured HTTP errors before SSE starts, while agent-phase failures still degrade gracefully in-stream
- Malformed or missing assistant `toolCallId` / `actionRequestId` paths now return stable `error_code` values without loosening the public request schema
- Structured logging context helpers added in `apps/api/src/noa_api/core/logging_context.py`
- Request completion, unhandled exception, assistant, admin, threads, and WHM admin logs now emit structured event-style fields in the touched flows
- Successful assistant action approval, denial, and tool-result flows now log with stable request/entity identifiers in the touched service paths
- Logging setup now upgrades pre-existing root handlers so structured output still works under Uvicorn-style startup
- Focused regression coverage added for logging context, request logging setup, assistant helper seams, and widened route error-code contracts
- Full backend Ruff is now clean, including the previously unrelated `apps/api/src/noa_api/api/routes/auth.py` unused import warning

Done on `feat/assistant-route-decomposition-continuation`
- Assistant orchestration extraction added in `apps/api/src/noa_api/api/routes/assistant_operations.py`
- Pre-stream prepare work now runs through `prepare_assistant_transport(...)` instead of an in-route orchestration block
- In-stream agent coordination now runs through `run_agent_phase(...)` instead of the monolithic route callback
- Focused helper coverage added in `apps/api/tests/test_assistant_operations.py`
- Additive route/SSE regression coverage added in `apps/api/tests/test_assistant.py`
- Fresh backend verification recorded for this pass:
  - `uv run pytest -q tests/test_assistant_operations.py tests/test_assistant.py tests/test_assistant_service.py tests/test_assistant_commands.py tests/test_assistant_streaming.py` -> `55 passed`
  - `uv run pytest -q` -> `177 passed`
  - `uv run ruff check src tests` -> `All checks passed!`

Done on `feat/assistant-service-extraction`
- Assistant action validation, denial, approval, and approved-tool execution extraction added in `apps/api/src/noa_api/api/routes/assistant_action_operations.py`
- Tool-result validation, completion, audit logging, and success-path persistence extraction added in `apps/api/src/noa_api/api/routes/assistant_tool_result_operations.py`
- Assistant-domain failure construction and translation tightened in `apps/api/src/noa_api/api/routes/assistant_errors.py`
- `AssistantService` now delegates add-tool-result, approve-action, and deny-action flows through the extracted helpers in `apps/api/src/noa_api/api/routes/assistant.py`
- Focused helper and route regression coverage extended for the extracted assistant seam
- Fresh backend verification recorded for this pass:
  - `uv run pytest -q tests/test_assistant_operations.py tests/test_assistant.py tests/test_assistant_service.py tests/test_assistant_commands.py tests/test_assistant_streaming.py` -> `64 passed`
  - `uv run pytest -q` -> `186 passed`
  - `uv run ruff check src tests` -> `All checks passed!`

Done on `feat/backend-auth-boundary-logging`
- Shared auth dependency extraction added in `apps/api/src/noa_api/api/auth_dependencies.py`
- Request-facing auth HTTP translation removed from `apps/api/src/noa_api/core/auth/authorization.py`
- Protected routes now import the shared API-layer `get_current_auth_user` dependency instead of the core-module version
- Shared auth error-code constants added in `apps/api/src/noa_api/api/error_codes.py`
- `/auth/login` and protected-route auth failures now consume the shared auth error-code catalog consistently
- Structured auth logs added for `auth_login_succeeded`, `auth_login_rejected`, `auth_me_succeeded`, `auth_current_user_resolved`, and `auth_current_user_rejected`
- Focused backend coverage added and tightened in `apps/api/tests/test_auth_login.py`, `apps/api/tests/test_rbac.py`, `apps/api/tests/test_request_context.py`, and the related protected-route suites
- Fresh backend verification recorded for this pass:
  - `uv run pytest -q tests/test_auth_login.py tests/test_rbac.py tests/test_request_context.py` -> `46 passed`
  - `uv run pytest -q` -> `195 passed`
  - `uv run ruff check src tests` -> `All checks passed!`

Not yet done after the 2026-03-15 auth boundary logging and error-code pass
- Broader route-by-route `error_code` adoption beyond the now-covered auth/admin/threads/WHM/assistant flows
- Rich, consistent structured logging context binding across the rest of the backend surface beyond the current auth and assistant-adjacent event coverage
- Backend telemetry vendor adoption (`OpenTelemetry`, etc.) remains deferred

Recommended next from this worktree
1. Extend `log_context(...)` adoption beyond the touched backend flows so more successful non-auth paths bind stable identifiers consistently using the current auth and assistant event vocabulary.
2. Close the remaining selective non-assistant `error_code` gaps across the rest of the backend surface beyond the now-covered auth/admin/threads/WHM/assistant route set.
3. Revisit backend telemetry only after the new structured log/event fields stabilize and you know which data should feed traces/metrics.

Primary execution handoff for the current continuation pass
- Worktree: `.worktrees/feat-backend-auth-boundary-logging`
- Branch: `feat/backend-auth-boundary-logging`
- Primary plans:
  - `docs/plans/2026-03-15-backend-auth-boundary-logging-design.md`
  - `docs/plans/2026-03-15-backend-auth-boundary-logging-implementation-plan.md`
- Resume point: this worktree completed the shared auth dependency extraction plus auth-boundary logging/error-code slice and refreshed the audit handoff; use the deferred follow-up list in these docs as the backend-only next-step reference.

Historical execution handoff: assistant service extraction
- Worktree: `apps/api/.worktrees/feat-assistant-service-extraction`
- Branch: `feat/assistant-service-extraction`
- Primary plans:
  - `docs/plans/2026-03-15-assistant-service-extraction-design.md`
  - `docs/plans/2026-03-15-assistant-service-extraction-implementation-plan.md`
- Resume point: this worktree completed the assistant action/tool-result extraction slice and refreshed the audit handoff before the later auth-boundary continuation.

Historical execution handoff: assistant route decomposition continuation
- Worktree: `.worktrees/feat-assistant-route-decomposition-continuation`
- Branch: `feat/assistant-route-decomposition-continuation`
- Primary plans:
  - `docs/plans/2026-03-15-assistant-route-decomposition-continuation-design.md`
  - `docs/plans/2026-03-15-assistant-route-decomposition-continuation-implementation-plan.md`
- Resume point: this pass completed the `assistant_operations.py` seam and left the remaining `AssistantService` extraction work for the now-completed `feat/assistant-service-extraction` continuation.

Historical execution handoff: prior backend continuation branch
- Worktree: `.worktrees/backend-error-code-assistant-logging`
- Branch: `feat/backend-error-code-assistant-logging`
- Primary plans:
  - `docs/plans/2026-03-14-backend-error-code-assistant-logging-implementation-plan.md`
  - `docs/plans/2026-03-14-backend-error-code-assistant-logging-continuation-implementation-plan.md`
- Verification completed in this worktree:
  - `uv run pytest -q tests/test_assistant.py tests/test_assistant_service.py tests/test_assistant_commands.py tests/test_assistant_streaming.py tests/test_logging_context.py` -> `51 passed`
  - `uv run pytest -q` -> `171 passed`
  - `uv run ruff check src tests` -> `All checks passed!`
- Historical note: this block documents the prior continuation state only; the current resume point is the primary handoff above.

Historical execution handoff: original backend follow-up setup
- Planning docs were committed in `15850e1` (`docs: plan backend error handling follow-up`).
- Isolated implementation worktree created at `.worktrees/backend-error-code-assistant-logging` on branch `feat/backend-error-code-assistant-logging`.
- Backend worktree baseline verified on 2026-03-14 with:
  - `uv sync` in `apps/api`
  - `uv run pytest -q tests/test_threads.py tests/test_rbac.py tests/test_whm_admin_routes.py tests/test_assistant.py tests/test_assistant_service.py`
  - Result: `55 passed`
- Historical note: this was the original setup checkpoint and is retained for lineage, not as the current resume point.

---

## Historical Suggested Plan Starter (Superseded)

This list is retained as audit history. Multiple items below were completed across `feat/error-handling-logging-foundation`, `feat/backend-error-code-assistant-logging`, and `feat/assistant-route-decomposition-continuation`, so it should not be read as the active work queue for the current worktree.

1) Logging decision doc
- Choose logging approach (stdlib-json vs structlog-integrated).
- Define required fields: `request_id`, `user_id`, `thread_id`, `tool_name`, `tool_run_id`.

2) Backend request context middleware
- Generate `request_id` per request.
- Attach to response headers and bind into logs.

3) Central exception handling
- Add a top-level exception handler/middleware that:
  - Logs uncaught exceptions with context.
  - Returns a consistent JSON error envelope (maintain current `detail` behavior).

4) Consolidate SQLAlchemy engine/session
- Provide a single engine/session factory shared across routes/services.
- Remove duplicated `_engine`/`_session_factory` initializations.

5) Tool failure sanitization
- Implement a sanitizer for exception messages before persisting/surfacing.
- Add safe error codes for common failures (timeout, auth_failed, invalid_response).

6) Assistant transport refactor (incremental)
- Extract: repositories, orchestration services, streaming helpers, and logging helpers.
- Add unit tests around each extracted component.

7) Frontend error boundary
- Add `apps/web/app/error.tsx` and ensure it handles `ApiError` vs unknown errors gracefully.

8) Frontend error helper consolidation
- Create a shared `toUserMessage(error)` function that maps `ApiError` and common network failures.
- Replace duplicated helpers in admin pages.

9) Telemetry (optional but recommended)
- Add frontend error reporting.
- Add backend structured logging output suitable for your log aggregator.

---

## Files Reviewed (Non-exhaustive)

Backend
- `apps/api/src/noa_api/main.py`
- `apps/api/src/noa_api/core/logging.py`
- `apps/api/src/noa_api/api/routes/auth.py`
- `apps/api/src/noa_api/api/routes/admin.py`
- `apps/api/src/noa_api/api/routes/threads.py`
- `apps/api/src/noa_api/api/routes/assistant.py`
- `apps/api/src/noa_api/api/routes/whm_admin.py`
- `apps/api/src/noa_api/core/agent/runner.py`
- `apps/api/src/noa_api/core/auth/ldap_service.py`
- `apps/api/src/noa_api/storage/postgres/action_tool_runs.py`

Frontend
- `apps/web/components/lib/fetch-helper.ts`
- `apps/web/components/lib/runtime-provider.tsx`
- `apps/web/components/lib/thread-list-adapter.ts`
- `apps/web/app/api/[...path]/route.ts`
- `apps/web/app/login/page.tsx`
- `apps/web/components/admin/users-admin-page.tsx`
- `apps/web/components/admin/whm-servers-admin-page.tsx`
