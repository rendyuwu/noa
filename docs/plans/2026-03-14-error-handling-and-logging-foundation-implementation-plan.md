# Error Handling and Logging Foundation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add request-scoped backend logging and error envelopes, sanitize tool failures, unify frontend error handling, and start extracting assistant transport seams without breaking current product behavior.

**Architecture:** Add a request-context and exception-handling layer around the FastAPI app, expose stable `error_code` and `request_id` additively, centralize Postgres engine/session creation, sanitize tool failures at execution boundaries, and move the web app to a richer typed `ApiError` plus a shared user-message helper. Use the existing assistant tests as characterization coverage while extracting one small seam from `apps/api/src/noa_api/api/routes/assistant.py` instead of attempting a rewrite.

**Tech Stack:** FastAPI, structlog, async SQLAlchemy, Next.js App Router, Vitest, pytest

---

### Task 1: Add failing API request-context and error-envelope tests

@test-driven-development

**Files:**
- Create: `apps/api/tests/test_request_context.py`

**Step 1: Write the failing tests**

Add three tests that pin the new HTTP contract:

```python
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from noa_api.main import create_app


async def test_health_includes_request_id_header() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers["x-request-id"]


async def test_existing_request_id_is_preserved() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"x-request-id": "req-test-123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-test-123"


async def test_uncaught_exceptions_return_safe_envelope_with_request_id() -> None:
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("token=secret boom")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.headers["x-request-id"]
    assert response.json() == {
        "detail": "Internal server error",
        "error_code": "internal_server_error",
        "request_id": response.headers["x-request-id"],
    }
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q apps/api/tests/test_request_context.py`

Expected: FAIL because `create_app()` does not add `X-Request-Id` headers and uncaught exceptions are not returned in the new envelope.

**Step 3: Commit**

```bash
git add apps/api/tests/test_request_context.py
git commit -m "test: cover request ids and safe error envelopes"
```

### Task 2: Implement backend request context and global exception handling

@systematic-debugging

**Files:**
- Create: `apps/api/src/noa_api/core/request_context.py`
- Create: `apps/api/src/noa_api/api/error_handling.py`
- Modify: `apps/api/src/noa_api/core/logging.py`
- Modify: `apps/api/src/noa_api/main.py`
- Test: `apps/api/tests/test_request_context.py`
- Test: `apps/api/tests/test_health.py`

**Step 1: Add a request-context helper module**

Create `apps/api/src/noa_api/core/request_context.py` with a small contextvar-based API:

```python
from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return str(uuid4())


def set_request_id(value: str) -> None:
    _request_id_var.set(value)


def get_request_id() -> str | None:
    return _request_id_var.get()


def clear_request_id() -> None:
    _request_id_var.set(None)
```

**Step 2: Make `structlog` merge request-scoped context**

Update `apps/api/src/noa_api/core/logging.py` so stdlib loggers and `structlog` share one structured pipeline:

```python
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
)
```

Keep the change additive so existing `logging.getLogger(__name__)` calls still emit structured output.

**Step 3: Add middleware and exception handlers**

In `apps/api/src/noa_api/api/error_handling.py`, add:

- a request middleware that preserves inbound `X-Request-Id` or generates one
- a helper that builds `{"detail": ..., "error_code": ..., "request_id": ...}`
- an `HTTPException` handler that preserves `detail` and attaches `request_id`
- a catch-all `Exception` handler that returns `detail="Internal server error"`

Use a safe body builder like:

```python
def error_body(*, detail: str, request_id: str, error_code: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {"detail": detail, "request_id": request_id}
    if error_code is not None:
        body["error_code"] = error_code
    return body
```

In `apps/api/src/noa_api/main.py`, register the middleware and handlers during `create_app()`.

**Step 4: Run tests to verify the implementation**

Run: `uv run pytest -q apps/api/tests/test_request_context.py apps/api/tests/test_health.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/request_context.py apps/api/src/noa_api/api/error_handling.py apps/api/src/noa_api/core/logging.py apps/api/src/noa_api/main.py apps/api/tests/test_request_context.py apps/api/tests/test_health.py
git commit -m "feat(api): add request-scoped logging and error context"
```

### Task 3: Add failing auth-route tests for stable error codes

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_auth_login.py`

**Step 1: Extend the existing route test assertions**

In `apps/api/tests/test_auth_login.py`, update the login-route assertions to require `error_code` and `request_id` for the key auth failures:

```python
assert invalid_response.status_code == 401
invalid_body = invalid_response.json()
assert invalid_body["detail"] == "Invalid credentials"
assert invalid_body["error_code"] == "invalid_credentials"
assert isinstance(invalid_body["request_id"], str)
assert invalid_response.headers["x-request-id"] == invalid_body["request_id"]

assert pending_response.status_code == 403
pending_body = pending_response.json()
assert pending_body["detail"] == "User pending approval"
assert pending_body["error_code"] == "user_pending_approval"

assert unavailable_response.status_code == 503
unavailable_body = unavailable_response.json()
assert unavailable_body["detail"] == "Authentication service unavailable"
assert unavailable_body["error_code"] == "authentication_service_unavailable"
```

Do the same for the `/auth/me` invalid-token path.

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q apps/api/tests/test_auth_login.py::test_login_route_maps_auth_errors_and_success`

Expected: FAIL because the responses only include `detail` today.

**Step 3: Commit**

```bash
git add apps/api/tests/test_auth_login.py
git commit -m "test: require stable auth error codes"
```

### Task 4: Add API error helpers and migrate auth routes to them

@systematic-debugging

**Files:**
- Modify: `apps/api/src/noa_api/api/error_handling.py`
- Modify: `apps/api/src/noa_api/api/routes/auth.py`
- Test: `apps/api/tests/test_auth_login.py`

**Step 1: Add an `ApiHTTPException` type**

Extend `apps/api/src/noa_api/api/error_handling.py` with a small subclass so routes can attach stable codes without changing the `detail` contract:

```python
from fastapi import HTTPException


class ApiHTTPException(HTTPException):
    def __init__(self, *, status_code: int, detail: str, error_code: str | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code
```

Make the `HTTPException` handler read `getattr(exc, "error_code", None)` and include it in the response body when present.

**Step 2: Update `auth.py` to raise coded exceptions**

Change the route mappings in `apps/api/src/noa_api/api/routes/auth.py` to use `ApiHTTPException` for the common auth failures:

```python
except AuthInvalidCredentialsError as exc:
    raise ApiHTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        error_code="invalid_credentials",
    ) from exc
except AuthPendingApprovalError as exc:
    raise ApiHTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="User pending approval",
        error_code="user_pending_approval",
    ) from exc
```

Use the same pattern for invalid token and service-unavailable cases.

**Step 3: Run the auth tests**

Run: `uv run pytest -q apps/api/tests/test_auth_login.py`

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/api/src/noa_api/api/error_handling.py apps/api/src/noa_api/api/routes/auth.py apps/api/tests/test_auth_login.py
git commit -m "feat(api): add stable auth error codes"
```

### Task 5: Add failing singleton tests for the Postgres engine and session factory

@test-driven-development

**Files:**
- Create: `apps/api/tests/test_postgres_client.py`

**Step 1: Write the failing tests**

Create `apps/api/tests/test_postgres_client.py` and assert the shared creation helpers are process singletons:

```python
from noa_api.storage.postgres.client import get_engine, get_session_factory


def test_get_engine_returns_process_singleton() -> None:
    assert get_engine() is get_engine()


def test_get_session_factory_returns_process_singleton() -> None:
    assert get_session_factory() is get_session_factory()
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q apps/api/tests/test_postgres_client.py`

Expected: FAIL because the current API only exposes constructor-style helpers that create new objects every call.

**Step 3: Commit**

```bash
git add apps/api/tests/test_postgres_client.py
git commit -m "test: pin shared postgres client singletons"
```

### Task 6: Centralize engine/session creation and adopt it across routes and auth dependencies

@systematic-debugging

**Files:**
- Modify: `apps/api/src/noa_api/storage/postgres/client.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/routes/threads.py`
- Modify: `apps/api/src/noa_api/api/routes/whm_admin.py`
- Modify: `apps/api/src/noa_api/core/auth/deps.py`
- Modify: `apps/api/src/noa_api/core/auth/authorization.py`
- Test: `apps/api/tests/test_postgres_client.py`
- Test: `apps/api/tests/test_auth_login.py`
- Test: `apps/api/tests/test_assistant.py`
- Test: `apps/api/tests/test_whm_admin_routes.py`
- Test: `apps/api/tests/test_threads.py`

**Step 1: Convert `client.py` to cached getters**

Replace constructor-only helpers with cached getters:

```python
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(str(settings.postgres_url), pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory
```

Keep the module focused on a single engine/pool per process.

**Step 2: Update route and auth modules to use the getters**

Replace imports like:

```python
from noa_api.storage.postgres.client import create_engine, create_session_factory

_engine = create_engine()
_session_factory = create_session_factory(_engine)
```

with:

```python
from noa_api.storage.postgres.client import get_session_factory

_session_factory = get_session_factory()
```

Do this in `assistant.py`, `threads.py`, `whm_admin.py`, `core/auth/deps.py`, and `core/auth/authorization.py`.

**Step 3: Run the tests**

Run: `uv run pytest -q apps/api/tests/test_postgres_client.py apps/api/tests/test_auth_login.py apps/api/tests/test_assistant.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_threads.py`

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/api/src/noa_api/storage/postgres/client.py apps/api/src/noa_api/api/routes/assistant.py apps/api/src/noa_api/api/routes/threads.py apps/api/src/noa_api/api/routes/whm_admin.py apps/api/src/noa_api/core/auth/deps.py apps/api/src/noa_api/core/auth/authorization.py apps/api/tests/test_postgres_client.py
git commit -m "refactor(api): share postgres engine and session factory"
```

### Task 7: Add failing regression tests for sanitized tool failures

@test-driven-development

**Files:**
- Create: `apps/api/tests/test_tool_error_sanitizer.py`
- Modify: `apps/api/tests/test_agent_runner.py`
- Modify: `apps/api/tests/test_assistant_service.py`

**Step 1: Add a unit test for the sanitizer contract**

Create `apps/api/tests/test_tool_error_sanitizer.py`:

```python
import asyncio

from noa_api.core.tool_error_sanitizer import sanitize_tool_exception


def test_runtime_error_is_redacted_to_generic_code_and_message() -> None:
    sanitized = sanitize_tool_exception(RuntimeError("token=secret"))

    assert sanitized.error_code == "tool_execution_failed"
    assert sanitized.message == "Tool execution failed"


def test_timeout_is_mapped_to_timeout_code() -> None:
    sanitized = sanitize_tool_exception(asyncio.TimeoutError("slow backend"))

    assert sanitized.error_code == "timeout"
    assert sanitized.message == "Tool timed out"
```

**Step 2: Tighten the existing runner and assistant-service assertions**

In `apps/api/tests/test_agent_runner.py`, add a failing-tool test like:

```python
async def test_agent_runner_redacts_tool_execution_errors(monkeypatch) -> None:
    repo = _InMemoryActionToolRunRepository(action_requests={}, tool_runs={})

    async def failing_tool() -> dict[str, object]:
        raise RuntimeError("token=secret")

    tool = ToolDefinition(
        name="failing_tool",
        description="Always fails.",
        risk=ToolRisk.READ,
        parameters_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        execute=failing_tool,
    )

    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_definition", lambda name: tool if name == tool.name else None)
    monkeypatch.setattr("noa_api.core.agent.runner.get_tool_registry", lambda: (tool,))

    class _TwoTurnLLM:
        def __init__(self) -> None:
            self.turn = 0

        async def run_turn(self, *, messages, tools, on_text_delta=None):
            _ = messages, tools, on_text_delta
            self.turn += 1
            if self.turn == 1:
                return LLMTurnResponse(text="", tool_calls=[LLMToolCall(name=tool.name, arguments={})])
            return LLMTurnResponse(text="done", tool_calls=[])

    runner = AgentRunner(llm_client=_TwoTurnLLM(), action_tool_run_service=ActionToolRunService(repository=repo))
    result = await runner.run_turn(
        thread_messages=[{"role": "user", "parts": [{"type": "text", "text": "go"}]}],
        available_tool_names={tool.name},
        thread_id=uuid4(),
        requested_by_user_id=uuid4(),
    )

    run = next(iter(repo.tool_runs.values()))
    assert run.error == "tool_execution_failed"

    tool_message = next(message for message in result.messages if message.role == "tool")
    part = tool_message.parts[0]
    assert part["result"] == {
        "error": "Tool execution failed",
        "error_code": "tool_execution_failed",
    }
```

In `apps/api/tests/test_assistant_service.py`, tighten `test_assistant_service_approve_change_tool_failure_is_persisted_and_does_not_raise` to assert the same redacted payload and to ensure the raw `boom` string is not persisted.

**Step 3: Run tests to verify they fail**

Run: `uv run pytest -q apps/api/tests/test_tool_error_sanitizer.py apps/api/tests/test_agent_runner.py::test_agent_runner_redacts_tool_execution_errors apps/api/tests/test_assistant_service.py::test_assistant_service_approve_change_tool_failure_is_persisted_and_does_not_raise`

Expected: FAIL because current code persists raw `str(exc)` values.

**Step 4: Commit**

```bash
git add apps/api/tests/test_tool_error_sanitizer.py apps/api/tests/test_agent_runner.py apps/api/tests/test_assistant_service.py
git commit -m "test: require redacted tool failure payloads"
```

### Task 8: Implement tool-error sanitization in runner and assistant approval flows

@systematic-debugging

**Files:**
- Create: `apps/api/src/noa_api/core/tool_error_sanitizer.py`
- Modify: `apps/api/src/noa_api/core/agent/runner.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Test: `apps/api/tests/test_tool_error_sanitizer.py`
- Test: `apps/api/tests/test_agent_runner.py`
- Test: `apps/api/tests/test_assistant_service.py`

**Step 1: Implement the sanitizer helper**

Create `apps/api/src/noa_api/core/tool_error_sanitizer.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SanitizedToolError:
    error_code: str
    message: str


def sanitize_tool_exception(exc: Exception) -> SanitizedToolError:
    if isinstance(exc, asyncio.TimeoutError):
        return SanitizedToolError(error_code="timeout", message="Tool timed out")
    return SanitizedToolError(
        error_code="tool_execution_failed",
        message="Tool execution failed",
    )
```

Keep it intentionally small; do not over-design a full taxonomy in the first pass.

**Step 2: Use the sanitizer in `AgentRunner`**

In `apps/api/src/noa_api/core/agent/runner.py`, replace raw `str(exc)` persistence with the sanitized contract:

```python
except Exception as exc:
    sanitized = sanitize_tool_exception(exc)
    _ = await self._action_tool_run_service.fail_tool_run(
        tool_run_id=started.id,
        error=sanitized.error_code,
    )
    error_message = AgentMessage(
        role="tool",
        parts=[
            {
                "type": "tool-result",
                "toolName": tool.name,
                "toolCallId": tool_call_id,
                "result": {
                    "error": sanitized.message,
                    "error_code": sanitized.error_code,
                },
                "isError": True,
            }
        ],
    )
    return [call_message, error_message]
```

Log the raw exception once with context, but do not persist or return it.

**Step 3: Use the same sanitizer in approved-action execution**

Apply the same pattern in `apps/api/src/noa_api/api/routes/assistant.py` inside `AssistantService.approve_action()`:

- `fail_tool_run(..., error=sanitized.error_code)`
- tool-result payload contains `error` and `error_code`
- audit metadata should not store raw `str(exc)`

**Step 4: Run the tests**

Run: `uv run pytest -q apps/api/tests/test_tool_error_sanitizer.py apps/api/tests/test_agent_runner.py apps/api/tests/test_assistant_service.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/tool_error_sanitizer.py apps/api/src/noa_api/core/agent/runner.py apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_tool_error_sanitizer.py apps/api/tests/test_agent_runner.py apps/api/tests/test_assistant_service.py
git commit -m "fix(api): sanitize persisted tool failure errors"
```

### Task 9: Add failing web tests for richer `ApiError` parsing and shared user messaging

@test-driven-development

**Files:**
- Create: `apps/web/components/lib/fetch-helper.test.ts`
- Create: `apps/web/components/lib/error-message.test.ts`

**Step 1: Write `jsonOrThrow()` regression tests**

Create `apps/web/components/lib/fetch-helper.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { ApiError, jsonOrThrow } from "./fetch-helper";

describe("jsonOrThrow", () => {
  it("throws ApiError with detail, error_code, and request_id", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "User pending approval",
        error_code: "user_pending_approval",
        request_id: "req-123",
      }),
      {
        status: 403,
        headers: { "content-type": "application/json", "x-request-id": "req-123" },
      },
    );

    await expect(jsonOrThrow(response)).rejects.toMatchObject({
      status: 403,
      detail: "User pending approval",
      errorCode: "user_pending_approval",
      requestId: "req-123",
    } satisfies Partial<ApiError>);
  });
});
```

**Step 2: Write shared user-message tests**

Create `apps/web/components/lib/error-message.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { ApiError } from "./fetch-helper";
import { toUserMessage } from "./error-message";

describe("toUserMessage", () => {
  it("maps pending approval code to friendly copy", () => {
    const error = new ApiError(403, "User pending approval", {
      errorCode: "user_pending_approval",
      requestId: "req-123",
    });

    expect(toUserMessage(error, "Login failed")).toBe(
      "Your account is pending approval. Ask an admin to enable it.",
    );
  });

  it("uses fallback for unknown network failures", () => {
    expect(toUserMessage(new TypeError("fetch failed"), "Unable to reach API")).toBe(
      "Unable to reach API",
    );
  });
});
```

**Step 3: Run test to verify it fails**

Run: `npm test -- components/lib/fetch-helper.test.ts components/lib/error-message.test.ts`

Expected: FAIL because `ApiError` does not carry the new fields and the shared helper does not exist yet.

**Step 4: Commit**

```bash
git add apps/web/components/lib/fetch-helper.test.ts apps/web/components/lib/error-message.test.ts
git commit -m "test(web): cover typed API errors and user messaging"
```

### Task 10: Implement richer `ApiError` parsing and a shared `toUserMessage()` helper

@systematic-debugging

**Files:**
- Create: `apps/web/components/lib/error-message.ts`
- Modify: `apps/web/components/lib/fetch-helper.ts`
- Test: `apps/web/components/lib/fetch-helper.test.ts`
- Test: `apps/web/components/lib/error-message.test.ts`

**Step 1: Expand `ApiError` without breaking current callers**

Update `apps/web/components/lib/fetch-helper.ts` so the constructor stays backward-compatible but carries richer metadata:

```ts
export class ApiError extends Error {
  status: number;
  detail: string;
  errorCode?: string;
  requestId?: string;

  constructor(
    status: number,
    detail: string,
    options: { errorCode?: string; requestId?: string } = {},
  ) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.errorCode = options.errorCode;
    this.requestId = options.requestId;
  }
}
```

**Step 2: Teach `jsonOrThrow()` to parse the new envelope**

Use the response body first and the header as fallback:

```ts
const detail = payload?.detail ?? `Request failed (${response.status})`;
const errorCode = typeof payload?.error_code === "string" ? payload.error_code : undefined;
const requestId =
  typeof payload?.request_id === "string"
    ? payload.request_id
    : response.headers.get("x-request-id") ?? undefined;

throw new ApiError(response.status, detail, { errorCode, requestId });
```

**Step 3: Add `toUserMessage()`**

Create `apps/web/components/lib/error-message.ts`:

```ts
import { ApiError } from "@/components/lib/fetch-helper";

export function toUserMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.errorCode === "user_pending_approval") {
      return "Your account is pending approval. Ask an admin to enable it.";
    }
    return error.detail || fallback;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}
```

Keep it small and deterministic; do not introduce product-specific branching beyond the current pending-approval requirement.

**Step 4: Run the tests**

Run: `npm test -- components/lib/fetch-helper.test.ts components/lib/error-message.test.ts`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/web/components/lib/fetch-helper.ts apps/web/components/lib/error-message.ts apps/web/components/lib/fetch-helper.test.ts apps/web/components/lib/error-message.test.ts
git commit -m "feat(web): centralize API error parsing"
```

### Task 11: Add failing UI regression tests for login and the app-level error boundary

@test-driven-development

**Files:**
- Create: `apps/web/app/login/page.test.tsx`
- Create: `apps/web/app/error.test.tsx`

**Step 1: Add a login regression test that proves we branch on `error_code`, not prose**

Create `apps/web/app/login/page.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
const setAuthToken = vi.fn();
const setAuthUser = vi.fn();

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/components/lib/auth-store", () => ({ setAuthToken, setAuthUser }));

import LoginPage from "./page";

describe("LoginPage", () => {
  beforeEach(() => {
    push.mockReset();
    setAuthToken.mockReset();
    setAuthUser.mockReset();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: "Different backend prose",
            error_code: "user_pending_approval",
            request_id: "req-123",
          }),
          { status: 403, headers: { "content-type": "application/json" } },
        ),
      ),
    );
  });

  it("shows the pending approval message from error_code mapping", async () => {
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "user@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Your account is pending approval. Ask an admin to enable it.",
      );
    });
  });
});
```

**Step 2: Add a test for the global error UI**

Create `apps/web/app/error.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import GlobalError from "./error";

describe("GlobalError", () => {
  it("renders fallback copy and lets the user retry", () => {
    const reset = vi.fn();

    render(<GlobalError error={new Error("boom")} reset={reset} />);

    expect(screen.getByRole("heading")).toHaveTextContent("Something went wrong");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
```

**Step 3: Run tests to verify they fail**

Run: `npm test -- app/login/page.test.tsx app/error.test.tsx`

Expected: FAIL because login still parses responses manually and `app/error.tsx` does not exist.

**Step 4: Commit**

```bash
git add apps/web/app/login/page.test.tsx apps/web/app/error.test.tsx
git commit -m "test(web): cover login error mapping and global error UI"
```

### Task 12: Move login/admin flows to the shared helper and add `app/error.tsx`

@systematic-debugging

**Files:**
- Create: `apps/web/app/error.tsx`
- Modify: `apps/web/app/login/page.tsx`
- Modify: `apps/web/components/admin/users-admin-page.tsx`
- Modify: `apps/web/components/admin/whm-servers-admin-page.tsx`
- Test: `apps/web/app/login/page.test.tsx`
- Test: `apps/web/app/error.test.tsx`
- Test: `apps/web/components/admin/users-admin-page.test.tsx`
- Test: `apps/web/components/lib/runtime-provider.test.tsx`

**Step 1: Update login to use shared error parsing**

In `apps/web/app/login/page.tsx`, keep the direct `fetch()` call but stop manually branching on `payload.detail`:

```tsx
try {
  const response = await fetch(`${getApiUrl()}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  const payload = await jsonOrThrow<LoginResponse>(response);
  setAuthToken(payload.access_token);
  setAuthUser(payload.user ?? null);
  router.push("/assistant");
} catch (error) {
  setError(toUserMessage(error, "Unable to reach API"));
}
```

**Step 2: Replace duplicated admin helpers**

In both admin components, remove the local `toErrorMessage()` helper and import the shared one:

```tsx
import { toUserMessage } from "@/components/lib/error-message";

...
setLoadError(toUserMessage(error, "Unable to load users"));
setSaveError(toUserMessage(error, "Unable to save tool allowlist"));
setCreateError(toUserMessage(error, "Unable to create WHM server"));
```

**Step 3: Add `apps/web/app/error.tsx`**

Create a small client error boundary UI:

```tsx
"use client";

export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="w-full max-w-lg rounded-2xl border border-border bg-surface/70 p-6 shadow-[0_0.5rem_2rem_rgba(0,0,0,0.06)]">
        <h1 className="text-2xl font-semibold text-text">Something went wrong</h1>
        <p className="mt-2 font-ui text-sm text-muted">
          We hit an unexpected error while rendering this page.
        </p>
        <button className="mt-6 rounded-xl bg-accent px-4 py-2.5 font-ui text-sm font-semibold text-white" onClick={reset}>
          Try again
        </button>
      </div>
    </main>
  );
}
```

Preserve the established visual language from the current login/admin surfaces.

**Step 4: Run the targeted web tests**

Run: `npm test -- app/login/page.test.tsx app/error.test.tsx components/admin/users-admin-page.test.tsx components/lib/runtime-provider.test.tsx`

Expected: PASS.

**Step 5: Run a production build**

Run: `npm run build`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/web/app/error.tsx apps/web/app/login/page.tsx apps/web/components/admin/users-admin-page.tsx apps/web/components/admin/whm-servers-admin-page.tsx apps/web/app/login/page.test.tsx apps/web/app/error.test.tsx
git commit -m "feat(web): unify user-facing error handling"
```

### Task 13: Extract one small assistant seam without changing behavior

@systematic-debugging

**Files:**
- Create: `apps/api/src/noa_api/api/routes/assistant_repository.py`
- Create: `apps/api/src/noa_api/api/routes/assistant_tool_execution.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Test: `apps/api/tests/test_assistant_service.py`
- Test: `apps/api/tests/test_assistant.py`

**Step 1: Run the existing characterization suite first**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant.py`

Expected: PASS.

**Step 2: Extract `SQLAssistantRepository` into its own module**

Move the repository class and only the repository class from `assistant.py` into `apps/api/src/noa_api/api/routes/assistant_repository.py`.

The extracted file should keep the current public shape:

```python
class SQLAssistantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_thread(self, *, owner_user_id: UUID, thread_id: UUID) -> Thread | None:
        ...

    async def list_messages(self, *, thread_id: UUID) -> list[Message]:
        ...

    async def create_message(self, *, thread_id: UUID, role: str, parts: list[dict[str, Any]]) -> Message:
        ...

    async def create_audit_log(self, *, event_type: str, actor_email: str | None, tool_name: str | None, metadata: dict[str, object]) -> None:
        ...
```

**Step 3: Extract assistant tool-result builders**

Create `apps/api/src/noa_api/api/routes/assistant_tool_execution.py` with tiny pure helpers used by both normal tool execution and approved-action execution:

```python
def build_tool_result_part(*, tool_name: str, tool_call_id: str, result: dict[str, object], is_error: bool) -> dict[str, object]:
    return {
        "type": "tool-result",
        "toolName": tool_name,
        "toolCallId": tool_call_id,
        "result": result,
        "isError": is_error,
    }
```

Update `assistant.py` to import and use the helpers. Do not move business logic in the same task.

**Step 4: Re-run the characterization suite**

Run: `uv run pytest -q apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant_repository.py apps/api/src/noa_api/api/routes/assistant_tool_execution.py apps/api/src/noa_api/api/routes/assistant.py
git commit -m "refactor(api): extract assistant persistence helpers"
```

### Task 14: Run final verification for the foundation pass

@verification-before-completion

**Files:**
- Verify only: `apps/api/tests/test_request_context.py`
- Verify only: `apps/api/tests/test_health.py`
- Verify only: `apps/api/tests/test_auth_login.py`
- Verify only: `apps/api/tests/test_postgres_client.py`
- Verify only: `apps/api/tests/test_tool_error_sanitizer.py`
- Verify only: `apps/api/tests/test_agent_runner.py`
- Verify only: `apps/api/tests/test_assistant_service.py`
- Verify only: `apps/api/tests/test_assistant.py`
- Verify only: `apps/web/components/lib/fetch-helper.test.ts`
- Verify only: `apps/web/components/lib/error-message.test.ts`
- Verify only: `apps/web/app/login/page.test.tsx`
- Verify only: `apps/web/app/error.test.tsx`
- Verify only: `apps/web/components/admin/users-admin-page.test.tsx`
- Verify only: `apps/web/components/lib/runtime-provider.test.tsx`

**Step 1: Run the targeted backend suite**

Run: `uv run pytest -q apps/api/tests/test_request_context.py apps/api/tests/test_health.py apps/api/tests/test_auth_login.py apps/api/tests/test_postgres_client.py apps/api/tests/test_tool_error_sanitizer.py apps/api/tests/test_agent_runner.py apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant.py`

Expected: PASS.

**Step 2: Run the targeted web suite**

Run: `npm test -- components/lib/fetch-helper.test.ts components/lib/error-message.test.ts app/login/page.test.tsx app/error.test.tsx components/admin/users-admin-page.test.tsx components/lib/runtime-provider.test.tsx`

Expected: PASS.

**Step 3: Run the web production build**

Run: `npm run build`

Expected: PASS.

**Step 4: Commit the verified foundation pass**

```bash
git add apps/api apps/web docs/plans/2026-03-14-error-handling-and-logging-foundation-design.md docs/plans/2026-03-14-error-handling-and-logging-foundation-implementation-plan.md
git commit -m "feat: harden error handling and logging foundations"
```

**Step 5: Record follow-up work, but do not start it in this plan**

Capture later candidates in a follow-up note or issue:

- widen `error_code` coverage beyond auth routes
- add request-context binding to more assistant-specific logs
- evaluate `Sentry` or `OpenTelemetry` once the new baseline is stable
