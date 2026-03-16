# Observability Dashboards, Alerts, and Frontend Reporting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a staged observability follow-up by documenting the backend dashboard/alert baseline and wiring selective frontend error reporting with backend request correlation.

**Architecture:** Keep backend telemetry emission unchanged and treat the existing OpenTelemetry-backed event vocabulary as the source for dashboards and alerts. Add frontend reporting behind a local `apps/web` adapter so browser capture, filtering, and vendor integration stay isolated from UI components.

**Tech Stack:** FastAPI telemetry docs, Next.js 16, React 19, Vitest, `@sentry/nextjs`, Markdown

---

### Task 1: Add the backend observability baseline docs

**Files:**
- Create: `docs/observability/backend-observability-baseline.md`
- Create: `docs/observability/frontend-error-reporting.md`
- Review: `docs/reports/2026-03-14-error-handling-and-logging-audit.md`
- Review: `docs/plans/2026-03-15-backend-telemetry-mapping-design.md`
- Review: `docs/plans/2026-03-15-backend-telemetry-exporter-design.md`

**Step 1: Write the docs-first baseline**

Document the canonical dashboard groups, alert rules, and frontend-reporting policy instead of trying to encode vendor-specific dashboards in a repo that does not yet manage them as code.

```md
## API health
- Request rate
- 5xx rate
- P50/P95 latency
- Unhandled exceptions

## Auth
- Login success vs rejection
- Current-user rejection rate
- Authentication service outages

## Alerts
- Page on sustained 5xx regression
- Page on auth service availability failures
- Page on unexpected assistant failure spikes
```

**Step 2: Review the docs for event-name drift**

Run: `grep -n "api_unhandled_exception\|auth_\|assistant_" docs/observability/backend-observability-baseline.md`

Expected: the docs refer only to existing backend event names or clearly derived operational groupings.

**Step 3: Commit**

```bash
git add docs/observability/backend-observability-baseline.md docs/observability/frontend-error-reporting.md
git commit -m "docs: add observability baseline"
```

### Task 2: Add frontend reporting configuration and dependency

**Files:**
- Modify: `apps/web/package.json`
- Modify: `apps/web/.env.example`

**Step 1: Write the failing configuration expectation in docs or tests**

Add or extend a small unit test file for config parsing if you create one, or start by documenting the new env surface in `.env.example`.

```env
NOA_API_URL=http://localhost:8000
NEXT_PUBLIC_ERROR_REPORTING_ENABLED=false
NEXT_PUBLIC_ERROR_REPORTING_DSN=
NEXT_PUBLIC_ERROR_REPORTING_ENVIRONMENT=development
```

**Step 2: Add the dependency**

Add `@sentry/nextjs` to `apps/web/package.json` dependencies.

```json
"dependencies": {
  "@sentry/nextjs": "^9.0.0"
}
```

**Step 3: Install web dependencies**

Run: `npm install`

Workdir: `apps/web`

Expected: install completes and updates `package-lock.json`.

**Step 4: Commit**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/.env.example
git commit -m "chore: add frontend error reporting config"
```

### Task 3: Add the frontend reporting adapter with tests

**Files:**
- Create: `apps/web/components/lib/error-reporting.ts`
- Create: `apps/web/components/lib/error-reporting.test.ts`

**Step 1: Write the failing tests**

Cover three behaviors:

1. reporting is a no-op when disabled or unconfigured
2. expected API failures such as `user_pending_approval` are filtered out
3. unexpected failures forward normalized extras such as `requestId`, `errorCode`, `status`, and `pathname`

```ts
it("ignores expected approval errors", () => {
  const error = new ApiError(403, "Pending", {
    errorCode: "user_pending_approval",
    requestId: "req-123",
  });

  expect(shouldReportClientError(error)).toBe(false);
});
```

**Step 2: Run the test to verify it fails**

Run: `npm run test -- error-reporting.test.ts`

Workdir: `apps/web`

Expected: FAIL because the module does not exist yet.

**Step 3: Write the minimal adapter**

Expose a narrow app-facing API with SDK details hidden inside.

```ts
export function reportClientError(error: unknown, context: ReportContext = {}) {
  if (!isErrorReportingEnabled()) return;
  if (!shouldReportClientError(error)) return;

  Sentry.captureException(normalizeError(error), {
    extra: buildReportExtras(error, context),
  });
}
```

Include helpers such as:

- `isErrorReportingEnabled()`
- `shouldReportClientError(error)`
- `buildReportExtras(error, context)`

**Step 4: Run the tests to verify they pass**

Run: `npm run test -- error-reporting.test.ts`

Workdir: `apps/web`

Expected: PASS

**Step 5: Commit**

```bash
git add apps/web/components/lib/error-reporting.ts apps/web/components/lib/error-reporting.test.ts
git commit -m "feat: add frontend error reporting adapter"
```

### Task 4: Add a global browser reporting provider

**Files:**
- Create: `apps/web/components/lib/error-reporting-provider.tsx`
- Create: `apps/web/components/lib/error-reporting-provider.test.tsx`
- Modify: `apps/web/app/layout.tsx`

**Step 1: Write the failing provider test**

Verify that the provider registers one `error` listener and one `unhandledrejection` listener and forwards eligible failures through the adapter.

```tsx
it("reports unhandled promise rejections once", () => {
  render(
    <ErrorReportingProvider>
      <div>child</div>
    </ErrorReportingProvider>,
  );

  window.dispatchEvent(new PromiseRejectionEvent("unhandledrejection", {
    reason: new Error("boom"),
  }));

  expect(reportClientError).toHaveBeenCalledTimes(1);
});
```

**Step 2: Run the test to verify it fails**

Run: `npm run test -- error-reporting-provider.test.tsx`

Workdir: `apps/web`

Expected: FAIL because the provider does not exist yet.

**Step 3: Write the provider and mount it**

Use a client component and mount it once from `apps/web/app/layout.tsx`.

```tsx
export function ErrorReportingProvider({ children }: PropsWithChildren) {
  useEffect(() => {
    const onError = (event: ErrorEvent) => {
      reportClientError(event.error ?? new Error(event.message), {
        source: "window.error",
      });
    };

    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      reportClientError(event.reason, { source: "window.unhandledrejection" });
    };

    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onUnhandledRejection);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection);
    };
  }, []);

  return <>{children}</>;
}
```

**Step 4: Run the provider tests**

Run: `npm run test -- error-reporting-provider.test.tsx`

Workdir: `apps/web`

Expected: PASS

**Step 5: Commit**

```bash
git add apps/web/components/lib/error-reporting-provider.tsx apps/web/components/lib/error-reporting-provider.test.tsx apps/web/app/layout.tsx
git commit -m "feat: add global browser error reporting"
```

### Task 5: Report route-level render failures

**Files:**
- Modify: `apps/web/app/error.tsx`

**Step 1: Write the failing test or extend an existing boundary test if needed**

If there is no boundary test yet, add a focused test next to the reporting adapter or provider that asserts `reportClientError(...)` is called from the error boundary effect.

```tsx
useEffect(() => {
  reportClientError(error, {
    source: "nextjs.global-error-boundary",
    digest: error.digest,
  });
}, [error]);
```

**Step 2: Implement the boundary report**

Keep the existing `console.error(error)` behavior, but add the adapter call before or alongside it.

**Step 3: Run the focused tests**

Run: `npm run test -- error-reporting.test.ts error-reporting-provider.test.tsx`

Workdir: `apps/web`

Expected: PASS

**Step 4: Commit**

```bash
git add apps/web/app/error.tsx
git commit -m "feat: report route-level frontend crashes"
```

### Task 6: Report unexpected API failures with backend correlation

**Files:**
- Modify: `apps/web/components/lib/fetch-helper.ts`
- Modify: `apps/web/components/lib/fetch-helper.test.ts`
- Review: `apps/web/components/lib/error-message.ts`

**Step 1: Write the failing tests**

Add focused cases that verify:

1. expected product-state failures are not reported
2. 5xx or network-style failures become report candidates
3. report context includes `requestId`, `errorCode`, and `status`

```ts
it("keeps request correlation on unexpected API failures", async () => {
  const response = new Response(JSON.stringify({
    detail: "Internal error",
    error_code: "internal_server_error",
    request_id: "req-999",
  }), { status: 500, headers: { "content-type": "application/json" } });

  await expect(jsonOrThrow(response)).rejects.toMatchObject({
    status: 500,
    errorCode: "internal_server_error",
    requestId: "req-999",
  });
});
```

**Step 2: Run the test to verify it fails for reporting integration**

Run: `npm run test -- fetch-helper.test.ts error-reporting.test.ts`

Workdir: `apps/web`

Expected: FAIL until the reporting integration is added.

**Step 3: Add selective reporting**

Keep `jsonOrThrow(...)` as the shared normalization point, but only report unexpected failures.

```ts
const apiError = new ApiError(response.status, detail, {
  errorCode,
  requestId,
});

if (response.status >= 500 || response.status === 0) {
  reportClientError(apiError, {
    source: "api.fetch",
    status: response.status,
    requestId,
    errorCode,
  });
}

throw apiError;
```

**Step 4: Run the focused tests**

Run: `npm run test -- fetch-helper.test.ts error-reporting.test.ts`

Workdir: `apps/web`

Expected: PASS

**Step 5: Commit**

```bash
git add apps/web/components/lib/fetch-helper.ts apps/web/components/lib/fetch-helper.test.ts
git commit -m "feat: report unexpected api failures"
```

### Task 7: Run final verification and review the diff

**Files:**
- Review: `docs/observability/backend-observability-baseline.md`
- Review: `docs/observability/frontend-error-reporting.md`
- Review: `apps/web/.env.example`
- Review: `apps/web/app/error.tsx`
- Review: `apps/web/app/layout.tsx`
- Review: `apps/web/components/lib/error-reporting.ts`
- Review: `apps/web/components/lib/error-reporting-provider.tsx`
- Review: `apps/web/components/lib/fetch-helper.ts`

**Step 1: Run the focused web test suite**

Run: `npm run test -- error-reporting.test.ts error-reporting-provider.test.tsx fetch-helper.test.ts`

Workdir: `apps/web`

Expected: PASS

**Step 2: Run the production check**

Run: `npm run build`

Workdir: `apps/web`

Expected: PASS

**Step 3: Review the final diff**

Run: `git diff -- docs/observability apps/web/.env.example apps/web/app/error.tsx apps/web/app/layout.tsx apps/web/components/lib/error-reporting.ts apps/web/components/lib/error-reporting-provider.tsx apps/web/components/lib/fetch-helper.ts apps/web/package.json`

Expected: diff is limited to the observability docs, frontend reporting files, and the small integration points described in this plan.

**Step 4: Commit**

```bash
git add docs/observability apps/web/.env.example apps/web/app/error.tsx apps/web/app/layout.tsx apps/web/components/lib/error-reporting.ts apps/web/components/lib/error-reporting-provider.tsx apps/web/components/lib/fetch-helper.ts apps/web/package.json apps/web/package-lock.json
git commit -m "feat: add observability follow-up baseline"
```
