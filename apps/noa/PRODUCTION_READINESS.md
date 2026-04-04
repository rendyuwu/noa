# apps/noa Production Readiness Decisions

## Route treatment
- Admin surfaces are protected by server-side route checks and backend RBAC.

## Auth boundary
- Browser code does not store bearer tokens.
- Same-origin auth routes set and clear cookies at the Next.js boundary.
- State-changing requests require CSRF validation.

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
- `cd apps/noa && npm run verify:design-system`
- `cd apps/api && uv run pytest -q tests/test_auth_login.py tests/test_auth_login_rate_limiter.py`
