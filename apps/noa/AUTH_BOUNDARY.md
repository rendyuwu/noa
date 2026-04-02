# Auth Boundary Contract

## Public surface
- `getAuthToken(): string | null`
- `setAuthToken(token: string): void`
- `getAuthUser(): AuthUser | null`
- `setAuthUser(user: AuthUser | null): void`
- `clearAuth(options?): void`
- `sanitizeReturnTo(value: string | null | undefined): string`
- `useRequireAuth(): boolean`

## Behavioral decisions
- Store JWT in session storage for browser-first behavior.
- Preserve one-time migration from legacy local storage tokens to session storage.
- Preserve a local storage user snapshot for role checks and post-login shell hydration.
- On 401, clear auth state and return to `/login?returnTo=<current path>`.
- Only same-origin relative return targets are allowed.
