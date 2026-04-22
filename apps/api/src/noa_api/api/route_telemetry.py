"""Shared telemetry helpers for API route modules."""

from __future__ import annotations

import logging

from fastapi import Request

from noa_api.core.telemetry import TelemetryEvent, get_telemetry_recorder

logger = logging.getLogger(__name__)


def status_family(status_code: int) -> str:
    return f"{status_code // 100}xx"


def safe_trace(request: Request, event: TelemetryEvent) -> None:
    try:
        get_telemetry_recorder(request.app).trace(event)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "trace",
                "telemetry_event": event.name,
            },
        )


def safe_metric(request: Request, event: TelemetryEvent, *, value: int | float) -> None:
    try:
        get_telemetry_recorder(request.app).metric(event, value=value)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "metric",
                "telemetry_event": event.name,
            },
        )


def record_route_outcome(
    request: Request,
    *,
    metric_name: str,
    event_name: str,
    status_code: int,
    trace_attributes: dict[str, str | int | bool],
    error_code: str | None = None,
) -> None:
    event_attributes = dict(trace_attributes)
    if error_code is not None:
        event_attributes["error_code"] = error_code
        event_attributes["status_code"] = status_code

    safe_trace(
        request,
        TelemetryEvent(name=event_name, attributes=event_attributes),
    )

    metric_attributes: dict[str, str] = {
        "event_name": event_name,
        "status_family": status_family(status_code),
    }
    if error_code is not None:
        metric_attributes["error_code"] = error_code

    safe_metric(
        request,
        TelemetryEvent(name=metric_name, attributes=metric_attributes),
        value=1,
    )
