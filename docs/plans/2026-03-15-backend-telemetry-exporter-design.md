# Backend Telemetry Exporter Design

Date: 2026-03-15

## Context

The backend telemetry mapping pass is complete and the audit now treats the route-level telemetry slice as done. The remaining backend observability decision is narrower: whether to wire `apps/api/src/noa_api/core/telemetry.py` to a concrete exporter or vendor while keeping the event vocabulary stable.

The current seam is intentionally small. `apps/api/src/noa_api/core/telemetry.py` defines immutable `TelemetryEvent` payloads, a `TelemetryRecorder` protocol, a default no-op implementation, and an app-scoped accessor used by request handling, auth, assistant, admin, threads, and WHM routes.

The audit explicitly says the next backend step should be exporter wiring, not another route-level telemetry pass. It also says dashboards, alerts, sampling policy, and frontend error reporting remain deferred.

## Goals

- Wire the existing backend telemetry seam to a concrete exporter path using OpenTelemetry.
- Preserve the current event names and stable attribute vocabulary already emitted by backend callers.
- Keep exporter details behind a narrow backend boundary so route modules do not learn vendor SDK concepts.
- Preserve the current best-effort safety model so telemetry failures never break request handling.

## Non-goals

- Reopening request, auth, assistant, admin, threads, or WHM route telemetry call sites.
- Renaming telemetry events or changing the stabilized attribute vocabulary.
- Choosing dashboards, alerts, or final operational thresholds.
- Installing a frontend error reporting tool.
- Treating this slice as the place for helper-level logging cleanup or shared/helper-level `error_code` expansion.

## Approaches Considered

### 1) Wire the existing seam to OpenTelemetry in place (chosen)

Keep `TelemetryEvent`, `TelemetryRecorder`, and the existing `trace`, `metric`, and `report` methods as the only app-facing API. Extend the factory behind `create_telemetry_recorder(...)` so it can return either a no-op recorder or an OpenTelemetry-backed recorder based on configuration.

Pros:

- preserves the stable backend event vocabulary
- keeps route and middleware code unchanged
- isolates exporter details to one backend integration point
- matches the audit's active recommendation directly

Cons:

- requires a careful mapping from the current generic recorder methods to OpenTelemetry concepts
- still leaves dashboards and alerts for later

### 2) Add another abstraction layer before choosing OpenTelemetry

Introduce a second internal layer such as `TelemetryBackend` or `TelemetrySink`, then have `TelemetryRecorder` delegate to it.

Pros:

- slightly cleaner separation if multiple telemetry backends are expected soon

Cons:

- adds indirection before there is evidence it is needed
- makes a small seam more abstract than the current repo complexity justifies

### 3) Instrument routes and middleware directly with OpenTelemetry SDK calls

Bypass the current seam and add OpenTelemetry instrumentation at each emission site.

Pros:

- fastest path to a visible exporter hookup

Cons:

- leaks vendor concepts into route code
- makes the stabilized event vocabulary easier to drift
- reopens the completed route slice the audit says to leave alone

## Proposed Design

### 1) Keep the current seam as the only app-facing interface

`apps/api/src/noa_api/core/telemetry.py` remains the boundary the rest of the backend uses.

Callers continue to do exactly what they do now:

- create `TelemetryEvent` values with stable names and attributes
- call `trace(...)`, `metric(...)`, or `report(...)`
- retrieve the recorder from `app.state.telemetry`

No route or middleware module should import OpenTelemetry types directly in this slice.

### 2) Add configuration for exporter wiring without changing defaults

Add backend settings in `apps/api/src/noa_api/core/config.py` for telemetry enablement and OpenTelemetry export configuration.

Recommended initial settings:

- `telemetry_enabled: bool = False`
- `telemetry_service_name: str = "noa-api"`
- `telemetry_otlp_endpoint: str | None = None`
- `telemetry_otlp_headers: dict[str, str] = Field(default_factory=dict)`
- `telemetry_traces_enabled: bool = True`
- `telemetry_metrics_enabled: bool = True`

Defaults should preserve today's behavior: telemetry stays no-op unless explicitly enabled and sufficiently configured.

### 3) Build an OpenTelemetry-backed recorder behind the seam

Implement an OpenTelemetry-backed recorder in `apps/api/src/noa_api/core/telemetry.py` or a small sibling module such as `apps/api/src/noa_api/core/telemetry_opentelemetry.py`.

The factory should follow this behavior:

1. If telemetry is disabled, return `NoOpTelemetryRecorder`.
2. If telemetry is enabled but required exporter settings are missing, log a safe setup warning and return `NoOpTelemetryRecorder`.
3. If telemetry is enabled and valid, construct one app-scoped OpenTelemetry-backed recorder and attach it to `app.state.telemetry`.

This keeps startup ownership in `apps/api/src/noa_api/main.py` while centralizing translation logic in the telemetry layer.

### 4) Translate the current event model to OpenTelemetry centrally

The current recorder methods should map to OpenTelemetry in one place:

- `trace(event)` -> add attributes or events to the active span, or create a short-lived span event path when there is no active span context
- `metric(event, value=...)` -> record counters or histograms using bounded attributes only
- `report(event, detail=...)` -> record a high-severity span event or exception-style signal that downstream OpenTelemetry infrastructure can export

The important rule is semantic stability: backend code continues to emit the same event names and stable fields even though the exporter implementation changes underneath.

### 5) Preserve bounded metric labels

The existing telemetry design already avoids raw IDs in metric labels. The OpenTelemetry-backed recorder should preserve that rule centrally.

Allowed metric attributes should stay limited to bounded values such as:

- `status_code`
- `error_code`
- `error_type` where the current mapping already allows it
- `failure_stage`
- normalized request path or route template where already supplied
- bounded assistant command classification

High-cardinality fields such as `request_id`, `user_id`, `user_email`, `thread_id`, `server_id`, `server_name`, and raw paths should stay in traces or logs, not metric labels.

### 6) Keep `report(...)` selective

The current selective reporting candidates should remain selective after exporter wiring.

This means `report(...)` continues to be used only for the existing high-signal backend failure set:

- `api_unhandled_exception`
- auth service availability failures already mapped to stable auth `error_code` values
- unexpected or degraded assistant failure paths

Routine validation failures, authorization denials, and expected business conflicts should continue to use logs plus metrics only.

## Module Shape

Target shape after this pass:

- `apps/api/src/noa_api/core/config.py`
  - telemetry enablement and exporter settings
- `apps/api/src/noa_api/core/telemetry.py`
  - stable `TelemetryEvent` and `TelemetryRecorder` interface
  - recorder factory and safe app accessor
- `apps/api/src/noa_api/core/telemetry_opentelemetry.py` if needed
  - OpenTelemetry-specific translation and provider setup
- `apps/api/src/noa_api/main.py`
  - app startup wiring that installs the configured recorder
- `apps/api/tests/test_telemetry.py`
  - recorder factory, translation, and failure-mode coverage

## Data Flow

1. `create_app(...)` reads settings and builds the app-scoped recorder.
2. Request/auth/assistant/admin/threads/WHM code keeps emitting the same `TelemetryEvent` values it emits today.
3. The configured recorder maps those events to traces, metrics, and selective reporting signals.
4. OpenTelemetry exports those signals to the configured downstream endpoint when enabled.
5. If setup or export fails, the backend keeps serving requests and logs a telemetry failure event rather than surfacing telemetry problems to clients.

## Error Handling and Safety

- Keep telemetry best-effort only.
- Preserve existing HTTP status codes, response envelopes, and `detail` strings.
- Do not leak raw exception text, tokens, passwords, or external-system responses through telemetry setup logs.
- Treat missing or invalid exporter configuration as a safe fallback to no-op behavior.
- Keep the current route telemetry inventory frozen for this slice.

## Testing Strategy

- Extend `apps/api/tests/test_telemetry.py` to verify factory behavior for disabled, enabled, and misconfigured settings.
- Add focused tests that assert the OpenTelemetry-backed recorder preserves event names and stable attributes.
- Add tests that verify high-cardinality attributes are excluded from metrics.
- Add tests that verify exporter/reporting failures are swallowed and logged without changing API behavior.
- Keep the existing route telemetry tests as regression proof that the same backend surfaces still emit the stabilized event inventory.

## Deferred After This Slice

The following work stays explicitly out of scope after exporter wiring:

- dashboards and alerts
- sampling policy refinement
- frontend error reporting installation
- helper/service logging cleanup
- shared/helper-level `error_code` catalog expansion

Those remain future follow-up once the exporter decision is implemented.
