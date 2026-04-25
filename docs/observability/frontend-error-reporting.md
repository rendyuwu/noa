# Frontend Error Reporting

Date: 2026-03-16

## Scope

- Frontend reporting complements the existing backend telemetry and request-ID/error-code contract.
- Keep the reporting layer adapter-backed and best-effort.
- Default posture: disabled until explicitly configured.

## Report Policy

Report these client failures:

- uncaught render failures that reach `apps/web/app/error.tsx`
- uncaught browser `error` events
- unhandled promise rejections
- unexpected API failures from `apps/web/components/lib/fetch-helper.ts`, including rejected fetch/network failures (reported as raw errors before `ApiError` normalization), same-origin proxy failures, and 5xx responses

Ignore these failures:

- routine 401 expiry flows that already trigger `clearAuth()`
- expected product-state failures already represented in UI behavior, including `user_pending_approval`
- expected validation, conflict, not-found, or access-denied API responses when the UI handles them normally
- aborted or cancelled requests
- duplicate captures that can be tied to the same failure from multiple listeners

## Required Correlation Fields

Every report should carry these fields when known:

- `requestId`
- `errorCode`
- `status`
- `pathname`

Field rules:

- `requestId`: use `ApiError.requestId` or the backend `x-request-id` header when present
- `errorCode`: use the normalized backend value from `error_code` / `errorCode`
- `status`: use the HTTP status for API failures; omit for pure browser/runtime failures
- `pathname`: send the current route pathname only, not the full URL with query string or fragment

## Expected Integration Points

- `apps/web/components/lib/fetch-helper.ts`: remain the API error normalization boundary; only normalized unexpected failures become report candidates
- `apps/web/app/error.tsx`: report route-level render failures once, then preserve the current user-facing fallback UI
- global browser listeners: install one provider mounted from `apps/web/app/layout.tsx` to capture `error` and `unhandledrejection`
- local adapter module: centralize enablement checks, filtering, deduping, and provider calls in `apps/web/components/lib/observability/error-reporting.ts` and `apps/web/components/lib/observability/error-reporting-provider.tsx` so app code does not depend on a specific reporting SDK (re-export shims exist at the parent `components/lib/` level)

## Config and Environment Expectations

- `NEXT_PUBLIC_ERROR_REPORTING_ENABLED` controls whether reporting is active
- `NEXT_PUBLIC_ERROR_REPORTING_DSN` holds the client-safe ingest target required when reporting is enabled
- `NEXT_PUBLIC_ERROR_REPORTING_ENVIRONMENT` labels the deployment environment
- Missing or invalid config must fail closed: no reporting, no user-visible breakage
- Enable in non-production first, confirm signal quality, then roll out to production

## Safety and Privacy Rules

- Never send tokens, cookies, authorization headers, passwords, WHM credentials, or raw request/response bodies.
- Never send assistant conversation content, tool inputs, or tool outputs.
- Prefer stable backend identifiers over raw backend `detail` text for correlation.
- Send pathname-only route context; do not include query strings or fragments.
- Keep reporting best-effort so capture failures never block rendering, navigation, or fetch flows.
- Deduplicate repeated captures from boundary, listener, and API paths before sending.

## Operational Notes

- The frontend layer should add browser visibility for failures that do not already show up clearly in backend telemetry.
- When backend context exists, correlate through `requestId` and `errorCode` instead of inventing a separate client taxonomy.
- This doc is intentionally policy-focused; provider-specific SDK setup belongs in implementation code, not here.
