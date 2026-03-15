from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from noa_api.api.error_handling import install_error_handling
from noa_api.core.config import Settings
from noa_api.main import create_app
from noa_api.core.telemetry import (
    NoOpTelemetryRecorder,
    TelemetryEvent,
    get_telemetry_recorder,
)


def _settings(**kwargs: object) -> Settings:
    return Settings(**kwargs, _env_file=None)  # type: ignore[call-arg]


class RecordingTelemetryRecorder:
    def __init__(self) -> None:
        self.trace_events: list[TelemetryEvent] = []
        self.metric_events: list[tuple[TelemetryEvent, int | float]] = []
        self.report_events: list[tuple[TelemetryEvent, str | None]] = []

    def trace(self, event: TelemetryEvent) -> None:
        self.trace_events.append(event)

    def metric(self, event: TelemetryEvent, *, value: int | float) -> None:
        self.metric_events.append((event, value))

    def report(self, event: TelemetryEvent, *, detail: str | None = None) -> None:
        self.report_events.append((event, detail))


class ReportFailingTelemetryRecorder(RecordingTelemetryRecorder):
    def report(self, event: TelemetryEvent, *, detail: str | None = None) -> None:
        raise RuntimeError("report boom")


def test_telemetry_settings_defaults() -> None:
    settings = _settings(environment="test")

    assert settings.telemetry_enabled is False
    assert settings.telemetry_service_name == "noa-api"
    assert settings.telemetry_otlp_endpoint is None
    assert settings.telemetry_otlp_headers == {}
    assert settings.telemetry_traces_enabled is True
    assert settings.telemetry_metrics_enabled is True


def test_telemetry_settings_parse_otlp_headers() -> None:
    settings = _settings(
        environment="test",
        telemetry_otlp_headers="authorization=Bearer token, x-tenant-id = tenant-123",
    )

    assert settings.telemetry_otlp_headers == {
        "authorization": "Bearer token",
        "x-tenant-id": "tenant-123",
    }


def test_create_app_exposes_app_scoped_noop_telemetry_recorder() -> None:
    app = create_app()
    other_app = create_app()
    recorder = get_telemetry_recorder(app)
    other_recorder = get_telemetry_recorder(other_app)

    assert isinstance(recorder, NoOpTelemetryRecorder)
    assert app.state.telemetry is recorder
    assert isinstance(other_recorder, NoOpTelemetryRecorder)
    assert other_app.state.telemetry is other_recorder
    assert other_recorder is not recorder

    event = TelemetryEvent(
        name="assistant.request.started",
        attributes={"request_id": "req-123", "route": "/health"},
    )

    assert recorder.trace(event) is None
    assert recorder.metric(event, value=1) is None
    assert recorder.report(event, detail="healthy") is None


def test_telemetry_event_attributes_are_immutable_after_creation() -> None:
    event = TelemetryEvent(
        name="assistant.request.started",
        attributes={"request_id": "req-123"},
    )

    with pytest.raises(TypeError):
        event.attributes["route"] = "/health"


def test_get_telemetry_recorder_falls_back_to_noop_for_bare_fastapi_app() -> None:
    app = FastAPI()

    recorder = get_telemetry_recorder(app)

    assert isinstance(recorder, NoOpTelemetryRecorder)


async def test_install_error_handling_app_uses_noop_telemetry_without_error_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    install_error_handling(app)
    caplog.set_level(logging.ERROR, logger="noa_api.api.error_handling")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")
    assert not any(
        record.getMessage() == "api_telemetry_failed" for record in caplog.records
    )


async def test_unhandled_exception_records_reporting_candidate() -> None:
    app = create_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder

    @app.get("/_tests/error")
    async def error_route() -> Response:
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_tests/error")

    assert response.status_code == 500

    report_event, report_detail = recorder.report_events[-1]
    assert report_event.name == "api_unhandled_exception"
    assert report_event.attributes == {
        "error_type": "RuntimeError",
        "request_method": "GET",
        "request_path": "/_tests/error",
    }
    assert report_detail is None


async def test_unhandled_exception_tolerates_reporting_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()
    recorder = ReportFailingTelemetryRecorder()
    app.state.telemetry = recorder

    @app.get("/_tests/error")
    async def error_route() -> Response:
        raise RuntimeError("boom")

    caplog.set_level(logging.ERROR, logger="noa_api.api.error_handling")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_tests/error")

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert response.json() == {
        "detail": "Internal server error",
        "error_code": "internal_server_error",
        "request_id": request_id,
    }

    assert any(
        record.getMessage() == "api_unhandled_exception" for record in caplog.records
    )
    failure_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "api_telemetry_failed"
    )
    assert getattr(failure_record, "telemetry_operation") == "report"
    assert getattr(failure_record, "telemetry_event") == "api_unhandled_exception"
    assert "report boom" in caplog.text
