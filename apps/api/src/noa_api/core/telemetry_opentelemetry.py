from __future__ import annotations

import logging
from collections.abc import Mapping

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from noa_api.core.config import Settings
from noa_api.core.telemetry import (
    NoOpTelemetryRecorder,
    TelemetryAttributeValue,
    TelemetryEvent,
    TelemetryRecorder,
)

logger = logging.getLogger(__name__)

_METRIC_ATTRIBUTE_KEYS = {
    "assistant_command_types",
    "command_type",
    "command_types",
    "error_code",
    "error_type",
    "event_name",
    "failure_stage",
    "request_method",
    "request_path",
    "route",
    "status_code",
    "status_family",
    "validation_ok",
}


class OpenTelemetryRecorder:
    def __init__(self, *, tracer: Tracer | None, meter: Meter | None) -> None:
        self._tracer = tracer
        self._meter = meter
        self._counters: dict[str, object] = {}
        self._histograms: dict[str, object] = {}

    def trace(self, event: TelemetryEvent) -> None:
        span = _active_span()
        attributes = _event_attributes(event)

        if span is not None:
            span.add_event(event.name, attributes=attributes)
            return

        if self._tracer is None:
            return

        with self._tracer.start_as_current_span(event.name, attributes=attributes):
            return None

    def metric(self, event: TelemetryEvent, *, value: int | float) -> None:
        if self._meter is None:
            return

        attributes = _metric_attributes_for_event(event)
        if event.name.endswith(".total") or event.name.endswith("_total"):
            counter = self._counters.get(event.name)
            if counter is None:
                counter = self._meter.create_counter(event.name)
                self._counters[event.name] = counter
            counter.add(value, attributes=attributes)
            return

        histogram = self._histograms.get(event.name)
        if histogram is None:
            histogram = self._meter.create_histogram(event.name)
            self._histograms[event.name] = histogram
        histogram.record(value, attributes=attributes)

    def report(self, event: TelemetryEvent, *, detail: str | None = None) -> None:
        attributes = _event_attributes(
            event,
            extra_attributes={
                "detail": detail,
                "telemetry.kind": "report",
            },
        )
        span = _active_span()

        if span is not None:
            span.set_status(Status(StatusCode.ERROR, description=detail))
            span.add_event(event.name, attributes=attributes)
            return

        if self._tracer is None:
            return

        with self._tracer.start_as_current_span(
            event.name, attributes=attributes
        ) as span:
            span.set_status(Status(StatusCode.ERROR, description=detail))
            span.add_event(event.name, attributes=attributes)


def create_open_telemetry_recorder(app_settings: Settings) -> TelemetryRecorder:
    resource = Resource.create({"service.name": app_settings.telemetry_service_name})
    signals_enabled = _signals_enabled(app_settings)
    if not signals_enabled:
        return OpenTelemetryRecorder(tracer=None, meter=None)

    endpoint = _telemetry_endpoint(app_settings)
    if endpoint is None:
        return _warn_and_return_noop("missing_otlp_endpoint")

    tracer_provider = _tracer_provider(app_settings, resource, endpoint)
    meter_provider = _meter_provider(app_settings, resource, endpoint)

    if tracer_provider is None and meter_provider is None:
        return NoOpTelemetryRecorder()

    tracer = (
        tracer_provider.get_tracer("noa_api.core.telemetry")
        if tracer_provider is not None
        else None
    )
    meter = (
        meter_provider.get_meter("noa_api.core.telemetry")
        if meter_provider is not None
        else None
    )

    return OpenTelemetryRecorder(tracer=tracer, meter=meter)


def _warn_and_return_noop(
    reason: str,
    exc: Exception | None = None,
) -> NoOpTelemetryRecorder:
    logger.warning(
        "telemetry_falling_back_to_noop",
        exc_info=exc,
        extra={"reason": reason},
    )
    return NoOpTelemetryRecorder()


def _telemetry_endpoint(app_settings: Settings) -> str | None:
    endpoint = app_settings.telemetry_otlp_endpoint
    if endpoint is None:
        return None

    endpoint = endpoint.strip()
    if not endpoint:
        return None

    return endpoint


def _signals_enabled(app_settings: Settings) -> bool:
    return (
        app_settings.telemetry_traces_enabled or app_settings.telemetry_metrics_enabled
    )


def _event_attributes(
    event: TelemetryEvent,
    *,
    extra_attributes: Mapping[str, TelemetryAttributeValue] | None = None,
) -> dict[str, TelemetryAttributeValue]:
    attributes = dict(event.attributes)
    if extra_attributes is None:
        return attributes
    for key, value in extra_attributes.items():
        if value is None:
            continue
        attributes[key] = value
    return attributes


def _metric_attributes_for_event(
    event: TelemetryEvent,
) -> dict[str, TelemetryAttributeValue]:
    return {
        key: value
        for key, value in event.attributes.items()
        if key in _METRIC_ATTRIBUTE_KEYS and value is not None
    }


def _active_span() -> Span | None:
    span = trace.get_current_span()
    if not span.is_recording():
        return None
    return span


def _tracer_provider(
    app_settings: Settings,
    resource: Resource,
    endpoint: str,
) -> TracerProvider | None:
    if not app_settings.telemetry_traces_enabled:
        return None

    try:
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers=app_settings.telemetry_otlp_headers,
        )
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception as exc:
        _warn_and_return_noop("trace_exporter_setup_failed", exc)
        return None

    return tracer_provider


def _meter_provider(
    app_settings: Settings,
    resource: Resource,
    endpoint: str,
) -> MeterProvider | None:
    if not app_settings.telemetry_metrics_enabled:
        return None

    try:
        exporter = OTLPMetricExporter(
            endpoint=endpoint,
            headers=app_settings.telemetry_otlp_headers,
        )
        metric_reader = PeriodicExportingMetricReader(exporter)
        meter_provider = MeterProvider(
            metric_readers=[metric_reader], resource=resource
        )
    except Exception as exc:
        _warn_and_return_noop("metric_exporter_setup_failed", exc)
        return None

    return meter_provider
