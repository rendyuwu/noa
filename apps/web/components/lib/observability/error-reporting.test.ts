import { beforeEach, describe, expect, it, vi } from "vitest";

const init = vi.fn();
const captureException = vi.fn();

vi.mock("@sentry/nextjs", () => ({
  init: (...args: unknown[]) => init(...args),
  captureException: (...args: unknown[]) => captureException(...args),
}));

const loadErrorReporting = async () => {
  vi.resetModules();
  const modulePath = "./error-reporting";
  const fetchHelperPath = "@/components/lib/fetch-helper";
  const [{ ApiError }, errorReporting] = await Promise.all([
    import(fetchHelperPath),
    import(/* @vite-ignore */ modulePath),
  ]);

  return {
    ApiError,
    ...errorReporting,
  };
};

describe("error reporting", () => {
  beforeEach(() => {
    init.mockReset();
    captureException.mockReset();
    vi.unstubAllEnvs();
    window.history.replaceState({}, "", "/");
  });

  const handledApiErrorCases = [
    { label: "invalid credentials", status: 401, detail: "Invalid credentials", errorCode: "invalid_credentials", requestId: "req-401-invalid-credentials" },
    { label: "missing bearer token", status: 401, detail: "Missing bearer token", errorCode: "missing_bearer_token", requestId: "req-401-missing-bearer" },
    { label: "invalid token", status: 401, detail: "Invalid token", errorCode: "invalid_token", requestId: "req-401-invalid-token" },
    { label: "approval gating", status: 403, detail: "Pending", errorCode: "user_pending_approval", requestId: "req-403-pending" },
    { label: "request validation", status: 422, detail: "Validation failed", errorCode: "request_validation_error", requestId: "req-422" },
    { label: "access denied", status: 403, detail: "Admin access required", errorCode: "admin_access_required", requestId: "req-403-admin" },
    { label: "tool access denied", status: 403, detail: "Tool access denied", errorCode: "tool_access_denied", requestId: "req-403-tool-access" },
    { label: "not found", status: 404, detail: "Admin user not found", errorCode: "admin_user_not_found", requestId: "req-404-admin-user" },
    { label: "thread not found", status: 404, detail: "Thread not found", errorCode: "thread_not_found", requestId: "req-404" },
    { label: "server not found", status: 404, detail: "Server not found", errorCode: "whm_server_not_found", requestId: "req-404-server" },
    { label: "tool call not found", status: 404, detail: "Tool call not found", errorCode: "tool_call_not_found", requestId: "req-404-tool-call" },
    { label: "action request not found", status: 404, detail: "Action request not found", errorCode: "action_request_not_found", requestId: "req-404-action-request" },
    { label: "last active admin conflict", status: 409, detail: "Cannot disable the last active admin", errorCode: "last_active_admin", requestId: "req-409" },
    { label: "self deactivate conflict", status: 409, detail: "Cannot deactivate your own account", errorCode: "self_deactivate_admin", requestId: "req-409-self-deactivate" },
    { label: "server name conflict", status: 409, detail: "Server name already exists", errorCode: "whm_server_name_exists", requestId: "req-409-server-name" },
    { label: "action request already decided", status: 409, detail: "Action request already decided", errorCode: "action_request_already_decided", requestId: "req-409-action-request" },
  ] as const;

  const unexpectedHandledStatusCases = [
    { label: "401 with unknown code", status: 401, detail: "Session revoked unexpectedly", errorCode: "session_revoked", requestId: "req-401-unexpected" },
    { label: "422 with unknown code", status: 422, detail: "Unexpected validation envelope", errorCode: "schema_mismatch", requestId: "req-422-unexpected" },
  ] as const;

  it("is a no-op when disabled", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");

    const { isErrorReportingEnabled, reportClientError } = await loadErrorReporting();

    expect(isErrorReportingEnabled()).toBe(false);

    reportClientError(new Error("boom"));

    expect(init).not.toHaveBeenCalled();
    expect(captureException).not.toHaveBeenCalled();
  });

  it("is a no-op when the DSN is missing", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "   ");

    const { isErrorReportingEnabled, reportClientError } = await loadErrorReporting();

    expect(isErrorReportingEnabled()).toBe(false);

    reportClientError(new Error("boom"));

    expect(init).not.toHaveBeenCalled();
    expect(captureException).not.toHaveBeenCalled();
  });

  it.each(handledApiErrorCases)("ignores handled ApiError cases for $label", async ({ status, detail, errorCode, requestId }) => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");

    const { ApiError, reportClientError, shouldReportClientError } = await loadErrorReporting();

    const error = new ApiError(status, detail, { errorCode, requestId });

    expect(shouldReportClientError(error)).toBe(false);

    reportClientError(error);

    expect(init).not.toHaveBeenCalled();
    expect(captureException).not.toHaveBeenCalled();
  });

  it.each(unexpectedHandledStatusCases)("still reports unexpected ApiError cases for $label", async ({ status, detail, errorCode, requestId }) => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");

    const { ApiError, reportClientError, shouldReportClientError } = await loadErrorReporting();

    const error = new ApiError(status, detail, { errorCode, requestId });

    expect(shouldReportClientError(error)).toBe(true);

    reportClientError(error);

    expect(init).toHaveBeenCalledTimes(1);
    expect(captureException).toHaveBeenCalledWith(error, {
      extra: { errorCode, pathname: "/", requestId, status },
    });
  });

  it("still reports unexpected ApiError client failures outside the handled subset", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");

    const { ApiError, reportClientError, shouldReportClientError } = await loadErrorReporting();

    const error = new ApiError(429, "Too many requests", {
      errorCode: "rate_limited",
      requestId: "req-429",
    });

    expect(shouldReportClientError(error)).toBe(true);

    reportClientError(error);

    expect(init).toHaveBeenCalledTimes(1);
    expect(captureException).toHaveBeenCalledWith(error, {
      extra: {
        errorCode: "rate_limited",
        pathname: "/",
        requestId: "req-429",
        status: 429,
      },
    });
  });

  it("forwards normalized extras for unexpected failures", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");
    window.history.replaceState({}, "", "/threads/thread-1");

    const { ApiError, buildReportExtras, reportClientError } = await loadErrorReporting();

    const error = new ApiError(500, "Internal error", {
      errorCode: "internal_server_error",
      requestId: "req-999",
    });

    expect(buildReportExtras(error, { source: "api.fetch" })).toEqual({
      errorCode: "internal_server_error",
      pathname: "/threads/thread-1",
      requestId: "req-999",
      source: "api.fetch",
      status: 500,
    });

    reportClientError(error, { source: "api.fetch" });

    expect(init).toHaveBeenCalledTimes(1);
    expect(captureException).toHaveBeenCalledWith(error, {
      extra: {
        errorCode: "internal_server_error",
        pathname: "/threads/thread-1",
        requestId: "req-999",
        source: "api.fetch",
        status: 500,
      },
    });
  });

  it("initializes Sentry once before the first capture and forwards the environment", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENVIRONMENT", "staging");

    const { reportClientError } = await loadErrorReporting();

    reportClientError(new Error("first boom"));
    reportClientError(new Error("second boom"));

    expect(init).toHaveBeenCalledTimes(1);
    expect(init).toHaveBeenCalledWith({
      defaultIntegrations: false,
      dsn: "https://examplePublicKey@o0.ingest.sentry.io/0",
      environment: "staging",
    });
    expect(init.mock.invocationCallOrder[0]).toBeLessThan(captureException.mock.invocationCallOrder[0]);
    expect(captureException).toHaveBeenCalledTimes(2);
  });

  it("swallows Sentry initialization failures", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");
    init.mockImplementationOnce(() => {
      throw new Error("Sentry init failed");
    });

    const { reportClientError } = await loadErrorReporting();

    expect(() => reportClientError(new Error("boom"))).not.toThrow();

    expect(init).toHaveBeenCalledTimes(1);
    expect(captureException).not.toHaveBeenCalled();
  });

  it("swallows Sentry capture failures", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");
    captureException.mockImplementationOnce(() => {
      throw new Error("Sentry capture failed");
    });

    const { reportClientError } = await loadErrorReporting();

    expect(() => reportClientError(new Error("boom"))).not.toThrow();

    expect(init).toHaveBeenCalledTimes(1);
    expect(captureException).toHaveBeenCalledTimes(1);
  });

  it("suppresses aborted and cancelled failures", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");

    const { reportClientError, shouldReportClientError } = await loadErrorReporting();

    const abortError = new DOMException("The operation was aborted.", "AbortError");
    const canceledError = Object.assign(new Error("Request canceled by the client"), {
      code: "ERR_CANCELED",
    });

    expect(shouldReportClientError(abortError)).toBe(false);
    expect(shouldReportClientError(canceledError)).toBe(false);

    reportClientError(abortError);
    reportClientError(canceledError);

    expect(init).not.toHaveBeenCalled();
    expect(captureException).not.toHaveBeenCalled();
  });

  it("dedupes repeat captures of the same error object", async () => {
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_ERROR_REPORTING_DSN", "https://examplePublicKey@o0.ingest.sentry.io/0");

    const { reportClientError } = await loadErrorReporting();

    const error = new Error("duplicate boom");

    reportClientError(error);
    reportClientError(error, { source: "window.error" });

    expect(init).toHaveBeenCalledTimes(1);
    expect(captureException).toHaveBeenCalledTimes(1);
    expect(captureException).toHaveBeenCalledWith(error, {
      extra: {
        pathname: "/",
      },
    });
  });
});
