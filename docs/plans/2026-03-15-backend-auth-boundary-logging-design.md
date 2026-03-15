# Backend Auth Boundary Logging and Error Code Design

Date: 2026-03-15

## Context

The audit lineage in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` already improved request IDs, centralized error envelopes, assistant logging, and stable `error_code` coverage in several backend routes.

The next backend-only pass should stay small and high leverage. The clearest remaining non-assistant contract gap is the shared auth dependency in `apps/api/src/noa_api/core/auth/authorization.py`, where `get_current_auth_user(...)` still raises plain `HTTPException` values with `detail` only. At the same time, the auth boundary is the best place to expand structured success-path logging because it sits in front of admin, threads, WHM admin, and assistant routes.

This design intentionally covers only the first two active recommendations from the audit, using auth/authz as the narrow execution slice:

1. expand structured logging context across more backend success paths
2. close remaining selective non-assistant `error_code` gaps

Telemetry reconsideration remains deferred until the current event field set proves stable in this narrower pass.

## Goals

- Close the shared auth/authz `error_code` gap without changing user-facing `detail` text.
- Move request-facing auth HTTP translation out of `apps/api/src/noa_api/core/auth/authorization.py` and back into the API layer.
- Add structured success and rejection logs around login, `/auth/me`, and current-user resolution using the existing request-scoped context.
- Create a reusable auth boundary that later route-level logging work can build on.

## Non-goals

- Repo-wide logging cleanup across every route in this pass.
- App-wide validation error normalization.
- Installing `OpenTelemetry`, `Sentry`, or another telemetry vendor.
- Reworking the authorization domain model or permission rules.

## Approaches Considered

### 1) Auth route-only patch

Add structured logs to `apps/api/src/noa_api/api/routes/auth.py` and replace the current inline string literals in that file with shared constants.

Pros:

- lowest implementation risk
- keeps changes local to one route module

Cons:

- leaves the shared `get_current_auth_user(...)` dependency returning detail-only errors
- does not improve the auth boundary used by admin, threads, WHM admin, and assistant routes

### 2) Auth boundary hardening (chosen)

Introduce an API-layer auth dependency seam, move request-facing HTTP translation there, and add structured auth logs at both the route and dependency boundary.

Pros:

- closes the most important remaining non-assistant `error_code` gap
- improves the highest-leverage backend success path
- keeps the work small enough to finish and verify cleanly
- creates a clean base for the later route-by-route logging pass

Cons:

- touches both API-layer and auth-adjacent files
- requires a small amount of test reorganization

### 3) Broader route success logging first

Focus first on success-path logs in `admin.py`, `threads.py`, and `whm_admin.py`, then revisit auth later.

Pros:

- expands observability across more routes immediately

Cons:

- leaves the shared auth dependency gap unresolved
- duplicates some logging work before the auth boundary is standardized

## Proposed Design

### 1) Introduce an API-layer auth dependency seam

Add a new module at `apps/api/src/noa_api/api/auth_dependencies.py` that owns request-facing auth resolution for protected routes.

This module should provide small helpers such as:

- bearer credential validation
- JWT decoding and `sub` extraction
- current-user lookup through `AuthService`
- translation of auth-domain failures to `ApiHTTPException`
- conversion from `AuthUser` to `AuthorizationUser`

The purpose is to keep FastAPI and HTTP contract concerns in the API layer instead of `apps/api/src/noa_api/core/auth/authorization.py`.

`apps/api/src/noa_api/core/auth/authorization.py` should retain authorization domain logic and the `AuthorizationService`, but it should stop being the home for route-facing `HTTPException` mapping.

### 2) Normalize auth error codes through the shared catalog

Promote the remaining auth string literals into `apps/api/src/noa_api/api/error_codes.py`.

The catalog additions for this pass should include:

- `INVALID_CREDENTIALS`
- `AUTHENTICATION_SERVICE_UNAVAILABLE`
- `MISSING_BEARER_TOKEN`
- `INVALID_TOKEN`

Existing codes already used elsewhere, such as `USER_PENDING_APPROVAL`, should continue to be reused rather than duplicated.

The external contract stays additive only:

- preserve the current `detail` strings exactly
- keep the current status codes exactly
- ensure both `/auth/*` routes and shared current-user resolution return the same stable `error_code` values

### 3) Add structured logging at the auth boundary

Use `apps/api/src/noa_api/core/logging_context.py` rather than introducing a new logging helper.

The logging model for this pass is:

- rely on middleware to bind `request_id`, `request_method`, and `request_path`
- bind `user_id` only after a user has been resolved successfully
- log event names instead of string-interpolated prose
- never log secrets such as bearer tokens, passwords, LDAP raw responses, or exception text from external systems

Recommended event set:

- `auth_login_succeeded`
- `auth_login_rejected`
- `auth_me_succeeded`
- `auth_current_user_resolved`
- `auth_current_user_rejected`

Recommended bound fields by event:

- login success: `user_id`, `user_email`, `roles`, `is_active`
- current-user resolution success: `user_id`, `user_email`, `roles`, `is_active`
- auth rejection: `status_code`, `error_code`, and a small safe discriminator such as `failure_stage`

This pass should prefer stable event names and safe structured fields over wider log volume.

### 4) Keep route boundaries thin and consistent

`apps/api/src/noa_api/api/routes/auth.py` should continue to own request and response models plus the login and `/auth/me` endpoints.

After this pass:

- `login(...)` should use shared error-code constants and emit structured success/rejection logs
- `/auth/me` should delegate current-user resolution through the new API-layer dependency rather than reimplementing token decoding inline
- protected non-auth routes can continue importing `get_current_auth_user`, but the symbol should come from the API-layer auth dependency seam rather than the core authorization module

This preserves current route signatures while tightening the separation between domain logic and HTTP behavior.

### 5) Defer broader route success logging until after the auth boundary stabilizes

This pass should not try to simultaneously add success logs to every admin, threads, or WHM admin route.

Instead it should establish the stable field vocabulary that later route work will reuse:

- `request_id`
- `user_id`
- `user_email`
- `thread_id` where relevant
- `server_id` where relevant
- `status_code`
- `error_code`
- `failure_stage`

Once these auth events look right, the next pass can extend success-path logging in `apps/api/src/noa_api/api/routes/admin.py`, `apps/api/src/noa_api/api/routes/threads.py`, and `apps/api/src/noa_api/api/routes/whm_admin.py` without inventing new field shapes.

## Module Shape

Target shape after this pass:

- `apps/api/src/noa_api/api/auth_dependencies.py`
  - route-facing auth resolution helpers
  - stable auth error translation
  - `get_current_auth_user(...)` for protected API routes
- `apps/api/src/noa_api/api/routes/auth.py`
  - login route
  - `/auth/me` route using the shared dependency seam
  - auth route success/rejection logs
- `apps/api/src/noa_api/api/error_codes.py`
  - shared auth constants alongside the existing catalog
- `apps/api/src/noa_api/core/auth/authorization.py`
  - authorization domain/service logic only
  - no route-facing `HTTPException` construction in the current-user dependency

## Data Flow

### Login flow

1. Accept `LoginRequest`.
2. Authenticate through `AuthService`.
3. Translate auth-domain failures to stable `ApiHTTPException` values.
4. On success, bind `user_id` and emit `auth_login_succeeded`.
5. Return the existing `LoginResponse` shape.

### Protected-route current-user flow

1. Read bearer credentials from the request.
2. Reject missing credentials with `missing_bearer_token`.
3. Decode the JWT and validate `sub`.
4. Load the user through `AuthService`.
5. Reject invalid token or inactive user with the current stable status/detail contract.
6. Convert the resolved `AuthUser` to `AuthorizationUser`.
7. Bind `user_id` and emit `auth_current_user_resolved`.

### `/auth/me` flow

1. Depend on the shared current-user resolver.
2. Emit `auth_me_succeeded` under the same bound user context.
3. Return the existing response shape unchanged.

## Error Handling

- Preserve `401` for missing or invalid tokens.
- Preserve `403` for inactive users pending approval.
- Preserve `503` for authentication service failures on login.
- Keep `detail` strings unchanged because tests and clients may still rely on them.
- Use the shared error-code catalog everywhere in this slice instead of inline string literals.
- Keep centralized JSON error shaping untouched in `apps/api/src/noa_api/api/error_handling.py`.

## Testing Strategy

- Extend `apps/api/tests/test_auth_login.py` so it covers:
  - login success and failure `error_code` contracts through shared constants
  - `/auth/me` success and rejection paths after the dependency refactor
  - at least one structured auth success log and one auth rejection log
- Extend `apps/api/tests/test_rbac.py` or add a focused auth-dependency test file to verify protected admin routes still reject missing or invalid bearer tokens with the shaped envelope.
- Keep request-context tests in `apps/api/tests/test_request_context.py` focused on middleware and formatter behavior, not auth-specific event semantics.
- Run targeted auth/rbac/request-context tests first, then the full backend test suite.

## Acceptance Criteria

- Protected API routes no longer rely on plain `HTTPException` for current-user auth failures.
- Auth login and current-user flows return stable `error_code` values from the shared catalog.
- Auth success and rejection logs include the intended structured context without logging secrets.
- `apps/api/src/noa_api/core/auth/authorization.py` is simpler because route-facing HTTP translation moved out.
- Telemetry remains deferred; no new vendor dependency is added in this pass.
