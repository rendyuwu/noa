import { beforeEach, describe, expect, it, vi } from "vitest";

const clearAuth = vi.fn();
const getAuthToken = vi.fn();
const reportClientError = vi.fn();

vi.mock("@/components/lib/auth-store", () => ({
  clearAuth: () => clearAuth(),
  getAuthToken: () => getAuthToken(),
}));

vi.mock("@/components/lib/observability/error-reporting", () => ({
  reportClientError: (...args: unknown[]) => reportClientError(...args),
}));

import { ApiError, fetchWithAuth, jsonOrThrow } from "./fetch-helper";

describe("fetchWithAuth", () => {
  beforeEach(() => {
    clearAuth.mockReset();
    getAuthToken.mockReset();
    getAuthToken.mockReturnValue(null);
    reportClientError.mockReset();
    vi.restoreAllMocks();
  });

  it("reports rejected fetch failures through the client error adapter", async () => {
    const networkError = new TypeError("Failed to fetch");

    vi.spyOn(globalThis, "fetch").mockRejectedValue(networkError);

    await expect(fetchWithAuth("/api/threads")).rejects.toBe(networkError);

    expect(reportClientError).toHaveBeenCalledWith(networkError);
    expect(clearAuth).not.toHaveBeenCalled();
  });

  it("preserves the original rejected fetch error if reporting throws", async () => {
    const networkError = new TypeError("Failed to fetch");

    vi.spyOn(globalThis, "fetch").mockRejectedValue(networkError);
    reportClientError.mockImplementationOnce(() => {
      throw new Error("reporting failed");
    });

    await expect(fetchWithAuth("/api/threads")).rejects.toBe(networkError);

    expect(reportClientError).toHaveBeenCalledWith(networkError);
    expect(clearAuth).not.toHaveBeenCalled();
  });
});

describe("jsonOrThrow", () => {
  beforeEach(() => {
    clearAuth.mockReset();
    getAuthToken.mockReset();
    reportClientError.mockReset();
    vi.restoreAllMocks();
  });

  it("throws an ApiError with typed API details from snake_case payload fields", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "User pending approval",
        error_code: "user_pending_approval",
        request_id: "req-123",
      }),
      {
        status: 403,
        headers: {
          "content-type": "application/json",
        },
      },
    );

    let thrown: unknown;

    try {
      await jsonOrThrow(response);
    } catch (error) {
      thrown = error;
    }

    expect(thrown).toBeInstanceOf(ApiError);
    expect(thrown).toMatchObject({
      status: 403,
      detail: "User pending approval",
      errorCode: "user_pending_approval",
      requestId: "req-123",
    });
    expect(reportClientError).not.toHaveBeenCalled();
  });

  it("falls back to x-request-id header when request_id is missing from the payload", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "Forbidden",
      }),
      {
        status: 403,
        headers: {
          "content-type": "application/json",
          "x-request-id": "req-header-456",
        },
      },
    );

    await expect(jsonOrThrow(response)).rejects.toMatchObject({
      status: 403,
      detail: "Forbidden",
      requestId: "req-header-456",
    });

    expect(reportClientError).not.toHaveBeenCalled();
  });

  it("does not report product-state 404 failures", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "Thread not found",
        error_code: "thread_not_found",
        request_id: "req-404",
      }),
      {
        status: 404,
        headers: {
          "content-type": "application/json",
        },
      },
    );

    await expect(jsonOrThrow(response)).rejects.toMatchObject({
      status: 404,
      detail: "Thread not found",
      errorCode: "thread_not_found",
      requestId: "req-404",
    });

    expect(reportClientError).not.toHaveBeenCalled();
  });

  it("reports unexpected 5xx failures with normalized API context", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "Internal error",
        error_code: "internal_server_error",
        request_id: "req-500",
      }),
      {
        status: 503,
        headers: {
          "content-type": "application/json",
        },
      },
    );

    await expect(jsonOrThrow(response)).rejects.toMatchObject({
      status: 503,
      detail: "Internal error",
      errorCode: "internal_server_error",
      requestId: "req-500",
    });

    expect(reportClientError).toHaveBeenCalledWith(expect.any(ApiError), {
      errorCode: "internal_server_error",
      requestId: "req-500",
      status: 503,
    });
  });

  it("reports proxy-style status 0 failures with normalized API context", async () => {
    const response = {
      headers: new Headers(),
      json: async () => ({
        detail: "Unable to reach API",
        error_code: "proxy_error",
        request_id: "req-proxy-0",
      }),
      ok: false,
      status: 0,
    } as Response;

    await expect(jsonOrThrow(response)).rejects.toMatchObject({
      status: 0,
      detail: "Unable to reach API",
      errorCode: "proxy_error",
      requestId: "req-proxy-0",
    });

    expect(reportClientError).toHaveBeenCalledWith(expect.any(ApiError), {
      errorCode: "proxy_error",
      requestId: "req-proxy-0",
      status: 0,
    });
  });
});
