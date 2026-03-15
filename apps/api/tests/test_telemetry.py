from __future__ import annotations

import importlib
import inspect
import logging

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from noa_api.api.error_handling import install_error_handling
from noa_api.core.config import Settings
from noa_api.main import create_app
from noa_api.core.telemetry import (
    NoOpTelemetryRecorder,
    TelemetryEvent,
    create_telemetry_recorder,
    get_telemetry_recorder,
)


def _settings(**kwargs: object) -> Settings:
    return Settings(**kwargs, _env_file=None)  # type: ignore[call-arg]


def _telemetry_otel_module():
    try:
        return importlib.import_module("noa_api.core.telemetry_opentelemetry")
    except ModuleNotFoundError:
        pytest.fail("telemetry_opentelemetry module missing")


def _create_telemetry_recorder_for_test(settings: Settings):
    if inspect.signature(create_telemetry_recorder).parameters:
        return create_telemetry_recorder(settings)
    return create_telemetry_recorder()


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


class ExportFailingSpan:
    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        raise RuntimeError("otel export boom")


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


def test_telemetry_settings_parse_otlp_headers_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv(
        "TELEMETRY_OTLP_HEADERS",
        "authorization=Bearer token, x-tenant-id = tenant-123",
    )

    settings = Settings(_env_file=None)

    assert settings.telemetry_otlp_headers == {
        "authorization": "Bearer token",
        "x-tenant-id": "tenant-123",
    }


def test_telemetry_settings_ignore_malformed_otlp_headers() -> None:
    settings = _settings(
        environment="test",
        telemetry_otlp_headers=(
            "authorization=Bearer token, malformed, =missing-name, x-tenant-id=tenant-123"
        ),
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


def test_create_telemetry_recorder_accepts_settings() -> None:
    assert "app_settings" in inspect.signature(create_telemetry_recorder).parameters


def test_create_telemetry_recorder_returns_noop_when_disabled() -> None:
    recorder = _create_telemetry_recorder_for_test(_settings(environment="test"))

    assert isinstance(recorder, NoOpTelemetryRecorder)


def test_create_telemetry_recorder_returns_otel_recorder_when_enabled() -> None:
    recorder = _create_telemetry_recorder_for_test(
        _settings(
            environment="test",
            telemetry_enabled=True,
            telemetry_otlp_endpoint="http://collector:4318",
        )
    )

    assert recorder.__class__.__name__ == "OpenTelemetryRecorder"


def test_create_telemetry_recorder_falls_back_to_noop_when_enabled_without_endpoint(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="noa_api.core.telemetry_opentelemetry")

    recorder = _create_telemetry_recorder_for_test(
        _settings(environment="test", telemetry_enabled=True)
    )

    assert isinstance(recorder, NoOpTelemetryRecorder)
    warning_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "telemetry_falling_back_to_noop"
    )
    assert getattr(warning_record, "reason") == "missing_otlp_endpoint"


def test_create_telemetry_recorder_falls_back_to_noop_when_exporter_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = _telemetry_otel_module()
    caplog.set_level(logging.WARNING, logger="noa_api.core.telemetry_opentelemetry")

    def fail_exporter(*args: object, **kwargs: object) -> None:
        raise RuntimeError("span exporter boom")

    monkeypatch.setattr(module, "OTLPSpanExporter", fail_exporter)

    recorder = _create_telemetry_recorder_for_test(
        _settings(
            environment="test",
            telemetry_enabled=True,
            telemetry_otlp_endpoint="http://collector:4318",
        )
    )

    assert isinstance(recorder, NoOpTelemetryRecorder)
    warning_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "telemetry_falling_back_to_noop"
    )
    assert getattr(warning_record, "reason") == "exporter_setup_failed"
    assert "span exporter boom" in caplog.text


def test_create_app_passes_settings_into_telemetry_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_settings = _settings(
        environment="test",
        telemetry_enabled=True,
        telemetry_otlp_endpoint="http://collector:4318",
    )
    fake_recorder = object()
    calls: list[Settings] = []

    def fake_create_telemetry_recorder(settings: Settings) -> object:
        calls.append(settings)
        return fake_recorder

    monkeypatch.setattr(
        "noa_api.main.create_telemetry_recorder",
        fake_create_telemetry_recorder,
    )

    app = create_app(app_settings)

    assert calls == [app_settings]
    assert app.state.telemetry is fake_recorder


def test_metric_labels_drop_high_cardinality_fields() -> None:
    module = _telemetry_otel_module()
    filter_metric_attributes = getattr(module, "_metric_attributes_for_event", None)

    assert callable(filter_metric_attributes)

    event = TelemetryEvent(
        name="assistant_failures_total",
        attributes={
            "error_code": "assistant_failed",
            "thread_id": "thread-123",
            "user_id": "user-456",
        },
    )

    assert filter_metric_attributes(event) == {"error_code": "assistant_failed"}


def test_bounded_metric_labels_survive_filtering() -> None:
    module = _telemetry_otel_module()
    filter_metric_attributes = getattr(module, "_metric_attributes_for_event", None)

    assert callable(filter_metric_attributes)

    event = TelemetryEvent(
        name="assistant_failures_total",
        attributes={
            "assistant_command_types": "message,tool",
            "validation_ok": True,
            "thread_id": "thread-123",
            "user_id": "user-456",
        },
    )

    assert filter_metric_attributes(event) == {
        "assistant_command_types": "message,tool",
        "validation_ok": True,
    }


def test_telemetry_recorder_trace_adds_event_to_active_span() -> None:
    module = _telemetry_otel_module()
    recorder_class = getattr(module, "OpenTelemetryRecorder", None)

    assert recorder_class is not None

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    tracer = tracer_provider.get_tracer("tests.telemetry")
    recorder = recorder_class(
        tracer=tracer,
        meter=MeterProvider().get_meter("tests.telemetry"),
    )
    event = TelemetryEvent(
        name="assistant.request.started",
        attributes={"request_id": "req-123", "route": "/health"},
    )

    with tracer.start_as_current_span("request"):
        recorder.trace(event)

    finished_span = span_exporter.get_finished_spans()[0]

    assert finished_span.events[0].name == "assistant.request.started"
    assert finished_span.events[0].attributes == {
        "request_id": "req-123",
        "route": "/health",
    }


def test_telemetry_recorder_report_marks_span_error() -> None:
    module = _telemetry_otel_module()
    recorder_class = getattr(module, "OpenTelemetryRecorder", None)

    assert recorder_class is not None

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    tracer = tracer_provider.get_tracer("tests.telemetry")
    recorder = recorder_class(
        tracer=tracer,
        meter=MeterProvider().get_meter("tests.telemetry"),
    )
    event = TelemetryEvent(
        name="api_unhandled_exception",
        attributes={"error_type": "RuntimeError", "request_method": "GET"},
    )

    with tracer.start_as_current_span("request"):
        recorder.report(event, detail="boom")

    finished_span = span_exporter.get_finished_spans()[0]
    report_event = finished_span.events[0]

    assert finished_span.status.status_code is StatusCode.ERROR
    assert report_event.name == "api_unhandled_exception"
    assert report_event.attributes == {
        "error_type": "RuntimeError",
        "request_method": "GET",
        "detail": "boom",
        "telemetry.kind": "report",
    }


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


async def test_request_still_succeeds_when_otel_export_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()
    module = _telemetry_otel_module()
    recorder_class = getattr(module, "OpenTelemetryRecorder", None)

    assert recorder_class is not None

    monkeypatch.setattr(module, "_active_span", lambda: ExportFailingSpan())
    app.state.telemetry = recorder_class(tracer=None, meter=None)

    @app.get("/_tests/ok")
    async def ok_route() -> dict[str, str]:
        return {"status": "ok"}

    caplog.set_level(logging.ERROR, logger="noa_api.api.error_handling")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_tests/ok")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    failure_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "api_telemetry_failed"
    )
    assert getattr(failure_record, "telemetry_operation") == "trace"
    assert getattr(failure_record, "telemetry_event") == "api_request_completed"
    assert "otel export boom" in caplog.text
