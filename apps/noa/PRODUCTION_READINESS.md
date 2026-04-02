# apps/noa Production Readiness Decisions

## Route treatment
- Stable admin surfaces are limited to `/admin/users` and `/admin/roles`.
- Placeholder admin routes (`/admin/audit`, `/admin/whm/servers`, `/admin/proxmox/servers`) are hidden from the default navigation.
- Placeholder routes are disabled in production unless `NOA_ENABLE_PLACEHOLDER_ADMIN_SURFACES=true`.

## Observability contract
- Client-side reporting is enabled only when:
  - `NEXT_PUBLIC_ERROR_REPORTING_ENABLED=true`
  - `NEXT_PUBLIC_ERROR_REPORTING_DSN` is set
- `NEXT_PUBLIC_ERROR_REPORTING_ENVIRONMENT` is forwarded to the reporting backend when present.
- Runtime, route, fetch, and browser-global failures should include structured source metadata.

## Auth freshness contract
- Protected/admin surfaces must validate the current token via `/api/auth/me` before trusting stored role data.
- `401`/`403` validation failures clear local auth state and send the user back to `/login`.
- Transient validation failures remain observable and retryable instead of leaving stale privileged UI visible.

## Release gate
- `cd apps/noa && npm run build`
- `cd apps/noa && npm run typecheck`
- `cd apps/noa && npm test`
- `cd apps/noa && npm run test:smoke`
