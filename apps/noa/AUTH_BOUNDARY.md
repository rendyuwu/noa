# Auth Boundary Contract

## Public surface
- `getAuthToken(): string | null`
- `setAuthToken(token: string): void`
- `getAuthUser(): AuthUser | null`
- `setAuthUser(user: AuthUser | null): void`
- `clearAuth(options?): void`
- `sanitizeReturnTo(value: string | null | undefined): string`
- `useRequireAuth(): { ready; validating; error; user; refresh }`
- `useAuthSession(): { ready; validating; error; user; refresh }`

## Behavioral decisions
- Store JWT in session storage for browser-first behavior.
- Preserve one-time migration from legacy local storage tokens to session storage.
- Preserve a local storage user snapshot for post-login shell hydration, but do not trust it for protected/admin freshness.
- Protected surfaces revalidate through `/api/auth/me` before rendering privileged UI.
- On 401, clear auth state and return to `/login?returnTo=<current path>`.
- On `/api/auth/me` 401/403 responses, clear auth state and return to `/login?returnTo=<current path>`.
- On auth verification transport/server failure, show a retryable verification screen instead of stale protected UI.
- Only same-origin relative return targets are allowed.
