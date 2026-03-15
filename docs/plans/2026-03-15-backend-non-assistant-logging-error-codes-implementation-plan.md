# Backend Non-Assistant Logging and Error Code Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend structured success-path logging across the remaining non-assistant backend routes and add a stable shared 422 request-validation `error_code` without changing existing response details.

**Architecture:** Keep the change inside the API layer. Reuse the existing stdlib logger plus `log_context(...)` in `admin.py`, `threads.py`, and `whm_admin.py`, and add one shared validation-error constant consumed by the centralized request-validation handler in `error_handling.py`. Preserve all current route details, statuses, and failure-path business codes.

**Tech Stack:** FastAPI, Pydantic, async route services, structlog-compatible stdlib logging, pytest, httpx, Ruff

---

### Task 1: Pin the shared 422 validation envelope contract

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_threads.py`
- Modify: `apps/api/src/noa_api/api/error_codes.py`
- Modify: `apps/api/src/noa_api/api/error_handling.py`

**Step 1: Tighten the existing oversized-title test in `apps/api/tests/test_threads.py`**

Expand `test_threads_routes_reject_oversized_title()` so it asserts the full shaped envelope instead of only the status code.

Suggested assertions:

```python
assert response.status_code == 422
body = response.json()
assert body["error_code"] == "request_validation_error"
assert isinstance(body["request_id"], str)
assert isinstance(body["detail"], list)
assert body["detail"][0]["loc"][-1] == "title"
assert response.headers["x-request-id"] == body["request_id"]
```

**Step 2: Run the focused validation test and confirm it fails first**

Run: `uv run pytest -q apps/api/tests/test_threads.py -k oversized_title`

Expected: FAIL because 422 responses do not yet include `error_code`.

**Step 3: Add the shared validation constant**

In `apps/api/src/noa_api/api/error_codes.py`, add:

```python
REQUEST_VALIDATION_ERROR = "request_validation_error"
```

Place it with the other shared API error-code constants.

**Step 4: Wire the centralized validation handler to the shared code**

Update `apps/api/src/noa_api/api/error_handling.py` so `request_validation_exception_handler(...)` calls `_json_error_response(...)` with the new shared `error_code` while keeping `detail=exc.errors()` unchanged.

Conceptually:

```python
return _json_error_response(
    status_code=422,
    detail=exc.errors(),
    request=request,
    error_code=REQUEST_VALIDATION_ERROR,
)
```

**Step 5: Re-run the focused validation test**

Run: `uv run pytest -q apps/api/tests/test_threads.py -k oversized_title`

Expected: PASS.

**Step 6: Commit the validation-envelope change**

```bash
git add apps/api/src/noa_api/api/error_codes.py apps/api/src/noa_api/api/error_handling.py apps/api/tests/test_threads.py
git commit -m "feat(api): shape validation errors with code"
```

### Task 2: Pin admin success-path structured logs

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_rbac.py`
- Modify: `apps/api/src/noa_api/api/routes/admin.py`

**Step 1: Add a structured-log capture helper in `apps/api/tests/test_rbac.py`**

Reuse the same pattern already proven in `apps/api/tests/test_auth_login.py`:

```python
@contextmanager
def _capture_structured_logs() -> Iterator[io.StringIO]:
    ...


def _load_log_payloads(stream: io.StringIO) -> list[dict[str, Any]]:
    ...
```

Import `configure_logging` from `noa_api.core.logging` and use `json.loads(...)` to parse one JSON payload per log line.

**Step 2: Add a failing admin success-log characterization test**

Extend `test_admin_routes_allow_admin_management_operations()` or add a dedicated test that wraps the existing successful admin route calls in `_capture_structured_logs()` and then asserts the expected events are present.

Suggested assertions:

```python
assert {payload["event"] for payload in payloads} >= {
    "admin_users_list_succeeded",
    "admin_tools_list_succeeded",
    "admin_user_active_updated",
    "admin_user_tools_updated",
}
```

Pin a few stable fields, for example:

```python
assert list_payload["user_count"] == 1
assert tools_payload["tool_count"] == 3
assert active_payload["is_active"] is False
assert isinstance(active_payload["target_user_id"], str)
assert tools_update_payload["assigned_tool_count"] == 2
assert tools_update_payload["request_path"] == f"/admin/users/{service.target_user_id}/tools"
```

**Step 3: Run the focused admin test and confirm it fails first**

Run: `uv run pytest -q apps/api/tests/test_rbac.py -k admin_routes_allow_admin_management_operations`

Expected: FAIL because the success events do not exist yet.

**Step 4: Add the admin success logs**

Update `apps/api/src/noa_api/api/routes/admin.py` so each successful route emits one event after the service call succeeds and before returning the response.

Recommended shapes:

```python
logger.info("admin_users_list_succeeded", extra={"user_count": len(users)})
logger.info("admin_tools_list_succeeded", extra={"tool_count": len(tools)})
logger.info("admin_user_active_updated", extra={"is_active": user.is_active})
logger.info(
    "admin_user_tools_updated",
    extra={"assigned_tool_count": len(user.tools)},
)
```

For the mutation routes, keep using `log_context(target_user_id=str(id), user_id=str(admin_user.user_id))` so the new events inherit stable acting/target user identifiers.

**Step 5: Re-run the focused admin test**

Run: `uv run pytest -q apps/api/tests/test_rbac.py -k admin_routes_allow_admin_management_operations`

Expected: PASS.

**Step 6: Commit the admin logging change**

```bash
git add apps/api/src/noa_api/api/routes/admin.py apps/api/tests/test_rbac.py
git commit -m "feat(api): log admin route success events"
```

### Task 3: Pin threads success-path structured logs

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_threads.py`
- Modify: `apps/api/src/noa_api/api/routes/threads.py`

**Step 1: Add the same structured-log capture helper pattern in `apps/api/tests/test_threads.py`**

Mirror the proven helper approach from `apps/api/tests/test_auth_login.py` so this file can capture JSON logs during route calls.

**Step 2: Add a failing threads success-log characterization test**

Add a dedicated test that performs a representative sequence:

- `GET /threads`
- `POST /threads` with a new `localId`
- repeated `POST /threads` with the same `localId`
- `GET /threads/{id}`
- `PATCH /threads/{id}`
- `POST /threads/{id}/archive`
- `POST /threads/{id}/unarchive`
- `POST /threads/{id}/title`
- `DELETE /threads/{id}`

Then assert the expected events exist, for example:

```python
assert {payload["event"] for payload in payloads} >= {
    "threads_list_succeeded",
    "thread_created",
    "thread_reused",
    "thread_retrieved",
    "thread_title_updated",
    "thread_archived",
    "thread_unarchived",
    "thread_title_generated",
    "thread_deleted",
}
```

Pin stable route-specific fields such as:

```python
assert created_payload["external_id_present"] is True
assert reused_payload["thread_id"] == created_payload["thread_id"]
assert generated_payload["generated_title_source"] == "request_messages"
assert delete_payload["request_path"] == f"/threads/{thread_id}"
```

If the stored-title branch is easier to pin with an existing test, you may additionally assert `thread_title_returned_existing` instead of extending the sequence further.

**Step 3: Run the focused threads test and confirm it fails first**

Run: `uv run pytest -q apps/api/tests/test_threads.py -k structured`

Expected: FAIL because the success events do not exist yet.

**Step 4: Add the threads success logs**

Update `apps/api/src/noa_api/api/routes/threads.py` so each successful route emits one event after the service result is known.

Recommended examples:

```python
logger.info("threads_list_succeeded", extra={"thread_count": len(threads)})
logger.info(
    "thread_created",
    extra={"external_id_present": payload is not None and payload.local_id is not None},
)
logger.info("thread_reused", extra={"external_id_present": True})
logger.info("thread_archived")
logger.info("thread_unarchived")
logger.info("thread_deleted")
logger.info(
    "thread_title_generated",
    extra={"generated_title_source": "request_messages"},
)
```

Use `with log_context(user_id=str(current_user.user_id), thread_id=str(id)):` around the single-thread routes so the event payloads inherit stable identifiers.

For title generation, pick a small stable discriminator only from the already-available branches in the route, such as:

- `existing_title`
- `request_messages`
- `persisted_messages`

**Step 5: Re-run the focused threads test**

Run: `uv run pytest -q apps/api/tests/test_threads.py -k structured`

Expected: PASS.

**Step 6: Commit the threads logging change**

```bash
git add apps/api/src/noa_api/api/routes/threads.py apps/api/tests/test_threads.py
git commit -m "feat(api): log thread route success events"
```

### Task 4: Pin WHM admin success-path structured logs

@test-driven-development

**Files:**
- Modify: `apps/api/tests/test_whm_admin_routes.py`
- Modify: `apps/api/src/noa_api/api/routes/whm_admin.py`

**Step 1: Add the structured-log capture helper pattern in `apps/api/tests/test_whm_admin_routes.py`**

Use the same helper approach already proven in the auth tests so this file can parse the JSON log lines emitted during route calls.

**Step 2: Add a failing WHM success-log characterization test**

Add a dedicated test that performs:

- `POST /admin/whm/servers`
- `GET /admin/whm/servers`
- `PATCH /admin/whm/servers/{id}`

Use a lightweight fake service that also supports:

- `delete_server(...) -> bool`
- `validate_server(...) -> ValidateWHMServerResponse`

Then call:

- `POST /admin/whm/servers/{id}/validate`
- `DELETE /admin/whm/servers/{id}`

Assert the success events exist:

```python
assert {payload["event"] for payload in payloads} >= {
    "whm_server_created",
    "whm_servers_list_succeeded",
    "whm_server_updated",
    "whm_server_validated",
    "whm_server_deleted",
}
```

Pin safe fields, for example:

```python
assert create_payload["server_name"] == "web1"
assert list_payload["server_count"] == 1
assert validated_payload["validation_ok"] is True
assert isinstance(delete_payload["server_id"], str)
```

**Step 3: Run the focused WHM test and confirm it fails first**

Run: `uv run pytest -q apps/api/tests/test_whm_admin_routes.py -k structured`

Expected: FAIL because the success events do not exist yet.

**Step 4: Add the WHM admin success logs**

Update `apps/api/src/noa_api/api/routes/whm_admin.py` so successful list/create/update/delete/validate routes each emit one event.

Recommended examples:

```python
logger.info("whm_servers_list_succeeded", extra={"server_count": len(servers)})
logger.info("whm_server_created")
logger.info("whm_server_updated")
logger.info("whm_server_deleted")
logger.info(
    "whm_server_validated",
    extra={
        "validation_ok": result.ok,
        "validation_error_code": result.error_code,
    },
)
```

For create/update/delete/validate, keep the existing `log_context(...)` blocks and add `user_id` to the bound context when the acting admin is available.

**Step 5: Re-run the focused WHM test**

Run: `uv run pytest -q apps/api/tests/test_whm_admin_routes.py -k structured`

Expected: PASS.

**Step 6: Commit the WHM logging change**

```bash
git add apps/api/src/noa_api/api/routes/whm_admin.py apps/api/tests/test_whm_admin_routes.py
git commit -m "feat(api): log whm admin success events"
```

### Task 5: Run focused regression coverage for the combined pass

@test-driven-development

**Files:**
- Verify only: `apps/api/tests/test_rbac.py`
- Verify only: `apps/api/tests/test_threads.py`
- Verify only: `apps/api/tests/test_whm_admin_routes.py`

**Step 1: Run the focused route and validation tests together**

Run:

```bash
uv run pytest -q apps/api/tests/test_rbac.py apps/api/tests/test_threads.py apps/api/tests/test_whm_admin_routes.py
```

Expected: PASS.

**Step 2: Fix any log payload drift before broad verification**

If any test fails because an event name or field shape drifted, correct the route log payloads rather than weakening the assertions.

**Step 3: Commit the focused pass checkpoint**

```bash
git add apps/api/src/noa_api/api/routes/admin.py apps/api/src/noa_api/api/routes/threads.py apps/api/src/noa_api/api/routes/whm_admin.py apps/api/src/noa_api/api/error_codes.py apps/api/src/noa_api/api/error_handling.py apps/api/tests/test_rbac.py apps/api/tests/test_threads.py apps/api/tests/test_whm_admin_routes.py
git commit -m "feat(api): expand non-assistant logging coverage"
```

### Task 6: Refresh the audit handoff and run full backend verification

@verification-before-completion

**Files:**
- Modify: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`

**Step 1: Refresh the audit report**

Update `docs/reports/2026-03-14-error-handling-and-logging-audit.md` so it records:

- broader structured success logging now covers admin, threads, and WHM admin route flows
- shared request validation responses now include a stable `request_validation_error` code
- the remaining backend follow-up is now telemetry reconsideration plus any future deeper helper-level logging/code-catalog work, not this route slice

**Step 2: Run focused verification for this continuation pass**

Run:

```bash
uv run pytest -q apps/api/tests/test_rbac.py apps/api/tests/test_threads.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_request_context.py
```

Expected: PASS.

**Step 3: Run Ruff across backend source and tests**

Run:

```bash
uv run ruff check apps/api/src apps/api/tests
```

Expected: `All checks passed!`

**Step 4: Run the full backend suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS for the full API suite.

**Step 5: Commit the audit refresh**

```bash
git add docs/reports/2026-03-14-error-handling-and-logging-audit.md
git commit -m "docs: refresh backend logging follow-up"
```
