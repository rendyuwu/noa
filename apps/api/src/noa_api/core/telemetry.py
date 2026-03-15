from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Protocol

from noa_api.core.config import Settings, settings

TelemetryAttributeValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    name: str
    attributes: Mapping[str, TelemetryAttributeValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "attributes",
            MappingProxyType(dict(self.attributes)),
        )


class TelemetryRecorder(Protocol):
    def trace(self, event: TelemetryEvent) -> None: ...

    def metric(self, event: TelemetryEvent, *, value: int | float) -> None: ...

    def report(self, event: TelemetryEvent, *, detail: str | None = None) -> None: ...


class NoOpTelemetryRecorder:
    def trace(self, event: TelemetryEvent) -> None:
        return None

    def metric(self, event: TelemetryEvent, *, value: int | float) -> None:
        return None

    def report(self, event: TelemetryEvent, *, detail: str | None = None) -> None:
        return None


_FALLBACK_NOOP_TELEMETRY_RECORDER = NoOpTelemetryRecorder()


class TelemetryState(Protocol):
    telemetry: TelemetryRecorder


class HasTelemetryRecorder(Protocol):
    state: TelemetryState


def create_telemetry_recorder(app_settings: Settings = settings) -> TelemetryRecorder:
    if not app_settings.telemetry_enabled:
        return NoOpTelemetryRecorder()

    from noa_api.core.telemetry_opentelemetry import create_open_telemetry_recorder

    return create_open_telemetry_recorder(app_settings)


def get_telemetry_recorder(app: HasTelemetryRecorder) -> TelemetryRecorder:
    return getattr(app.state, "telemetry", _FALLBACK_NOOP_TELEMETRY_RECORDER)
