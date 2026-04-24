"""Shared telemetry helpers for API route modules.

Single implementation of safe_trace / safe_metric / safe_report (V49).
Accepts Request, FastAPI app, or TelemetryRecorder|None as source.
"""

from __future__ import annotations

import logging
from typing import Union

from fastapi import FastAPI, Request

from noa_api.core.telemetry import (
    TelemetryEvent,
    TelemetryRecorder,
    get_telemetry_recorder,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Telemetry source resolution
# ---------------------------------------------------------------------------

TelemetrySource = Union[Request, FastAPI, TelemetryRecorder, None]


def _resolve_recorder(source: TelemetrySource) -> TelemetryRecorder | None:
    if source is None:
        return None
    if isinstance(source, Request):
        return get_telemetry_recorder(source.app)  # type: ignore[arg-type]
    if isinstance(source, FastAPI):
        return get_telemetry_recorder(source)  # type: ignore[arg-type]
    # Already a TelemetryRecorder
    return source  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# status_family
# ---------------------------------------------------------------------------


def status_family(status_code: int) -> str:
    return f"{status_code // 100}xx"


# ---------------------------------------------------------------------------
# safe_trace / safe_metric / safe_report
# ---------------------------------------------------------------------------


def safe_trace(source: TelemetrySource, event: TelemetryEvent) -> None:
    recorder = _resolve_recorder(source)
    if recorder is None:
        return
    try:
        recorder.trace(event)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "trace",
                "telemetry_event": event.name,
            },
        )


def safe_metric(
    source: TelemetrySource, event: TelemetryEvent, *, value: int | float
) -> None:
    recorder = _resolve_recorder(source)
    if recorder is None:
        return
    try:
        recorder.metric(event, value=value)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "metric",
                "telemetry_event": event.name,
            },
        )


def safe_report(source: TelemetrySource, event: TelemetryEvent) -> None:
    recorder = _resolve_recorder(source)
    if recorder is None:
        return
    try:
        recorder.report(event)
    except Exception:
        logger.exception(
            "api_telemetry_failed",
            extra={
                "telemetry_operation": "report",
                "telemetry_event": event.name,
            },
        )


# ---------------------------------------------------------------------------
# record_route_outcome — convenience for route-level trace + metric
# ---------------------------------------------------------------------------


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
