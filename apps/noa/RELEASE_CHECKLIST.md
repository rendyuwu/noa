# apps/noa Release Checklist

- [ ] Default navigation exposes only stable production routes
- [ ] Placeholder admin routes are disabled in production unless explicitly enabled
- [ ] `NEXT_PUBLIC_ERROR_REPORTING_ENABLED`, `NEXT_PUBLIC_ERROR_REPORTING_DSN`, and environment values are configured for the target deployment
- [ ] Auth cookie is `httpOnly`, `secure` in production, and `sameSite=lax`
- [ ] CSRF protection is enforced on state-changing `/api/*` requests
- [ ] `/assistant` and `/admin/**` are protected server-side before client hydration
- [ ] Backend login rate limiting returns `429` with `Retry-After`
- [ ] Protected/admin surfaces validate auth freshness through `/api/auth/me`
- [ ] Assistant hydration failures surface a retry path and report structured context
- [ ] `npm run build` passes
- [ ] `npm run typecheck` passes
- [ ] `npm test` passes
- [ ] `npm run test:smoke` passes
