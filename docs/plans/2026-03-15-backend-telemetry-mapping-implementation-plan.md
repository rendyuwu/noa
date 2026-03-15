# Backend Telemetry Mapping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a small vendor-neutral backend telemetry seam that reuses the current stable event vocabulary for traces, metrics, and external-reporting candidates without changing the existing log contracts.

**Architecture:** Introduce a narrow internal telemetry abstraction in `apps/api/src/noa_api/core/telemetry.py`, start with a no-op/default implementation plus focused tests, and wire it into the already-stable backend request, auth, assistant, and management flows. Keep structured logs as-is and have telemetry reuse the same event names and stable fields rather than redefining them.

**Tech Stack:** FastAPI, Python 3.11, stdlib logging, structlog contextvars, pytest, uv

---

### Task 1: Create the vendor-neutral telemetry seam

**Files:**
- Create: `apps/api/src/noa_api/core/telemetry.py`
- Modify: `apps/api/src/noa_api/main.py`
- Test: `apps/api/tests/test_telemetry.py`

**Step 1: Write the failing test**

Add a focused test module that defines the expected surface for the internal telemetry seam.

```python
from noa_api.core.telemetry import NoOpTelemetryRecorder, TelemetryEvent


def test_noop_recorder_accepts_trace_metric_and_reporting_inputs() -> None:
    recorder = NoOpTelemetryRecorder()

    recorder.record_trace_event(
        TelemetryEvent(name="api_request_completed", attributes={"status_code": 200})
    )
    recorder.increment_metric(
        name="api.requests.total", value=1, attributes={"status_code": 200}
    )
    recorder.report_exception_candidate(
        name="api_unhandled_exception", attributes={"error_type": "ValueError"}
    )
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_telemetry.py`

Expected: FAIL because `noa_api.core.telemetry` does not exist yet.

**Step 3: Write minimal implementation**

Create a small internal abstraction with:

```python
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    name: str
    attributes: Mapping[str, object]


class NoOpTelemetryRecorder:
    def record_trace_event(self, event: TelemetryEvent) -> None:
        return None

    def increment_metric(
        self, *, name: str, value: int | float = 1, attributes: Mapping[str, object]
    ) -> None:
        return None

    def report_exception_candidate(
        self, *, name: str, attributes: Mapping[str, object]
    ) -> None:
        return None
```

Also expose a cached app-level accessor from `main.py` or the new module so request/auth/assistant/routes can import one shared recorder without creating per-module instances.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_telemetry.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/telemetry.py apps/api/src/noa_api/main.py apps/api/tests/test_telemetry.py
git commit -m "feat(api): add backend telemetry seam"
```

### Task 2: Instrument request lifecycle telemetry

**Files:**
- Modify: `apps/api/src/noa_api/api/error_handling.py`
- Modify: `apps/api/src/noa_api/core/telemetry.py`
- Test: `apps/api/tests/test_request_context.py`
- Test: `apps/api/tests/test_telemetry.py`

**Step 1: Write the failing test**

Extend request-context or telemetry tests so they verify the request middleware emits the mapped request telemetry using the same stable fields already present in logs.

```python
def test_request_completion_records_request_metric_and_trace_event(...):
    response = client.get("/test-endpoint")

    assert response.status_code == 200
    assert recorder.trace_events[-1].name == "api_request_completed"
    assert recorder.trace_events[-1].attributes["status_code"] == 200
    assert recorder.metrics[-1]["name"] == "api.requests.total"
```

Also add coverage for unhandled exceptions:

```python
def test_unhandled_exception_records_reporting_candidate(...):
    response = client.get("/boom")

    assert response.status_code == 500
    assert recorder.reports[-1]["name"] == "api_unhandled_exception"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_request_context.py tests/test_telemetry.py`

Expected: FAIL because request middleware and exception handling do not call the recorder yet.

**Step 3: Write minimal implementation**

In `apps/api/src/noa_api/api/error_handling.py`, call the shared recorder in two places:

- after `api_request_completed` is logged, emit a request trace event and request counter/histogram observation using `request_method`, `request_path`, `status_code`, and `duration_ms`
- inside `unhandled_exception_handler(...)`, emit an external-reporting candidate for `api_unhandled_exception` using `error_type`, `request_method`, and `request_path`

Keep the existing logging and HTTP response behavior unchanged.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_request_context.py tests/test_telemetry.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/api/error_handling.py apps/api/src/noa_api/core/telemetry.py apps/api/tests/test_request_context.py apps/api/tests/test_telemetry.py
git commit -m "feat(api): record request telemetry"
```

### Task 3: Instrument auth telemetry from the stable auth event set

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/auth.py`
- Modify: `apps/api/src/noa_api/api/auth_dependencies.py`
- Modify: `apps/api/src/noa_api/core/telemetry.py`
- Test: `apps/api/tests/test_auth_login.py`
- Test: `apps/api/tests/test_rbac.py`
- Test: `apps/api/tests/test_telemetry.py`

**Step 1: Write the failing test**

Add focused tests that verify auth success and rejection flows emit telemetry with the same stable auth fields already present in logs.

```python
def test_login_rejection_records_auth_metric(...):
    response = client.post("/auth/login", json={"email": "a@example.com", "password": "bad"})

    assert response.status_code == 401
    assert recorder.metrics[-1]["name"] == "auth.outcomes.total"
    assert recorder.metrics[-1]["attributes"]["error_code"] == "invalid_credentials"
```

```python
def test_current_user_resolution_records_trace_attributes(...):
    response = client.get("/auth/me", headers=auth_headers())

    assert response.status_code == 200
    assert any(event.name == "auth_me_succeeded" for event in recorder.trace_events)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_auth_login.py tests/test_rbac.py tests/test_telemetry.py`

Expected: FAIL because auth flows still only log.

**Step 3: Write minimal implementation**

Add telemetry calls alongside the current auth logs for:

- `auth_login_succeeded`
- `auth_login_rejected`
- `auth_current_user_resolved`
- `auth_current_user_rejected`
- `auth_me_succeeded`

Use only existing stable attributes such as `status_code`, `error_code`, `failure_stage`, `user_id`, and `user_email` where already available. Keep `user_email` out of metrics if the metric labels would become too high-cardinality; use it only in traces or reporting metadata.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_auth_login.py tests/test_rbac.py tests/test_telemetry.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/api/routes/auth.py apps/api/src/noa_api/api/auth_dependencies.py apps/api/src/noa_api/core/telemetry.py apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py apps/api/tests/test_telemetry.py
git commit -m "feat(api): add auth telemetry mapping"
```

### Task 4: Instrument assistant failure telemetry

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/assistant_operations.py`
- Modify: `apps/api/src/noa_api/core/telemetry.py`
- Test: `apps/api/tests/test_assistant_operations.py`
- Test: `apps/api/tests/test_assistant.py`
- Test: `apps/api/tests/test_telemetry.py`

**Step 1: Write the failing test**

Add focused tests for the already-stabilized assistant failure events.

```python
async def test_run_agent_failure_records_trace_metric_and_reporting_candidate(...):
    await run_agent_phase(...)

    assert recorder.trace_events[-1].name == "assistant_run_failed_agent"
    assert recorder.metrics[-1]["name"] == "assistant.failures.total"
    assert recorder.reports[-1]["name"] == "assistant_run_failed_agent"
```

Add separate coverage for `assistant_error_message_persist_failed` and `assistant_state_refresh_failed`.

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_assistant_operations.py tests/test_assistant.py tests/test_telemetry.py`

Expected: FAIL because assistant failures are only logged today.

**Step 3: Write minimal implementation**

In `apps/api/src/noa_api/api/routes/assistant_operations.py`, add recorder calls next to the existing assistant failure logs:

- record trace events for all three assistant failure events
- increment assistant failure counters using bounded dimensions only
- report externally only the unexpected or degraded assistant failures identified in the design doc

Reuse only the current stable attributes: `assistant_command_types`, `thread_id`, `user_id`, `status_code`, `error_code`, and `error_type`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_assistant_operations.py tests/test_assistant.py tests/test_telemetry.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/src/noa_api/core/telemetry.py apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py apps/api/tests/test_telemetry.py
git commit -m "feat(api): add assistant telemetry mapping"
```

### Task 5: Instrument admin, threads, and WHM management telemetry

**Files:**
- Modify: `apps/api/src/noa_api/api/routes/admin.py`
- Modify: `apps/api/src/noa_api/api/routes/threads.py`
- Modify: `apps/api/src/noa_api/api/routes/whm_admin.py`
- Modify: `apps/api/src/noa_api/core/telemetry.py`
- Test: `apps/api/tests/test_threads.py`
- Test: `apps/api/tests/test_whm_admin_routes.py`
- Test: `apps/api/tests/test_rbac.py`
- Test: `apps/api/tests/test_telemetry.py`

**Step 1: Write the failing test**

Add route-level tests that verify success/conflict/not-found events now emit low-cardinality management metrics and trace decorations.

```python
def test_threads_list_records_management_metric(...):
    response = client.get("/threads", headers=auth_headers())

    assert response.status_code == 200
    assert recorder.metrics[-1]["name"] == "threads.outcomes.total"
```

```python
def test_whm_not_found_records_route_outcome_metric(...):
    response = client.delete(f"/whm-admin/{missing_id}", headers=admin_headers())

    assert response.status_code == 404
    assert recorder.metrics[-1]["attributes"]["error_code"] == "whm_server_not_found"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_threads.py tests/test_whm_admin_routes.py tests/test_rbac.py tests/test_telemetry.py`

Expected: FAIL because management routes do not emit telemetry yet.

**Step 3: Write minimal implementation**

Add telemetry calls beside the existing route logs in `admin.py`, `threads.py`, and `whm_admin.py`.

Rules:

- route outcomes become trace events or request-span attributes
- counters use bounded labels only
- do not include `user_id`, `thread_id`, `server_id`, `server_name`, or `target_user_id` as metric labels
- keep entity IDs in trace/report metadata only when they already exist in the route context

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_threads.py tests/test_whm_admin_routes.py tests/test_rbac.py tests/test_telemetry.py`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/api/routes/admin.py apps/api/src/noa_api/api/routes/threads.py apps/api/src/noa_api/api/routes/whm_admin.py apps/api/src/noa_api/core/telemetry.py apps/api/tests/test_threads.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_rbac.py apps/api/tests/test_telemetry.py
git commit -m "feat(api): add management telemetry mapping"
```

### Task 6: Run full verification and refresh docs handoff

**Files:**
- Review: `docs/plans/2026-03-15-backend-telemetry-mapping-design.md`
- Modify: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`
- Test: `apps/api/tests/test_telemetry.py`

**Step 1: Run focused verification**

Run:

```bash
uv run pytest -q tests/test_telemetry.py tests/test_request_context.py tests/test_auth_login.py tests/test_rbac.py tests/test_threads.py tests/test_whm_admin_routes.py tests/test_assistant_operations.py tests/test_assistant.py
```

Expected: PASS with the telemetry-focused suites green.

**Step 2: Run full backend verification**

Run:

```bash
uv run pytest -q
uv run ruff check src tests
```

Expected: full backend suite passes and Ruff reports `All checks passed!`.

**Step 3: Refresh the audit handoff after implementation**

Update `docs/reports/2026-03-14-error-handling-and-logging-audit.md` so it records:

- the backend telemetry mapping implementation pass that was completed
- the verification commands and results
- the next deferred work, if any, after vendor/export decisions are still outstanding

**Step 4: Review the final diff**

Run:

```bash
git diff -- apps/api/src/noa_api/core/telemetry.py apps/api/src/noa_api/api/error_handling.py apps/api/src/noa_api/api/routes/auth.py apps/api/src/noa_api/api/auth_dependencies.py apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/src/noa_api/api/routes/admin.py apps/api/src/noa_api/api/routes/threads.py apps/api/src/noa_api/api/routes/whm_admin.py apps/api/tests/test_telemetry.py apps/api/tests/test_request_context.py apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py apps/api/tests/test_threads.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py docs/reports/2026-03-14-error-handling-and-logging-audit.md
```

Expected: diff is limited to the telemetry seam, mapped backend flows, focused tests, and the audit refresh.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/telemetry.py apps/api/src/noa_api/api/error_handling.py apps/api/src/noa_api/api/routes/auth.py apps/api/src/noa_api/api/auth_dependencies.py apps/api/src/noa_api/api/routes/assistant_operations.py apps/api/src/noa_api/api/routes/admin.py apps/api/src/noa_api/api/routes/threads.py apps/api/src/noa_api/api/routes/whm_admin.py apps/api/tests/test_telemetry.py apps/api/tests/test_request_context.py apps/api/tests/test_auth_login.py apps/api/tests/test_rbac.py apps/api/tests/test_threads.py apps/api/tests/test_whm_admin_routes.py apps/api/tests/test_assistant_operations.py apps/api/tests/test_assistant.py docs/reports/2026-03-14-error-handling-and-logging-audit.md
git commit -m "feat(api): map backend telemetry signals"
```
