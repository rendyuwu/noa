# Error Handling and Logging Foundation Design

Date: 2026-03-14

## Context

The audit in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` found a solid baseline for route-level error handling, but weak production observability and a few maintainability and safety gaps.

The most important current issues are:

- backend logging is configured but not used as a consistent structured system
- requests have no shared request-scoped context such as `request_id`
- API errors rely too heavily on `detail` strings alone
- the assistant transport route in `apps/api/src/noa_api/api/routes/assistant.py` has too many responsibilities
- tool failures can persist or surface raw exception text
- the frontend duplicates error mapping logic and lacks route error boundaries

The chosen direction is foundation first: establish consistent request context, logging, and error shaping before deeper refactors.

## Goals

- Make backend logs operationally useful for production diagnosis.
- Add request-scoped context that can follow a request through API handling and assistant flows.
- Standardize API error responses without breaking existing client behavior.
- Unify frontend error handling around typed errors instead of duplicated string parsing.
- Prevent raw exception text from being persisted or surfaced to users in tool failure paths.
- Create safe seams for later incremental refactoring of the assistant transport route.

## Non-goals

- Rewriting the assistant transport route in a single pass.
- Introducing a mandatory full telemetry platform in the first implementation pass.
- Changing existing user-facing `detail` strings where current clients already depend on them.
- Adding broad end-to-end coverage before the new boundaries and helpers exist.

## Approach Options Considered

### 1) Minimal stabilization

Add request IDs, improve logging, and introduce a shared error envelope, but defer most assistant and tool-safety changes.

Pros:

- lowest risk and fastest rollout
- improves incident diagnosis quickly

Cons:

- leaves important safety and maintainability problems in place

### 2) Incremental hardening (chosen)

Implement the foundation layer first, then follow with tool-error sanitization and an incremental assistant-route extraction.

Pros:

- best balance of impact, safety, and maintainability
- reduces production risk without a large rewrite
- prepares the codebase for later telemetry and refactors

Cons:

- requires sequencing across backend and frontend
- does not fully eliminate assistant complexity in one iteration

### 3) Platform overhaul

Adopt telemetry tooling immediately and combine it with a broader assistant refactor.

Pros:

- strongest long-term platform story

Cons:

- higher implementation and rollout risk
- harder to validate incrementally

## Proposed Changes

### 1) Request-scoped context middleware

Add lightweight middleware in `apps/api/src/noa_api/main.py` that:

- accepts an inbound `X-Request-Id` when present, otherwise generates one
- stores the request ID in request state and request-scoped logging context
- attaches `X-Request-Id` to every response, including error responses
- captures baseline request metadata such as method, path, status, and duration

This creates a shared correlation key for logs and client-visible error responses.

### 2) Structured logging that is actually used

Keep `structlog`, but make it the real backend logging pipeline rather than a nominal configuration.

The logging setup in `apps/api/src/noa_api/core/logging.py` should:

- configure stdlib and `structlog` together so existing `logging.getLogger()` calls still work
- produce structured output suitable for log aggregation
- include shared fields such as `request_id`, `method`, `path`, `status`, and `duration_ms`
- allow additional bound fields such as `user_id`, `thread_id`, `tool_name`, and `tool_run_id` where available

This avoids adding a new logging dependency while still moving the API toward structured, queryable logs.

### 3) Centralized API error envelope

Add top-level exception handling for uncaught errors and common framework failures.

The response contract should preserve compatibility while improving stability:

- always keep `detail`
- optionally include `error_code`
- include `request_id`

Target shape:

```json
{
  "detail": "User pending approval",
  "error_code": "user_pending_approval",
  "request_id": "..."
}
```

Requirements:

- preserve existing `detail` text where clients already depend on it
- log uncaught exceptions once with request context
- avoid exposing internal stack traces or raw exception text to clients

### 4) Shared engine and session lifecycle

Use a single engine and session-factory path per process instead of route-level engine/session globals.

`apps/api/src/noa_api/storage/postgres/client.py` should remain the central creation point, while routes and services receive sessions through dependencies rather than instantiating their own pools.

This reduces:

- duplicated configuration
- hidden connection pool growth
- friction when adding DB observability later

### 5) Tool failure sanitization

Introduce a sanitization layer between caught exceptions and any persisted or user-visible tool failure payloads.

The new behavior should:

- map expected failure classes to stable codes such as `timeout`, `auth_failed`, or `invalid_response`
- persist safe summaries instead of raw `str(exc)` values
- keep raw exception detail in logs only, guarded by structured context
- give the frontend and assistant flow a predictable way to present tool failures

### 6) Incremental assistant transport extraction

Do not replace `apps/api/src/noa_api/api/routes/assistant.py` all at once.

Instead, extract narrow seams in steps:

- request validation and command parsing helpers
- persistence/repository helpers
- streaming state helpers
- tool execution and approval orchestration helpers
- logging helpers for assistant-specific context binding

This keeps behavior stable while making the route easier to test and safer to evolve.

### 7) Frontend error handling unification

Extend `ApiError` and `jsonOrThrow()` in `apps/web/components/lib/fetch-helper.ts` so the frontend consumes the richer backend envelope once and centrally.

Then:

- create a shared helper such as `toUserMessage(error)` for `ApiError` and network failures
- replace duplicated `toErrorMessage()` helpers in admin pages
- update `apps/web/app/login/page.tsx` to use the shared path instead of custom response parsing
- prefer `error_code` over `detail` for product logic, while still showing stable `detail` or fallback user messages

### 8) Frontend error boundaries

Add `apps/web/app/error.tsx` to provide a controlled fallback for unhandled route-level rendering/runtime failures.

Route-specific boundaries should only be added later if admin or assistant flows need meaningfully different recovery UX.

### 9) Telemetry as a follow-on, not a blocker

Telemetry libraries are allowed, but they are optional for the first pass.

The initial implementation should leave clean integration points so tools like `Sentry` on the web side or `OpenTelemetry` on the backend can be added later without reworking the new error and logging foundations.

## Rollout Order

1. request context middleware and logging plumbing
2. centralized error envelope and uncaught-exception logging
3. shared DB engine and session usage
4. tool failure sanitization
5. frontend error helper consolidation and login alignment
6. app-level frontend error boundary
7. assistant transport extraction in small slices
8. optional telemetry integration

## Testing Strategy

- backend tests for request ID generation and propagation
- backend tests for global exception handlers and error envelope shape
- backend tests that tool failures persist safe messages and stable codes rather than raw exception text
- backend tests for any extracted assistant helpers at seam boundaries
- frontend tests for shared error parsing and user-message mapping
- verification that current user-visible `detail` strings remain compatible where the UI already relies on them

## Acceptance Criteria

- every API response includes `X-Request-Id`
- backend logs contain request-scoped context for normal and exceptional flows
- uncaught backend failures are logged once and returned in the standardized envelope
- existing `detail` behavior remains compatible for current flows
- frontend generic error handling is centralized instead of duplicated
- login and admin flows rely on shared typed errors
- tool failures no longer persist or surface raw exception text
- assistant transport becomes easier to evolve through smaller extracted units

## Implementation Notes

- Prefer additive changes first so existing clients keep working while the new envelope and helpers land.
- Preserve stable route-layer `detail` strings during migration.
- Treat telemetry integration as a separate follow-up unless production needs require it immediately.
