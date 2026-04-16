from __future__ import annotations

import io
import json
import logging

import pytest
import structlog
from fastapi import Response
from httpx import ASGITransport, AsyncClient

from noa_api.core.logging import configure_logging
from noa_api.core.logging_context import log_context
from noa_api.core.telemetry import TelemetryEvent


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


class TraceFailingTelemetryRecorder(RecordingTelemetryRecorder):
    def trace(self, event: TelemetryEvent) -> None:
        raise RuntimeError("trace boom")


async def test_health_includes_x_request_id_header(create_test_app) -> None:
    app = create_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")


async def test_inbound_x_request_id_is_preserved(create_test_app) -> None:
    app = create_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health", headers={"x-request-id": "req-from-client"}
        )

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "req-from-client"


async def test_request_completion_records_request_lifecycle_trace_and_metrics(
    create_test_app,
) -> None:
    app = create_test_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200

    trace_event = recorder.trace_events[-1]
    assert trace_event.name == "api_request_completed"
    assert trace_event.attributes["request_method"] == "GET"
    assert trace_event.attributes["request_path"] == "/health"
    assert trace_event.attributes["status_code"] == 200
    assert trace_event.attributes["request_id"] == response.headers["x-request-id"]
    assert isinstance(trace_event.attributes["duration_ms"], int)
    assert trace_event.attributes["duration_ms"] >= 0

    request_total_event, request_total_value = recorder.metric_events[0]
    assert request_total_event.name == "api.requests.total"
    assert request_total_event.attributes == {
        "request_method": "GET",
        "request_path": "/health",
        "status_code": 200,
    }
    assert request_total_value == 1

    request_duration_event, request_duration_value = recorder.metric_events[1]
    assert request_duration_event.name == "api.request.duration_ms"
    assert request_duration_event.attributes == {
        "request_method": "GET",
        "request_path": "/health",
        "status_code": 200,
    }
    assert request_duration_value == trace_event.attributes["duration_ms"]
    assert recorder.report_events == []


async def test_request_completion_metrics_use_normalized_route_template(
    create_test_app,
) -> None:
    app = create_test_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder

    @app.get("/_tests/items/{item_id}")
    async def read_item(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_tests/items/123")

    assert response.status_code == 200

    trace_event = recorder.trace_events[-1]
    assert trace_event.attributes["request_path"] == "/_tests/items/123"

    request_total_event, _ = recorder.metric_events[0]
    assert request_total_event.attributes["request_path"] == "/_tests/items/{item_id}"

    request_duration_event, _ = recorder.metric_events[1]
    assert (
        request_duration_event.attributes["request_path"] == "/_tests/items/{item_id}"
    )


async def test_request_completion_metrics_use_bounded_fallback_for_unmatched_route(
    create_test_app,
) -> None:
    app = create_test_app()
    recorder = RecordingTelemetryRecorder()
    app.state.telemetry = recorder

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_tests/missing/123")

    assert response.status_code == 404

    trace_event = recorder.trace_events[-1]
    assert trace_event.attributes["request_path"] == "/_tests/missing/123"

    request_total_event, _ = recorder.metric_events[0]
    assert request_total_event.attributes["request_path"] == "unmatched"

    request_duration_event, _ = recorder.metric_events[1]
    assert request_duration_event.attributes["request_path"] == "unmatched"


async def test_request_completion_tolerates_telemetry_trace_failure(
    create_test_app,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_test_app()
    recorder = TraceFailingTelemetryRecorder()
    app.state.telemetry = recorder

    caplog.set_level(logging.ERROR, logger="noa_api.api.error_handling")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers.get("x-request-id")
    assert len(recorder.metric_events) == 2

    failure_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "api_telemetry_failed"
    )
    assert getattr(failure_record, "telemetry_operation") == "trace"
    assert getattr(failure_record, "telemetry_event") == "api_request_completed"
    assert "trace boom" in caplog.text


async def test_uncaught_exception_returns_safe_500_envelope_with_request_id(
    create_test_app,
) -> None:
    app = create_test_app()

    @app.get("/_tests/error")
    async def error_route() -> Response:
        raise RuntimeError("boom")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_tests/error")

    assert response.status_code == 500
    request_id = response.headers.get("x-request-id")
    assert request_id
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": "Internal server error",
        "error_code": "internal_server_error",
        "request_id": request_id,
    }


def test_create_app_preserves_existing_root_logging_configuration(
    create_test_app,
) -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_formatters = {
        id(handler): handler.formatter for handler in original_handlers
    }
    sentinel = logging.StreamHandler()
    sentinel.setLevel(logging.ERROR)
    sentinel_formatter = logging.Formatter("sentinel %(message)s")
    sentinel.setFormatter(sentinel_formatter)
    root_logger.handlers = [sentinel]
    root_logger.setLevel(logging.WARNING)

    try:
        create_test_app()
        create_test_app()

        assert root_logger.handlers == [sentinel]
        assert root_logger.level == logging.WARNING
        assert isinstance(sentinel.formatter, structlog.stdlib.ProcessorFormatter)
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        for handler in original_handlers:
            handler.setFormatter(original_formatters[id(handler)])


def test_configure_logging_formats_existing_root_handlers_with_structlog() -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_formatters = {
        id(handler): handler.formatter for handler in original_handlers
    }
    stream = io.StringIO()
    sentinel = logging.StreamHandler(stream)
    sentinel.setFormatter(logging.Formatter("sentinel %(message)s"))
    root_logger.handlers = [sentinel]
    root_logger.setLevel(logging.WARNING)

    try:
        configure_logging()

        with log_context(request_method="GET", request_path="/health"):
            logging.getLogger("tests.logging").warning(
                "api_request_completed",
                extra={"status_code": 200},
            )

        assert root_logger.handlers == [sentinel]
        assert root_logger.level == logging.WARNING
        assert isinstance(sentinel.formatter, structlog.stdlib.ProcessorFormatter)

        rendered = stream.getvalue().strip()
        payload = json.loads(rendered)
        assert payload["event"] == "api_request_completed"
        assert payload["request_method"] == "GET"
        assert payload["request_path"] == "/health"
        assert payload["status_code"] == 200
    finally:
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        for handler in original_handlers:
            handler.setFormatter(original_formatters[id(handler)])
