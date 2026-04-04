# NOA Rewrite Architecture Notes

## Guardrails
- `app/**` owns route composition, metadata, and route handlers.
- `components/layout/**` owns shared shell, responsive nav, and route framing.
- `components/assistant/**` owns assistant presentation only.
- `components/admin/**` owns admin presentation only.
- `components/lib/auth/**` owns token storage, auth redirects, and return-to sanitization.
- `components/lib/http/**` owns proxy helpers and normalized browser HTTP access.
- `components/lib/observability/**` owns client error reporting hooks.

## State ownership
- Auth token: session storage (`noa.jwt`) with one-time local-storage migration fallback for brownfield continuity.
- User snapshot: local storage (`noa.user`) for role checks and shell personalization.
- Shell UI state: local storage (`noa.shell.collapsed`) + component state.
- Feature state remains local to each route surface until a real shared abstraction is justified.

## Boundary rules
- Route files remain thin composition modules.
- Assistant and admin features do not import from each other.
- Browser fetches go through the shared auth-aware HTTP helper and same-origin `/api` path normalization.
- Proxy behavior stays server-side under `app/api/[...path]` backed by reusable pure helpers for testability.

## Deferred follow-up
- assistant-ui runtime adapters
- admin CRUD/data-table implementations
- Playwright parity/smoke harness
- backend contract fixtures beyond the scaffold/auth/proxy surface
