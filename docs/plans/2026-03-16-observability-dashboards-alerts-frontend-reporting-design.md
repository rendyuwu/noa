# Observability Dashboards, Alerts, and Frontend Reporting Design

Date: 2026-03-16

## Context

The backend observability foundation is now in place:

- `apps/api/src/noa_api/core/telemetry.py` defines the stable telemetry seam.
- `apps/api/src/noa_api/core/telemetry_opentelemetry.py` wires that seam to an OpenTelemetry-backed exporter path.
- The refreshed audit in `docs/reports/2026-03-14-error-handling-and-logging-audit.md` now treats backend route/exporter work as complete and points the next slice at dashboards, alerts, and frontend error reporting.

The frontend already has partial error-handling primitives:

- `apps/web/components/lib/fetch-helper.ts` preserves `errorCode` and `requestId` from API failures.
- `apps/web/components/lib/error-message.ts` maps known user-facing failures.
- `apps/web/app/error.tsx` provides a top-level error boundary.

What is still missing is an operational layer that turns the current backend signals into something actionable and adds browser-side visibility for errors that never reach the API telemetry path.

## Goal

- Turn the existing backend telemetry vocabulary into a small, version-controlled dashboard and alert baseline.
- Add frontend error reporting in a way that captures high-signal client failures without reopening the completed backend route/exporter slice.
- Keep the work staged so the backend operational baseline lands first and frontend reporting follows as a separate phase within the same overall design.

## Non-goals

- Reopening backend route-level telemetry call sites or changing backend event names.
- Expanding route-specific backend `error_code` coverage beyond the current completed slices.
- Building a large observability platform abstraction layer.
- Alerting on expected product-state failures such as validation errors, routine 401/403 responses, or known business conflicts.

## Approaches Considered

### 1) Staged baseline: dashboards and alerts first, frontend reporting second (chosen)

Define the backend operational baseline from the telemetry that already exists, then add browser-side reporting behind a thin frontend adapter.

Pros:

- uses the backend work that is already complete
- keeps the risk low by not mixing operational docs, alerts, and new browser instrumentation in one pass
- gives the team immediate value even before frontend reporting is installed
- makes frontend rollout easier because request correlation rules are already documented

Cons:

- full observability is not complete until both phases land
- dashboard realization remains deployment-specific even if the baseline lives in repo docs

### 2) One combined observability push

Implement dashboards, alerts, and frontend reporting as one end-to-end slice.

Pros:

- one milestone for the whole observability follow-up

Cons:

- wider scope and more moving parts in one branch
- harder to review and roll out safely
- easier to blur operational baseline work with browser SDK integration work

### 3) Frontend reporting first

Install browser error reporting now and come back to dashboards and alerts later.

Pros:

- quickest path to browser-side visibility

Cons:

- misses the opportunity to operationalize the backend telemetry that is already ready
- creates client-side signal before the team has a documented backend dashboard/alert baseline

## Proposed Design

### 1) Stage the work into two phases

Phase 1 is backend operationalization. Phase 2 is frontend reporting.

This keeps the completed backend telemetry implementation intact while making the next step concrete:

1. define what operators should watch,
2. define when they should be alerted, and then
3. add browser-side reporting for failures that logs and API telemetry do not fully explain.

### 2) Store the backend dashboard and alert baseline as repo docs

The repo does not currently contain dashboard-as-code or alert-rule infrastructure. Because of that, the next slice should store the baseline as version-controlled docs under a new `docs/observability/` area instead of pretending that a specific hosted backend is already managed in repo.

The docs should define the canonical operational baseline, even if the final dashboards and alerts are realized later in Grafana, Datadog, or another OTLP-compatible platform.

Recommended repo artifacts:

- `docs/observability/backend-observability-baseline.md`
- `docs/observability/frontend-error-reporting.md`

### 3) Backend dashboard baseline

The initial dashboard baseline should answer the operational questions that matter most for NOA today.

Recommended dashboard groups:

- `API health`: request rate, 5xx rate, latency percentiles, unhandled exceptions
- `Auth`: login success/rejection rates, current-user rejection rate, authentication-service outages
- `Assistant`: unexpected/degraded assistant failures, fallback/persistence failure counts, request latency where available
- `Admin and management`: outcome counters for admin users, threads, and WHM admin operations

The baseline should stay anchored to the existing telemetry vocabulary from the backend mapping/exporter passes. It should not introduce a second naming model.

### 4) Alert policy baseline

The initial alert set should be intentionally small and high-signal.

Recommended page-worthy or high-urgency candidates:

- sustained API 5xx increase
- sustained latency regression on core API traffic
- unhandled exception bursts
- authentication-service availability failures
- unexpected or degraded assistant failure spikes

Recommended non-alerting signals that should stay dashboard-only at first:

- routine validation failures
- authorization denials
- expected conflicts or not-found business outcomes
- low-volume single-event anomalies without persistence

This keeps alerts actionable and avoids teaching the team to ignore them.

### 5) Frontend reporting tool choice

For the frontend phase, use `@sentry/nextjs` behind a small local adapter.

Why this choice:

- it fits a Next.js app-router application
- it handles browser exceptions, React render failures, and promise rejections well
- it can capture structured extras such as backend `requestId`, `errorCode`, route, and environment
- a local adapter keeps the app from depending on Sentry-specific calls everywhere

The adapter boundary matters more than the specific vendor. If the reporting platform changes later, the code churn stays concentrated in one place.

### 6) Frontend reporting architecture

Frontend reporting should be added in three layers:

1. `apps/web/components/lib/error-reporting.ts`
   - owns SDK initialization, enablement checks, filtering, and the app-facing `reportClientError(...)` helper
2. `apps/web/components/lib/error-reporting-provider.tsx`
   - installs global `error` and `unhandledrejection` listeners in the browser and forwards eligible failures to the adapter
3. targeted integration points
   - `apps/web/app/layout.tsx` mounts the provider once
   - `apps/web/app/error.tsx` reports route-level render failures
   - `apps/web/components/lib/fetch-helper.ts` can report unexpected API failures with `requestId` and `errorCode` context while ignoring expected user-state failures

### 7) Frontend reporting rules

The frontend phase should report only unexpected or operationally useful failures.

Report:

- uncaught render errors reaching `apps/web/app/error.tsx`
- uncaught browser `error` events
- unhandled promise rejections
- unexpected API failures, especially 5xx responses or network failures

Do not report:

- `user_pending_approval`
- routine 401 expiry flows that already clear auth
- expected validation and business-conflict errors that are already represented in UI behavior
- duplicated events that the boundary/provider can prove were already captured

### 8) Correlation between frontend and backend signals

The browser reporting layer should carry backend identifiers when they already exist, especially:

- `requestId`
- `errorCode`
- HTTP status
- current pathname

This is important because `fetch-helper.ts` already preserves the API envelope fields needed to correlate a browser-visible failure with backend telemetry and logs.

### 9) Configuration surface

The frontend phase should use explicit environment variables documented in `apps/web/.env.example`.

Recommended variables:

- `NEXT_PUBLIC_ERROR_REPORTING_ENABLED`
- `NEXT_PUBLIC_ERROR_REPORTING_DSN`
- `NEXT_PUBLIC_ERROR_REPORTING_ENVIRONMENT`

Defaults should preserve current behavior: no frontend reporting unless explicitly configured.

### 10) Rollout order

Recommended rollout:

1. land the backend dashboard and alert baseline docs
2. verify the documented baseline against the current telemetry event inventory
3. add the frontend adapter and tests with reporting disabled by default
4. wire the global provider and error boundary integration
5. enable the reporting config in non-production first, validate signal quality, then decide production rollout

## Error Handling and Safety

- Keep backend telemetry event names and attributes stable.
- Keep high-cardinality identifiers out of metrics and alerts; they belong in logs, traces, and browser-report extras.
- Do not send secrets, tokens, or raw protected payloads through frontend reporting.
- Filter expected application-state failures so frontend reporting stays high-signal.
- Preserve existing user-visible error messages and auth-expiry behavior.

## Testing Strategy

- Add frontend unit tests for the reporting adapter enablement and filtering behavior.
- Add provider tests that verify browser `error` and `unhandledrejection` events are captured once.
- Extend `fetch-helper` tests to verify only unexpected failures are report candidates and that `requestId` / `errorCode` context is forwarded.
- Treat the dashboard and alert baseline docs as reviewable artifacts; verify they map cleanly to the existing backend telemetry vocabulary instead of inventing new event names.

## Resume Point

This design is approved for the staged follow-up.

The next execution step should be the paired implementation plan in `docs/plans/2026-03-16-observability-dashboards-alerts-frontend-reporting-implementation-plan.md`, which should:

1. create the version-controlled backend dashboard and alert baseline docs,
2. add the frontend reporting adapter and global integration points, and
3. verify that browser-side reports carry the backend correlation fields already exposed by the existing API error helpers.
