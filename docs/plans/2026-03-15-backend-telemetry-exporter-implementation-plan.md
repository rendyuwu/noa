# Backend Telemetry Exporter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire the existing backend telemetry seam to an OpenTelemetry-backed exporter path without changing the stabilized backend event vocabulary.

**Architecture:** Keep `apps/api/src/noa_api/core/telemetry.py` as the only app-facing telemetry boundary, add explicit telemetry settings in `apps/api/src/noa_api/core/config.py`, and translate the existing `trace`, `metric`, and `report` calls to OpenTelemetry in one central implementation. Preserve the current no-op default, bounded metric labels, and best-effort failure handling so route modules and HTTP contracts stay unchanged.

**Tech Stack:** FastAPI, Python 3.11, OpenTelemetry API/SDK/OTLP HTTP exporter, pytest, uv

**Status:** Implemented on `feat/backend-telemetry-exporter`. This plan is now the execution record for the completed exporter slice; use the refreshed audit for the next-step handoff. Dashboards, alerts, and frontend error reporting remain undone, and any later backend-only code follow-up should stay in helper/service logging cleanup or shared/helper-level `error_code` expansion rather than reopening the completed route/exporter slice.

---

### Task 1: Add telemetry dependencies and settings

**Files:**
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/src/noa_api/core/config.py`
- Test: `apps/api/tests/test_telemetry.py`

**Step 1: Write the failing test**

Add settings-focused coverage that describes the intended defaults and parsing behavior.

```python
from noa_api.core.config import Settings


def test_settings_disable_telemetry_by_default() -> None:
    settings = Settings()

    assert settings.telemetry_enabled is False
    assert settings.telemetry_service_name == "noa-api"
    assert settings.telemetry_otlp_endpoint is None


def test_settings_parse_telemetry_headers_from_json_dict() -> None:
    settings = Settings.model_validate(
        {
            "telemetry_enabled": True,
            "telemetry_otlp_endpoint": "http://collector:4318",
            "telemetry_otlp_headers": {"Authorization": "Bearer token"},
        }
    )

    assert settings.telemetry_otlp_headers == {"Authorization": "Bearer token"}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_telemetry.py -k telemetry_settings`

Expected: FAIL because the telemetry settings do not exist yet.

**Step 3: Write minimal implementation**

Update `apps/api/pyproject.toml` to add the smallest OpenTelemetry package set needed for traces and metrics:

```toml
dependencies = [
  "opentelemetry-api>=1.30.0",
  "opentelemetry-sdk>=1.30.0",
  "opentelemetry-exporter-otlp-proto-http>=1.30.0",
]
```

Add these fields to `apps/api/src/noa_api/core/config.py`:

```python
telemetry_enabled: bool = False
telemetry_service_name: str = "noa-api"
telemetry_otlp_endpoint: str | None = None
telemetry_otlp_headers: dict[str, str] = Field(default_factory=dict)
telemetry_traces_enabled: bool = True
telemetry_metrics_enabled: bool = True
```

Keep the defaults no-op-safe.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_telemetry.py -k telemetry_settings`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/pyproject.toml apps/api/src/noa_api/core/config.py apps/api/tests/test_telemetry.py
git commit -m "feat(api): add telemetry exporter settings"
```

### Task 2: Build the OpenTelemetry recorder implementation

**Files:**
- Modify: `apps/api/src/noa_api/core/telemetry.py`
- Create: `apps/api/src/noa_api/core/telemetry_opentelemetry.py`
- Test: `apps/api/tests/test_telemetry.py`

**Step 1: Write the failing test**

Add focused unit tests for factory selection and event translation.

```python
from noa_api.core.config import Settings
from noa_api.core.telemetry import TelemetryEvent, create_telemetry_recorder


def test_create_telemetry_recorder_returns_noop_when_disabled() -> None:
    recorder = create_telemetry_recorder(Settings())

    assert recorder.__class__.__name__ == "NoOpTelemetryRecorder"


def test_create_telemetry_recorder_returns_otel_recorder_when_enabled() -> None:
    settings = Settings.model_validate(
        {
            "telemetry_enabled": True,
            "telemetry_otlp_endpoint": "http://collector:4318",
        }
    )

    recorder = create_telemetry_recorder(settings)

    assert recorder.__class__.__name__ == "OpenTelemetryRecorder"
```

Add metric filtering coverage:

```python
def test_metric_labels_drop_high_cardinality_fields() -> None:
    event = TelemetryEvent(
        name="assistant_failures_total",
        attributes={
            "error_code": "assistant_failed",
            "thread_id": "thread-123",
            "user_id": "user-456",
        },
    )

    filtered = _metric_attributes_for_event(event)

    assert filtered == {"error_code": "assistant_failed"}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_telemetry.py -k "telemetry_recorder or metric_labels"`

Expected: FAIL because there is no OpenTelemetry-backed recorder or metric filtering helper yet.

**Step 3: Write minimal implementation**

Create `apps/api/src/noa_api/core/telemetry_opentelemetry.py` with:

```python
class OpenTelemetryRecorder:
    def trace(self, event: TelemetryEvent) -> None:
        ...

    def metric(self, event: TelemetryEvent, *, value: int | float) -> None:
        ...

    def report(self, event: TelemetryEvent, *, detail: str | None = None) -> None:
        ...
```

Implement central helpers for:

- provider/exporter setup
- metric-attribute filtering
- adding span attributes/events from the stable event payload
- recording selective `report(...)` signals as high-severity span events or exception-style telemetry

Update `apps/api/src/noa_api/core/telemetry.py` so `create_telemetry_recorder(...)` accepts settings and returns either `NoOpTelemetryRecorder` or `OpenTelemetryRecorder`.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_telemetry.py -k "telemetry_recorder or metric_labels"`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/telemetry.py apps/api/src/noa_api/core/telemetry_opentelemetry.py apps/api/tests/test_telemetry.py
git commit -m "feat(api): add opentelemetry telemetry recorder"
```

### Task 3: Wire app startup to the configured recorder

**Files:**
- Modify: `apps/api/src/noa_api/main.py`
- Modify: `apps/api/tests/test_telemetry.py`

**Step 1: Write the failing test**

Add create-app coverage that verifies startup passes settings into the recorder factory.

```python
from noa_api.core.config import Settings
from noa_api.main import create_app


def test_create_app_uses_configured_telemetry_recorder() -> None:
    settings = Settings.model_validate(
        {
            "telemetry_enabled": True,
            "telemetry_otlp_endpoint": "http://collector:4318",
        }
    )

    app = create_app(settings)

    assert app.state.telemetry.__class__.__name__ == "OpenTelemetryRecorder"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_telemetry.py -k create_app_uses_configured_telemetry_recorder`

Expected: FAIL because `create_app(...)` still calls `create_telemetry_recorder()` without settings.

**Step 3: Write minimal implementation**

Update `apps/api/src/noa_api/main.py`:

```python
def create_app(app_settings: Settings = settings) -> FastAPI:
    configure_logging()
    app = FastAPI(title="Project NOA API")
    app.state.telemetry = create_telemetry_recorder(app_settings)
```

Keep the fallback behavior unchanged when telemetry is disabled.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_telemetry.py -k create_app_uses_configured_telemetry_recorder`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/main.py apps/api/tests/test_telemetry.py
git commit -m "feat(api): wire configured telemetry recorder"
```

### Task 4: Prove telemetry failure handling stays best-effort

**Files:**
- Modify: `apps/api/tests/test_telemetry.py`
- Modify: `apps/api/src/noa_api/core/telemetry_opentelemetry.py`

**Step 1: Write the failing test**

Add failure-mode coverage for exporter setup and runtime failures.

```python
def test_enabled_telemetry_with_missing_endpoint_falls_back_to_noop(caplog) -> None:
    settings = Settings.model_validate({"telemetry_enabled": True})

    recorder = create_telemetry_recorder(settings)

    assert recorder.__class__.__name__ == "NoOpTelemetryRecorder"
    assert "telemetry_exporter_not_configured" in caplog.text
```

```python
async def test_request_still_succeeds_when_otel_export_raises(...) -> None:
    app = create_app(configured_settings)
    app.state.telemetry = RuntimeFailingTelemetryRecorder()

    response = await client.get("/health")

    assert response.status_code == 200
    assert "api_telemetry_failed" in caplog.text
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_telemetry.py -k "falls_back_to_noop or otel_export_raises"`

Expected: FAIL because the OpenTelemetry setup and runtime failure paths are not implemented yet.

**Step 3: Write minimal implementation**

Add safe fallback logic to the OpenTelemetry recorder factory and provider setup:

- missing endpoint or invalid config -> warn and return no-op
- exporter construction failure -> warn and return no-op
- runtime record failure inside recorder methods -> let existing route-level safe wrappers log `api_telemetry_failed`

Do not change request responses or existing log event names.

**Step 4: Run test to verify it passes**

Run: `uv run pytest -q tests/test_telemetry.py -k "falls_back_to_noop or otel_export_raises"`

Expected: PASS.

**Step 5: Commit**

```bash
git add apps/api/src/noa_api/core/telemetry_opentelemetry.py apps/api/tests/test_telemetry.py
git commit -m "fix(api): harden telemetry exporter fallback"
```

### Task 5: Run focused verification and refresh handoff docs

**Files:**
- Modify: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`
- Modify: `docs/plans/2026-03-15-backend-telemetry-exporter-design.md`
- Modify: `docs/plans/2026-03-15-backend-telemetry-exporter-implementation-plan.md`

**Step 1: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_telemetry.py tests/test_request_context.py tests/test_auth_login.py tests/test_rbac.py tests/test_threads.py tests/test_whm_admin_routes.py tests/test_assistant_operations.py tests/test_assistant.py
```

Expected: PASS with the telemetry and route regression suite green.

**Step 2: Run full backend tests**

Run: `uv run pytest -q`

Expected: PASS.

**Step 3: Run lint**

Run: `uv run ruff check src tests`

Expected: `All checks passed!`

**Step 4: Refresh handoff docs**

Update the audit to record:

- exporter wiring completed or intentionally deferred based on the implementation result
- dashboards/alerts/frontend reporting still deferred if unchanged
- future backend follow-up remains helper/service logging cleanup or shared/helper-level `error_code` expansion rather than route-slice reopening

**Step 5: Commit**

```bash
git add docs/reports/2026-03-14-error-handling-and-logging-audit.md docs/plans/2026-03-15-backend-telemetry-exporter-design.md docs/plans/2026-03-15-backend-telemetry-exporter-implementation-plan.md
git commit -m "docs: record telemetry exporter follow-up"
```
