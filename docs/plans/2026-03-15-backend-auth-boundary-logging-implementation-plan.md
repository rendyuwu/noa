# Backend Auth Boundary Logging and Error Code Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the shared auth/authz `error_code` gap and add structured logging on the backend auth boundary without changing existing user-facing response details.

**Architecture:** Add an API-layer auth dependency seam that owns bearer-token validation, JWT subject resolution, auth-domain to HTTP translation, and `AuthorizationUser` shaping. Reuse that seam from `/auth/me` and other protected routes, move remaining auth string literals into the shared error-code catalog, and emit safe structured auth events with request-scoped context.

**Tech Stack:** FastAPI, Pydantic, async auth services, structlog-compatible stdlib logging, pytest, httpx, Ruff

---

### Task 1: Pin the shared auth boundary with failing contract tests

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_auth_login.py`
- Modify: `apps/api/tests/test_rbac.py`

**Step 1: Add a missing-bearer protected-route test in `apps/api/tests/test_rbac.py`**

Extend the admin-route test app so it exercises the real protected-route auth dependency instead of overriding it.

Add a focused test like:

```python
async def test_admin_route_requires_bearer_token_with_stable_error_code() -> None:
    app = _create_admin_app()
    app.dependency_overrides[get_authorization_service] = lambda: _FakeAuthorizationService()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/users")

    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Missing bearer token"
    assert body["error_code"] == "missing_bearer_token"
    assert response.headers["x-request-id"] == body["request_id"]
```

**Step 2: Add an invalid-token protected-route test in `apps/api/tests/test_rbac.py`**

Use a fake JWT service that raises `AuthInvalidCredentialsError` so a protected admin route proves the shared dependency returns the same envelope used by `/auth/me`.

```python
async def test_admin_route_rejects_invalid_bearer_token_with_error_code() -> None:
    ...
    assert response.status_code == 401
    assert response.json()["error_code"] == "invalid_token"
```

**Step 3: Add one auth log-characterization test in `apps/api/tests/test_auth_login.py`**

Capture a structured log stream and pin one success event plus one rejection event.

Suggested assertions:

```python
assert payload["event"] == "auth_login_succeeded"
assert payload["user_email"] == "user@example.com"
assert isinstance(payload["user_id"], str)
assert payload["request_path"] == "/auth/login"
```

and

```python
assert payload["event"] == "auth_current_user_rejected"
assert payload["error_code"] == "invalid_token"
assert payload["failure_stage"] == "jwt_decode"
```

**Step 4: Run the focused tests to confirm they fail first**

Run: `uv run pytest -q apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py -k "bearer or invalid_token or auth_"`

Expected: FAIL because protected routes still use plain `HTTPException` for shared current-user auth failures and the new auth log events do not exist yet.

**Step 5: Commit the failing tests**

```bash
git add apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py
git commit -m "test(api): pin auth boundary contracts"
```

### Task 2: Extract route-facing auth resolution into an API-layer module

@test-driven-development

**Files:**
- Create: `apps/api/src/noa_api/api/auth_dependencies.py`
- Modify: `apps/api/src/noa_api/core/auth/authorization.py`
- Modify: `apps/api/src/noa_api/api/routes/auth.py`
- Test: `apps/api/tests/test_auth_login.py`
- Test: `apps/api/tests/test_rbac.py`

**Step 1: Create `apps/api/src/noa_api/api/auth_dependencies.py` with shared helpers**

Start with a small, explicit module surface:

```python
from __future__ import annotations

async def require_auth_user(... ) -> AuthUser:
    ...


async def get_current_auth_user(... ) -> AuthorizationUser:
    ...
```

Inside this module:

- read bearer credentials with `HTTPBearer(auto_error=False)`
- decode JWTs through `JWTService`
- validate `sub`
- load the user through `AuthService`
- reject inactive users with `USER_PENDING_APPROVAL`
- translate failures with `ApiHTTPException`
- convert `AuthUser` to `AuthorizationUser`

Keep the `detail` strings exactly:

- `Missing bearer token`
- `Invalid token`
- `User pending approval`

**Step 2: Simplify `apps/api/src/noa_api/core/auth/authorization.py`**

Remove the request-facing `get_current_auth_user(...)` dependency from this core module.

Keep only authorization domain concerns here:

- `AuthorizationUser`
- `AuthorizationService`
- repository-backed authorization logic
- `get_authorization_service(...)`

The goal is that this file no longer constructs route-facing `HTTPException` values.

**Step 3: Rewire route imports to use the API-layer dependency seam**

Update:

- `apps/api/src/noa_api/api/routes/auth.py`
- `apps/api/src/noa_api/api/routes/admin.py`
- `apps/api/src/noa_api/api/routes/threads.py`
- `apps/api/src/noa_api/api/routes/whm_admin.py`
- `apps/api/src/noa_api/api/routes/assistant.py`

so they import `get_current_auth_user` from `apps/api/src/noa_api/api/auth_dependencies.py` instead of `apps/api/src/noa_api/core/auth/authorization.py`.

Do not change the runtime behavior of those route signatures beyond the new stable `error_code` responses for shared auth failures.

**Step 4: Update `/auth/me` to use the shared resolver instead of inline token parsing**

Reduce `apps/api/src/noa_api/api/routes/auth.py` so `/auth/me` depends on the shared auth dependency and only shapes the response.

The route should become conceptually:

```python
@router.get("/me", response_model=MeResponse)
async def me(
    current_user: AuthorizationUser = Depends(get_current_auth_user),
) -> MeResponse:
    return _to_me_response(current_user)
```

Adjust `_to_me_response(...)` if needed so it accepts the resolved user shape you return from the new dependency.

**Step 5: Run the focused tests**

Run: `uv run pytest -q apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py`

Expected: PASS for the shared auth error envelopes and route behavior.

**Step 6: Commit the extraction**

```bash
git add apps/api/src/noa_api/api/auth_dependencies.py apps/api/src/noa_api/core/auth/authorization.py apps/api/src/noa_api/api/routes/auth.py apps/api/src/noa_api/api/routes/admin.py apps/api/src/noa_api/api/routes/threads.py apps/api/src/noa_api/api/routes/whm_admin.py apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py
git commit -m "refactor(api): extract auth route dependencies"
```

### Task 3: Move auth literals into the shared error-code catalog

@test-driven-development

**Files:**
- Modify: `apps/api/src/noa_api/api/error_codes.py`
- Modify: `apps/api/src/noa_api/api/auth_dependencies.py`
- Modify: `apps/api/src/noa_api/api/routes/auth.py`
- Test: `apps/api/tests/test_auth_login.py`
- Test: `apps/api/tests/test_rbac.py`

**Step 1: Add auth constants to `apps/api/src/noa_api/api/error_codes.py`**

Add shared constants near the top of the catalog:

```python
INVALID_CREDENTIALS = "invalid_credentials"
AUTHENTICATION_SERVICE_UNAVAILABLE = "authentication_service_unavailable"
MISSING_BEARER_TOKEN = "missing_bearer_token"
INVALID_TOKEN = "invalid_token"
```

Continue reusing `USER_PENDING_APPROVAL` from the existing catalog.

**Step 2: Replace inline auth string literals with shared constants**

Update both `apps/api/src/noa_api/api/routes/auth.py` and `apps/api/src/noa_api/api/auth_dependencies.py` so every auth-facing `ApiHTTPException` references the catalog constants.

Do not change the literal `detail` strings.

**Step 3: Tighten tests so they prove the shared seam is being used**

In `apps/api/tests/test_rbac.py`, ensure at least one protected admin route test goes through the real dependency seam and still gets:

```python
assert body["error_code"] == "missing_bearer_token"
assert body["detail"] == "Missing bearer token"
```

In `apps/api/tests/test_auth_login.py`, keep asserting the same auth codes for `/auth/login` and `/auth/me`.

**Step 4: Run the focused tests again**

Run: `uv run pytest -q apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py -k "auth or token or bearer"`

Expected: PASS.

**Step 5: Commit the catalog cleanup**

```bash
git add apps/api/src/noa_api/api/error_codes.py apps/api/src/noa_api/api/auth_dependencies.py apps/api/src/noa_api/api/routes/auth.py apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py
git commit -m "refactor(api): share auth error codes"
```

### Task 4: Add structured auth success and rejection logs

@test-driven-development

**Files:**
- Modify: `apps/api/src/noa_api/api/auth_dependencies.py`
- Modify: `apps/api/src/noa_api/api/routes/auth.py`
- Modify: `apps/api/tests/test_auth_login.py`
- Modify: `apps/api/tests/test_request_context.py`

**Step 1: Add safe auth event logs in `apps/api/src/noa_api/api/routes/auth.py`**

Emit route events for login success and `/auth/me` success.

Suggested shape:

```python
with log_context(user_id=str(result.user_id), user_email=result.email):
    logger.info(
        "auth_login_succeeded",
        extra={
            "is_active": result.is_active,
            "roles": result.roles,
        },
    )
```

For `/auth/me`, emit `auth_me_succeeded` with the resolved `user_id`, `user_email`, `is_active`, and `roles`.

**Step 2: Add rejection and resolution logs in `apps/api/src/noa_api/api/auth_dependencies.py`**

Emit structured auth boundary events for:

- successful current-user resolution: `auth_current_user_resolved`
- missing or invalid bearer credentials: `auth_current_user_rejected`

Use safe extras only, for example:

```python
logger.info(
    "auth_current_user_rejected",
    extra={
        "status_code": 401,
        "error_code": INVALID_TOKEN,
        "failure_stage": "jwt_decode",
    },
)
```

Never log bearer tokens, passwords, or raw LDAP/internal exception strings.

**Step 3: Extend the log-capture tests in `apps/api/tests/test_auth_login.py`**

Reuse the structured logging capture pattern already used in `apps/api/tests/test_request_context.py`.

Assert at least:

- `auth_login_succeeded` includes `request_path`, `user_id`, and `user_email`
- `auth_current_user_rejected` includes `request_path`, `status_code`, `error_code`, and `failure_stage`

**Step 4: Keep `apps/api/tests/test_request_context.py` focused on middleware behavior**

Only make changes here if you need a tiny shared helper for log capture or if an auth log test benefits from an existing formatter setup pattern. Do not move auth event assertions into this file.

**Step 5: Run the auth and request-context tests**

Run: `uv run pytest -q apps/api/tests/test_auth_login.py apps/api/tests/test_request_context.py`

Expected: PASS.

**Step 6: Commit the logging pass**

```bash
git add apps/api/src/noa_api/api/auth_dependencies.py apps/api/src/noa_api/api/routes/auth.py apps/api/tests/test_auth_login.py apps/api/tests/test_request_context.py
git commit -m "feat(api): add auth boundary structured logs"
```

### Task 5: Refresh the audit handoff and run backend verification

@verification-before-completion

**Files:**
- Modify: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`

**Step 1: Update the audit report status**

Refresh `docs/reports/2026-03-14-error-handling-and-logging-audit.md` so it records:

- auth/authz as the completed next backend slice
- shared auth dependency `error_code` coverage as no longer a notable gap
- auth boundary logging as the new stabilized event vocabulary for later route expansion
- telemetry still deferred until the broader field set settles

**Step 2: Run targeted backend verification**

Run: `uv run pytest -q apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py apps/api/tests/test_request_context.py`

Expected: PASS.

Run: `uv run ruff check apps/api/src apps/api/tests`

Expected: `All checks passed!`

**Step 3: Run the full backend suite**

Run: `uv run pytest -q`

Expected: PASS for the full API test suite.

**Step 4: Commit the verification and doc refresh**

```bash
git add docs/reports/2026-03-14-error-handling-and-logging-audit.md
git commit -m "docs: refresh auth boundary logging follow-up"
```
