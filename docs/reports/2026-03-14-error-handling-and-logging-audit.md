# Error Handling and Logging Audit (Project NOA)

Date: 2026-03-14

Scope:
- Backend: `apps/api` (FastAPI + SQLAlchemy async)
- Frontend: `apps/web` (Next.js + assistant-ui)

Method:
- Static inspection of the code paths that implement request handling, authentication, assistant transport/streaming, persistence, and client-side API access.
- No runtime testing, no production log sampling.

High-level assessment:
- Error handling: solid baseline; mostly intentional and consistent at route boundaries.
- Logging/observability: present but minimal; currently insufficient for reliable production diagnosis.
- Maintainability: moderate; biggest risk is a large multi-responsibility assistant transport route and inconsistent logging patterns.

Implementation status update (same branch, after foundation work):
- Core foundation work from this audit has been partially implemented in `feat/error-handling-logging-foundation`.
- Request-scoped IDs, centralized API error shaping, shared DB engine/session accessors, tool-failure sanitization, frontend shared error mapping, and a top-level web error boundary are now in place.
- The remaining notable gap is broader observability and deeper assistant transport decomposition; the assistant route is improved but still too large.
- A backend-only follow-up design and implementation plan for the next phase now live in `docs/plans/2026-03-14-backend-error-code-assistant-logging-design.md` and `docs/plans/2026-03-14-backend-error-code-assistant-logging-implementation-plan.md`.

---

## What Exists Today

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

## Logging: Current State

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

What changed on this branch
- Added request-context-aware logging setup in `apps/api/src/noa_api/core/logging.py`.
- Preserved safe embedding behavior by avoiding destructive root logger resets.
- Added internal exception logging in tool failure paths.

What remains
- Logging is still not consistently structured/bound across the whole API surface.
- Key contextual fields such as `user_id`, `thread_id`, `tool_name`, and `tool_run_id` are not yet systematically bound everywhere.

### W2: Missing request-scoped context (no request_id / correlation)
Status
- Done

What
- No request ID is generated/propagated, and logs are not automatically enriched with request metadata.

Why it matters
- Hard to trace a single user action across proxy -> API -> DB -> tool run, especially in async/streaming flows.

What changed on this branch
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

What changed on this branch
- Added centralized JSON error shaping in `apps/api/src/noa_api/api/error_handling.py`.
- Added `ApiHTTPException` support for stable `error_code` values while preserving `detail`.
- Converted auth routes to return stable auth-specific `error_code` values.
- Updated the frontend error parser to consume `error_code` and `request_id`.

What remains
- Stable `error_code` coverage is still selective; most non-auth routes still rely on `detail` only.
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

What changed on this branch
- Extracted `SQLAssistantRepository` into `apps/api/src/noa_api/api/routes/assistant_repository.py`.
- Extracted shared tool-result payload shaping into `apps/api/src/noa_api/api/routes/assistant_tool_execution.py`.
- Added better failure sanitization and internal logging in assistant tool execution paths.

What remains
- `apps/api/src/noa_api/api/routes/assistant.py` is still large and multi-responsibility.
- Validation/orchestration/streaming concerns are still colocated and should be split further in follow-up work.

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

What changed on this branch
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

What changed on this branch
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

What changed on this branch
- Added `apps/web/app/error.tsx`.
- Expanded `ApiError` and `jsonOrThrow()` in `apps/web/components/lib/fetch-helper.ts`.
- Added shared `toUserMessage()` in `apps/web/components/lib/error-message.ts`.
- Moved login and admin flows to the shared parsing/messaging path.

### W8: Limited operational telemetry (web + api)
Status
- Not yet

What
- Backend lacks request logging and structured context.
- Frontend lacks an error reporting tool.

Why it matters
- In production, issues will be discovered via user reports, and reproductions can be difficult without telemetry.

What changed on this branch
- Improved local diagnostic quality through request IDs, centralized error envelopes, and internal exception logging.

What remains
- No dedicated frontend error reporting tool is installed.
- No backend tracing/metrics stack is present.
- Request logging is better, but still not a full production telemetry solution.

---

## Recommendations (Prioritized)

Updated status after this branch:
- Completed on this branch: request context middleware, centralized error shaping foundation, shared DB engine/session lifecycle, tool failure sanitization, frontend shared error mapping, app-level error boundary, and initial assistant extraction.
- Planning completed on this branch: a backend-only follow-up design and implementation plan for wider `error_code` adoption, further assistant decomposition, richer structured logging context, and an audit-report refresh.
- Still recommended next: execute that backend plan, then revisit backend telemetry after the assistant split and log fields stabilize.

### P0 (highest impact)
- Establish a consistent logging strategy and make it real:
  - Decide: stdlib logging only (with JSON formatter) vs structlog-first (stdlib integration).
  - Add request-scoped context (request_id) and bind key fields (user_id, thread_id) where available.
- Reduce assistant transport complexity:
  - Split `assistant.py` into smaller modules/services (transport validation, persistence/repo, streaming/state handling, tool execution/approval orchestration).
- Add safeguards for tool error persistence:
  - Introduce a sanitization step for exceptions before persisting/surfacing.
  - Prefer stable error codes + safe user messages; keep raw exceptions only in logs (guarded).

### P1 (important)
- Add centralized API error shaping:
  - Keep `detail` stable, but optionally include `error_code` and `request_id` in JSON responses.
  - Add a global exception handler/middleware for uncaught exceptions that logs with request context.
- Consolidate DB engine/session creation:
  - Ensure the app uses a single engine/pool per process and injects sessions consistently.
- Frontend: add error boundaries and unify error presentation:
  - Add `app/error.tsx` (and route-level boundaries where needed) for controlled UX.
  - Consolidate `toErrorMessage()` into a shared helper that understands `ApiError`.
  - Consider moving login to use shared error mapping for consistency.

### P2 (nice-to-have / scaling)
- Add production-grade telemetry:
  - Frontend error reporting (e.g. Sentry) for uncaught exceptions and network failures.
  - Backend tracing/metrics (OpenTelemetry) if you expect multi-service workflows.
- Add targeted tests around failure modes:
  - Assistant transport failures (pre-agent and agent phases), tool failures, DB conflicts/idempotency.

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

Not yet done on this branch
- Broader route-by-route `error_code` adoption beyond auth and tool execution flows
- Full assistant transport decomposition into smaller orchestration/streaming modules
- Rich, consistent structured logging context binding across assistant and related backend route flows
- Backend telemetry vendor adoption (`OpenTelemetry`, etc.) remains deferred

Recommended next
1. Execute `docs/plans/2026-03-14-backend-error-code-assistant-logging-implementation-plan.md`, starting with route-level `error_code` tests and migrations in `threads.py`, `admin.py`, `whm_admin.py`, and assistant validation branches.
2. Continue splitting `apps/api/src/noa_api/api/routes/assistant.py` into command and streaming helpers while preserving current SSE behavior with targeted characterization tests.
3. Add structured logging context binding for `user_id`, `thread_id`, `tool_name`, `tool_run_id`, and `action_request_id` in assistant and related backend route flows.
4. Revisit backend telemetry only after the assistant refactor lands and the desired structured log fields have stabilized.

---

## Suggested Plan Starter (Work Items)

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
