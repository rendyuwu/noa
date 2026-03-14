# Backend Error Codes, Assistant Decomposition, and Structured Logging Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expand backend `error_code` coverage beyond auth/tool flows, keep shrinking `apps/api/src/noa_api/api/routes/assistant.py`, add richer structured logging context, and update the audit report so the branch stays trackable.

**Architecture:** Build on the existing request-id and API-error foundation instead of replacing it. Add a small shared backend error-code catalog, migrate selected route errors to `ApiHTTPException`, extract assistant command and streaming helpers from `apps/api/src/noa_api/api/routes/assistant.py`, and add reusable structlog context-binding helpers so assistant and route logs emit stable fields. Leave telemetry-vendor installation deferred until the assistant seams stabilize.

**Tech Stack:** FastAPI, structlog, async SQLAlchemy, pytest

---

### Task 1: Pin the expanded backend error contract with failing route tests

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_threads.py`
- Modify: `apps/api/tests/test_rbac.py`
- Modify: `apps/api/tests/test_whm_admin_routes.py`
- Modify: `apps/api/tests/test_assistant.py`

**Step 1: Write the failing tests**

Update the existing route tests so they install centralized error handling and assert the new additive contract: stable `detail`, stable `error_code`, and `request_id` matching the `X-Request-Id` header.

Use the real helper in each test app setup:

```python
from fastapi import FastAPI

from noa_api.api.error_handling import install_error_handling


app = FastAPI()
install_error_handling(app)
app.include_router(threads_router)
```

Add concrete assertions to existing scenarios:

```python
assert response.status_code == 404
body = response.json()
assert body["detail"] == "Thread not found"
assert body["error_code"] == "thread_not_found"
assert response.headers["x-request-id"] == body["request_id"]
```

Pin at least these cases:

- `apps/api/tests/test_threads.py`
  - inactive-user access returns `user_pending_approval`
  - missing thread access returns `thread_not_found`
- `apps/api/tests/test_rbac.py`
  - unknown admin tools returns `unknown_tools`
  - disabling the last active admin returns `last_active_admin`
- `apps/api/tests/test_whm_admin_routes.py`
  - add a missing-server case that returns `whm_server_not_found`
- `apps/api/tests/test_assistant.py`
  - edit-style add-message returns `message_edit_not_supported`
  - non-user add-message role returns `invalid_add_message_role`

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_assistant.py`

Expected: FAIL because the touched routes still mostly raise uncoded `HTTPException` values today.

**Step 3: Commit**

```bash
git add apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_assistant.py
git commit -m "test(api): pin backend route error codes"
```

### Task 2: Add a shared backend error-code catalog and migrate non-auth routes

@test-driven-development

**Files:**
- Create: `apps/api/src/noa_api/api/error_codes.py`
- Modify: `apps/api/src/noa_api/api/routes/threads.py`
- Modify: `apps/api/src/noa_api/api/routes/admin.py`
- Modify: `apps/api/src/noa_api/api/routes/whm_admin.py`
- Test: `apps/api/tests/test_threads.py`
- Test: `apps/api/tests/test_rbac.py`
- Test: `apps/api/tests/test_whm_admin_routes.py`

**Step 1: Write the minimal shared catalog**

Create `apps/api/src/noa_api/api/error_codes.py` with only the constants needed by the touched routes:

```python
USER_PENDING_APPROVAL = "user_pending_approval"
ADMIN_ACCESS_REQUIRED = "admin_access_required"
THREAD_NOT_FOUND = "thread_not_found"
UNKNOWN_TOOLS = "unknown_tools"
LAST_ACTIVE_ADMIN = "last_active_admin"
SELF_DEACTIVATE_ADMIN = "self_deactivate_admin"
WHM_SERVER_NOT_FOUND = "whm_server_not_found"
WHM_SERVER_NAME_EXISTS = "whm_server_name_exists"
```

Keep the file small. Do not add speculative codes for routes you are not touching in this pass.

**Step 2: Replace plain `HTTPException` branches with coded `ApiHTTPException` branches**

In `apps/api/src/noa_api/api/routes/threads.py`, `apps/api/src/noa_api/api/routes/admin.py`, and `apps/api/src/noa_api/api/routes/whm_admin.py`, import `ApiHTTPException` and the new constants, then preserve `detail` while adding `error_code`.

Example shape:

```python
raise ApiHTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Thread not found",
    error_code=THREAD_NOT_FOUND,
)
```

Map the existing admin exceptions explicitly:

- `LastActiveAdminError` -> `last_active_admin`
- `SelfDeactivateAdminError` -> `self_deactivate_admin`
- `UnknownToolError` -> `unknown_tools`

Map the existing WHM conflicts/not-found cases explicitly:

- duplicate server name -> `whm_server_name_exists`
- missing server -> `whm_server_not_found`

**Step 3: Run tests to verify the implementation**

Run: `uv run pytest -q apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py`

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/api/src/noa_api/api/error_codes.py apps/api/src/noa_api/api/routes/threads.py apps/api/src/noa_api/api/routes/admin.py apps/api/src/noa_api/api/routes/whm_admin.py apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py
git commit -m "feat(api): expand stable route error codes"
```

### Task 3: Add failing tests for extracted assistant command and streaming helpers

@test-driven-development

**Files:**
- Create: `apps/api/tests/test_assistant_commands.py`
- Create: `apps/api/tests/test_assistant_streaming.py`
- Modify: `apps/api/tests/test_assistant.py`

**Step 1: Write the failing helper tests**

Create focused tests for the seams you want to extract out of `apps/api/src/noa_api/api/routes/assistant.py`.

In `apps/api/tests/test_assistant_commands.py`, pin command validation/application behavior with fakes:

```python
import pytest

from noa_api.api.routes.assistant_commands import validate_commands


def test_validate_commands_rejects_edit_style_add_message() -> None:
    with pytest.raises(ApiHTTPException) as exc_info:
        validate_commands([
            AddMessageCommand(
                type="add-message",
                parentId="parent-1",
                sourceId="source-1",
                message={"role": "user", "parts": [{"type": "text", "text": "Edited"}]},
            )
        ])

    assert exc_info.value.detail == "Editing existing messages is not supported yet"
    assert exc_info.value.error_code == "message_edit_not_supported"
```

In `apps/api/tests/test_assistant_streaming.py`, pin the streaming helper behavior:

```python
def test_remove_streaming_placeholder_strips_assistant_streaming_message() -> None:
    messages = [
        {"id": "assistant-streaming", "role": "assistant", "parts": [{"type": "text", "text": "..."}]},
        {"id": "persisted", "role": "assistant", "parts": [{"type": "text", "text": "Saved"}]},
    ]

    assert remove_streaming_placeholder(messages) == [
        {"id": "persisted", "role": "assistant", "parts": [{"type": "text", "text": "Saved"}]}
    ]
```

Also keep the existing route-level assertions in `apps/api/tests/test_assistant.py` so the transport behavior remains characterized after extraction.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q apps/api/tests/test_assistant.py apps/api/tests/test_assistant_commands.py apps/api/tests/test_assistant_streaming.py`

Expected: FAIL because the extracted helper modules do not exist yet.

**Step 3: Commit**

```bash
git add apps/api/tests/test_assistant.py apps/api/tests/test_assistant_commands.py apps/api/tests/test_assistant_streaming.py
git commit -m "test(api): cover assistant helper seams"
```

### Task 4: Extract assistant command and streaming helpers and add assistant error codes

@test-driven-development

**Files:**
- Modify: `apps/api/src/noa_api/api/error_codes.py`
- Create: `apps/api/src/noa_api/api/routes/assistant_commands.py`
- Create: `apps/api/src/noa_api/api/routes/assistant_streaming.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Test: `apps/api/tests/test_assistant.py`
- Test: `apps/api/tests/test_assistant_commands.py`
- Test: `apps/api/tests/test_assistant_streaming.py`
- Test: `apps/api/tests/test_assistant_service.py`

**Step 1: Extend the error-code catalog only for the assistant cases you are extracting**

Add only the needed assistant constants, for example:

```python
MESSAGE_EDIT_NOT_SUPPORTED = "message_edit_not_supported"
INVALID_ADD_MESSAGE_ROLE = "invalid_add_message_role"
UNKNOWN_TOOL_CALL_ID = "unknown_tool_call_id"
TOOL_CALL_NOT_FOUND = "tool_call_not_found"
TOOL_CALL_NOT_AWAITING_RESULT = "tool_call_not_awaiting_result"
ACTION_REQUEST_NOT_FOUND = "action_request_not_found"
ACTION_REQUEST_ALREADY_DECIDED = "action_request_already_decided"
CHANGE_APPROVAL_REQUIRED = "change_approval_required"
TOOL_ACCESS_DENIED = "tool_access_denied"
```

Do not add speculative constants for future work.

**Step 2: Move command validation and application into `assistant_commands.py`**

Create `apps/api/src/noa_api/api/routes/assistant_commands.py` with helpers that accept existing assistant command models and delegate to `AssistantService`.

Suggested shape:

```python
async def apply_commands(
    *,
    commands: list[AssistantCommand],
    assistant_service: AssistantService,
    current_user: AuthorizationUser,
    payload: AssistantRequest,
    authorization_service: AuthorizationService,
) -> None:
    ...


def validate_commands(commands: list[AssistantCommand]) -> None:
    ...
```

Keep the current route behavior unchanged while moving the branching logic out of `assistant.py`.

**Step 3: Move streaming-state helpers into `assistant_streaming.py`**

Create `apps/api/src/noa_api/api/routes/assistant_streaming.py` with focused helpers for:

- making/removing the streaming placeholder message
- appending a fallback assistant error message safely
- flushing the controller state manager defensively
- refreshing controller state from the repository when possible

Pull the nested helper functions out of the transport callback where possible.

**Step 4: Rewire `assistant.py` to be the thin coordinator**

After the extraction, `apps/api/src/noa_api/api/routes/assistant.py` should mostly:

- define request/response models and dependencies
- call `validate_commands(payload.commands)` before creating the run callback
- call `apply_commands(...)` during the pre-agent phase
- use shared streaming helpers instead of nested callback-only utilities

Preserve the existing stream-resilience behavior: user-visible fallback assistant text, `CancelledError` re-raise, and no escaped exceptions from the callback.

**Step 5: Run tests to verify the implementation**

Run: `uv run pytest -q apps/api/tests/test_assistant.py apps/api/tests/test_assistant_commands.py apps/api/tests/test_assistant_streaming.py apps/api/tests/test_assistant_service.py`

Expected: PASS.

**Step 6: Commit**

```bash
git add apps/api/src/noa_api/api/error_codes.py apps/api/src/noa_api/api/routes/assistant_commands.py apps/api/src/noa_api/api/routes/assistant_streaming.py apps/api/src/noa_api/api/routes/assistant.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_commands.py apps/api/tests/test_assistant_streaming.py apps/api/tests/test_assistant_service.py
git commit -m "refactor(api): extract assistant command and streaming helpers"
```

### Task 5: Pin structured logging-context behavior with failing tests

@test-driven-development

**Files:**
- Create: `apps/api/tests/test_logging_context.py`

**Step 1: Write the failing tests**

Create `apps/api/tests/test_logging_context.py` and test helper behavior directly instead of snapshotting rendered JSON.

Use `structlog.contextvars.get_contextvars()` so the test stays cheap and deterministic:

```python
from structlog.contextvars import clear_contextvars, get_contextvars

from noa_api.core.logging_context import bind_log_context


def test_bind_log_context_ignores_none_values() -> None:
    clear_contextvars()
    bind_log_context(user_id="user-1", thread_id=None, tool_name="demo")

    context = get_contextvars()
    assert context["user_id"] == "user-1"
    assert context["tool_name"] == "demo"
    assert "thread_id" not in context
```

Add a second test for scoped reset behavior so request/assistant context does not leak across calls.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q apps/api/tests/test_logging_context.py`

Expected: FAIL because `apps/api/src/noa_api/core/logging_context.py` does not exist yet.

**Step 3: Commit**

```bash
git add apps/api/tests/test_logging_context.py
git commit -m "test(api): cover structured logging context helpers"
```

### Task 6: Add logging-context helpers and convert assistant logs to structured events

@test-driven-development

**Files:**
- Create: `apps/api/src/noa_api/core/logging_context.py`
- Modify: `apps/api/src/noa_api/core/logging.py`
- Modify: `apps/api/src/noa_api/api/error_handling.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/routes/admin.py`
- Modify: `apps/api/src/noa_api/api/routes/threads.py`
- Modify: `apps/api/src/noa_api/api/routes/whm_admin.py`
- Test: `apps/api/tests/test_logging_context.py`
- Test: `apps/api/tests/test_assistant.py`
- Test: `apps/api/tests/test_threads.py`
- Test: `apps/api/tests/test_rbac.py`
- Test: `apps/api/tests/test_whm_admin_routes.py`

**Step 1: Add a tiny context-binding helper module**

Create `apps/api/src/noa_api/core/logging_context.py` around `structlog.contextvars`:

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from structlog.contextvars import bind_contextvars, reset_contextvars


def bind_log_context(**values: object) -> dict[str, object]:
    filtered = {key: value for key, value in values.items() if value is not None}
    if not filtered:
        return {}
    return bind_contextvars(**filtered)


@contextmanager
def log_context(**values: object) -> Iterator[None]:
    tokens = bind_log_context(**values)
    try:
        yield
    finally:
        if tokens:
            reset_contextvars(**tokens)
```

Keep the API minimal and scoped to what the tests require.

**Step 2: Make request/assistant logs emit structured fields**

Update `apps/api/src/noa_api/core/logging.py` and the touched routes so the logs carry stable context as fields rather than embedding everything into formatted strings.

In `apps/api/src/noa_api/api/error_handling.py`, consider logging request completion with fields such as:

```python
logger.info(
    "api_request_completed",
    method=request.method,
    path=request.url.path,
    status_code=response.status_code,
    duration_ms=duration_ms,
)
```

In `apps/api/src/noa_api/api/routes/assistant.py`, bind request-scoped assistant context and convert failure logs to event-style calls such as:

```python
logger.exception(
    "assistant_run_failed_agent",
    thread_id=str(payload.thread_id),
    user_id=str(current_user.user_id),
)
```

Only add logs where they improve diagnosis. Do not spam every normal branch with redundant info logs.

**Step 3: Run tests to verify the implementation**

Run: `uv run pytest -q apps/api/tests/test_logging_context.py apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py`

Expected: PASS.

**Step 4: Commit**

```bash
git add apps/api/src/noa_api/core/logging_context.py apps/api/src/noa_api/core/logging.py apps/api/src/noa_api/api/error_handling.py apps/api/src/noa_api/api/routes/assistant.py apps/api/src/noa_api/api/routes/admin.py apps/api/src/noa_api/api/routes/threads.py apps/api/src/noa_api/api/routes/whm_admin.py apps/api/tests/test_logging_context.py apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py
git commit -m "feat(api): add structured logging context to backend flows"
```

### Task 7: Update the audit report and verify the backend slice

@verification-before-completion

**Files:**
- Modify: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`
- Reference: `docs/plans/2026-03-14-backend-error-code-assistant-logging-design.md`
- Reference: `docs/plans/2026-03-14-backend-error-code-assistant-logging-implementation-plan.md`

**Step 1: Update the report status sections**

Revise `docs/reports/2026-03-14-error-handling-and-logging-audit.md` so it cleanly lists:

- done in this follow-up backend pass
- not yet done after this pass
- recommended next backend-only steps

Make the updated summary explicitly reference the new design/plan docs so the next session can resume from them.

**Step 2: Run the targeted backend verification suite**

Run: `uv run pytest -q apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py apps/api/tests/test_logging_context.py`

Expected: PASS.

**Step 3: Run the broader API safety check**

Run: `uv run pytest -q`

Expected: PASS. If this is too slow or blocked by unrelated failures, record the exact failing tests in the report update before stopping.

**Step 4: Commit**

```bash
git add docs/reports/2026-03-14-error-handling-and-logging-audit.md docs/plans/2026-03-14-backend-error-code-assistant-logging-design.md docs/plans/2026-03-14-backend-error-code-assistant-logging-implementation-plan.md
git commit -m "docs: plan backend error handling and logging follow-up"
```
