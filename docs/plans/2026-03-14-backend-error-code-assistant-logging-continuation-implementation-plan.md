# Backend Error Code and Logging Continuation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Finish the remaining assistant `error_code` gaps for malformed and missing IDs, keep shrinking `apps/api/src/noa_api/api/routes/assistant.py` so route code stops owning service-level HTTP mapping, extend structured log context across more successful backend flows, and include the unrelated `auth.py` lint cleanup so full backend Ruff can pass.

**Architecture:** Keep the branch incremental. Add focused failing tests for assistant malformed/missing `toolCallId` and `actionRequestId` paths, move assistant-specific parsing and domain error normalization behind assistant helper types instead of plain route-shaped `HTTPException` helpers, then expand `log_context(...)` where successful assistant and touched backend flows already have stable identifiers available. Finish with a narrow `auth.py` import cleanup, targeted verification, and full backend Ruff.

**Tech Stack:** FastAPI, Pydantic, structlog-compatible stdlib logging, async SQLAlchemy, pytest, Ruff

---

### Task 1: Pin the remaining assistant malformed and missing ID contract

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_assistant.py`
- Modify: `apps/api/tests/test_assistant_service.py`
- Modify: `apps/api/tests/test_assistant_commands.py`

**Step 1: Write the failing tests**

Add focused assertions for the still-uncoded assistant ID validation gaps.

Cover at least these cases:

- `add-tool-result` with missing `toolCallId`
- `add-tool-result` with invalid `toolCallId`
- `approve-action` with missing `actionRequestId`
- `approve-action` with invalid `actionRequestId`
- `deny-action` with missing `actionRequestId`
- `deny-action` with invalid `actionRequestId`

Route-level expectations should stay additive:

```python
assert response.status_code == 400
body = response.json()
assert body["detail"] == "Missing toolCallId"
assert body["error_code"] == "missing_tool_call_id"
assert response.headers["x-request-id"] == body["request_id"]
```

Service/helper-level expectations should assert the same `detail` text and stable `error_code` values without going through SSE.

**Step 2: Run tests to verify they fail**

Run: `uv run pytest -q apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant_commands.py`

Expected: FAIL because missing/invalid assistant IDs still map through plain uncoded `_parse_uuid(...)` branches.

### Task 2: Replace assistant route-shaped ID parsing and service HTTP mapping with assistant-specific errors

@test-driven-development

**Files:**
- Modify: `apps/api/src/noa_api/api/error_codes.py`
- Create: `apps/api/src/noa_api/api/routes/assistant_errors.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/routes/assistant_commands.py`
- Test: `apps/api/tests/test_assistant.py`
- Test: `apps/api/tests/test_assistant_service.py`
- Test: `apps/api/tests/test_assistant_commands.py`

**Step 1: Extend the error-code catalog only for the assistant ID cases you are closing**

Add only the constants needed for this continuation, for example:

```python
MISSING_TOOL_CALL_ID = "missing_tool_call_id"
INVALID_TOOL_CALL_ID = "invalid_tool_call_id"
MISSING_ACTION_REQUEST_ID = "missing_action_request_id"
INVALID_ACTION_REQUEST_ID = "invalid_action_request_id"
```

Do not add speculative constants for future assistant flows.

**Step 2: Create assistant-specific error helpers**

Create `apps/api/src/noa_api/api/routes/assistant_errors.py` with a small assistant-domain error layer.

Suggested shape:

```python
class AssistantRouteError(Exception):
    status_code: int
    detail: str
    error_code: str | None


def parse_tool_call_id(raw: str | None) -> UUID:
    ...


def parse_action_request_id(raw: str | None) -> UUID:
    ...


def to_api_http_exception(exc: AssistantRouteError) -> ApiHTTPException:
    ...
```

Keep `detail` strings exactly as they are today; only make the error contract stable.

**Step 3: Rewire assistant service and route code through assistant errors instead of `_http_error(...)`**

In `apps/api/src/noa_api/api/routes/assistant.py`:

- remove or shrink the plain `_http_error(...)` / `_parse_uuid(...)` helpers
- let service methods raise assistant-domain errors instead of raw `HTTPException`
- translate assistant-domain errors to `ApiHTTPException` at one thin boundary
- keep existing `Thread not found`, `Unknown tool call id`, `Tool call not found`, `Tool call is not awaiting result`, `Action request not found`, `Action request already decided`, `Only CHANGE actions require approval`, and `Tool access denied` behavior intact

If a helper belongs naturally in `assistant_commands.py`, move it there rather than keeping it in the route file.

**Step 4: Run tests to verify the implementation**

Run: `uv run pytest -q apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant_commands.py apps/api/tests/test_assistant_streaming.py`

Expected: PASS.

### Task 3: Expand structured log context through more successful backend flows

@test-driven-development

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant.py`
- Modify: `apps/api/src/noa_api/api/routes/admin.py`
- Modify: `apps/api/src/noa_api/api/routes/threads.py`
- Modify: `apps/api/src/noa_api/api/routes/whm_admin.py`
- Test: `apps/api/tests/test_assistant.py`
- Test: `apps/api/tests/test_logging_context.py`

**Step 1: Add or extend focused tests where log context behavior is directly testable**

Prefer cheap assertions around helper behavior or logger records already captured in tests. Do not add brittle rendered-JSON snapshots.

Add coverage only where you can deterministically prove the new bound context or structured event fields.

**Step 2: Bind context in successful flows that already have natural identifiers**

In `apps/api/src/noa_api/api/routes/assistant.py`, extend `log_context(...)` through the successful operation scopes that already know:

- `user_id`
- `thread_id`
- `tool_name`
- `tool_run_id`
- `action_request_id`

In the touched admin/threads/WHM routes, add context only where the identifier is already present and operationally useful. Keep the event volume restrained; prefer richer context over more log lines.

**Step 3: Run tests to verify the implementation**

Run: `uv run pytest -q apps/api/tests/test_logging_context.py apps/api/tests/test_assistant.py apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py`

Expected: PASS.

### Task 4: Make final backend verification green and prepare the branch for commit/PR choice

@verification-before-completion

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/auth.py`
- Reference: `docs/plans/2026-03-14-backend-error-code-assistant-logging-design.md`
- Reference: `docs/plans/2026-03-14-backend-error-code-assistant-logging-implementation-plan.md`
- Reference: `docs/plans/2026-03-14-backend-error-code-assistant-logging-continuation-implementation-plan.md`
- Reference: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`

**Step 1: Fix the unrelated full-Ruff blocker**

Remove the unused `HTTPException` import from `apps/api/src/noa_api/api/routes/auth.py`.

**Step 2: Run the focused assistant/backend verification suite**

Run: `uv run pytest -q apps/api/tests/test_assistant.py apps/api/tests/test_assistant_service.py apps/api/tests/test_assistant_commands.py apps/api/tests/test_assistant_streaming.py apps/api/tests/test_logging_context.py apps/api/tests/test_threads.py apps/api/tests/test_rbac.py apps/api/tests/test_whm_admin_routes.py`

Expected: PASS.

**Step 3: Run the broader backend checks**

Run: `uv run pytest -q`

Expected: PASS.

Run: `uv run ruff check src tests`

Expected: PASS.

**Step 4: Commit and choose the branch handoff**

Commit the continuation work with a focused message, then decide whether to open a PR now or leave the branch ready for later review.
