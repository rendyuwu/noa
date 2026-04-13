# Observability Status

Date: 2026-03-16

## Current state

- The repo implementation slice for observability is complete.
- Backend telemetry/exporter wiring is already in place.
- Repo-owned dashboard and frontend reporting guidance lives in this directory:
  - `docs/observability/backend-observability-baseline.md`
  - `docs/observability/frontend-error-reporting.md`
- Frontend reporting code is implemented in the app, but remains disabled by default and should be enabled intentionally by environment.

## What is done in repo

- Backend request, auth, assistant, admin, threads, and WHM telemetry is implemented and exporter-backed.
- The backend event vocabulary and bounded metric dimensions are defined and should now be treated as stable for operational rollout.
- Frontend reporting policy, adapter/provider wiring, route-level crash reporting, and selective API/network failure reporting are implemented.
- The remaining work is not another repo implementation pass by default.

## What comes next

The next step is operational rollout work:

1. Realize the documented dashboards in the chosen observability platform.
2. Add the initial small, high-signal alert set from `docs/observability/backend-observability-baseline.md`.
3. Enable frontend reporting in non-production first.
4. Validate signal quality, noise level, deduping, and correlation fields before any production enablement.

## What can stay deferred

- Additional backend helper/service logging cleanup can stay deferred unless dashboarding, alert review, or incident response shows a real gap.
- Additional shared/helper-level `error_code` expansion can stay deferred unless frontend reporting or operations exposes missing correlation coverage.
- Route-level telemetry/exporter work should not be reopened unless a concrete defect is found.

## How to use these docs

- Use this file as the current status and next-step entry point.
- Use `docs/observability/backend-observability-baseline.md` for dashboard and alert realization.
- Use `docs/observability/frontend-error-reporting.md` for frontend rollout policy and validation expectations.
- Treat planning docs and branch handoff material as implementation history, not the primary source for current observability status.
