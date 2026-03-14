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

### W1: Structlog configured but not operationally used
What
- `structlog` is configured, but the codebase predominantly uses stdlib `logging.getLogger()` and does not bind structured context.

Why it matters
- In production, logs tend to be the main diagnostic tool. Without consistent structured logging and context (request_id, user_id, thread_id), incident triage becomes slow and unreliable.

Primary locations
- Logging config: `apps/api/src/noa_api/core/logging.py`
- Stdlib logger usage: `apps/api/src/noa_api/api/routes/assistant.py`

### W2: Missing request-scoped context (no request_id / correlation)
What
- No request ID is generated/propagated, and logs are not automatically enriched with request metadata.

Why it matters
- Hard to trace a single user action across proxy -> API -> DB -> tool run, especially in async/streaming flows.

### W3: No centralized API error response standardization
What
- Errors rely on FastAPI defaults plus per-route `HTTPException(detail=...)` strings.
- Frontend logic sometimes depends on specific `detail` strings (e.g. pending approval).

Why it matters
- String-based branching is brittle as the product grows. Adding a stable `error_code` (while keeping `detail`) improves long-term maintainability and i18n readiness.

### W4: Assistant transport route is large and multi-responsibility
What
- The assistant transport module mixes HTTP validation, persistence, orchestration, streaming state management, tool execution, and logging.

Why it matters
- Harder to test and refactor safely; higher chance of regressions when adding features.
- Broad exception catches are necessary for stream resilience, but they risk hiding actionable failure modes unless paired with robust telemetry.

Primary location
- `apps/api/src/noa_api/api/routes/assistant.py`

### W5: Multiple SQLAlchemy engines/pools instantiated across modules
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

### W6: Tool failures persist raw error strings (potential information leakage)
What
- Tool execution failures store `str(exc)` as the error, and that value is persisted and also surfaced in tool-result payloads in some paths.

Why it matters
- Exceptions can contain sensitive data (tokens, URLs, internal details). Persisting/surfacing raw strings can create a security and privacy issue.
- This also makes user-facing messaging inconsistent (some errors are friendly, some are raw).

Primary locations
- Tool run persistence: `apps/api/src/noa_api/storage/postgres/action_tool_runs.py`
- Tool execution: `apps/api/src/noa_api/core/agent/runner.py`
- Approval/execution path: `apps/api/src/noa_api/api/routes/assistant.py`

### W7: Frontend lacks error boundaries and duplicates error-string handling
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

### W8: Limited operational telemetry (web + api)
What
- Backend lacks request logging and structured context.
- Frontend lacks an error reporting tool.

Why it matters
- In production, issues will be discovered via user reports, and reproductions can be difficult without telemetry.

---

## Recommendations (Prioritized)

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
