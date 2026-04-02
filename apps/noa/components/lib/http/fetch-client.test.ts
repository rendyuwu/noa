import { beforeEach, describe, expect, it, vi } from "vitest";

const clearAuth = vi.fn();
const getAuthToken = vi.fn();
const reportClientError = vi.fn();

vi.mock("@/components/lib/auth/auth-storage", () => ({
  clearAuth: (...args: unknown[]) => clearAuth(...args),
  getAuthToken: () => getAuthToken(),
}));

vi.mock("@/components/lib/observability/error-reporting", () => ({
  reportClientError: (...args: unknown[]) => reportClientError(...args),
}));

import { ApiError, fetchWithAuth, jsonOrThrow } from "./fetch-client";

describe("fetchWithAuth", () => {
  beforeEach(() => {
    clearAuth.mockReset();
    getAuthToken.mockReset();
    reportClientError.mockReset();
    vi.restoreAllMocks();
  });

  it("injects bearer auth on same-origin requests", async () => {
    getAuthToken.mockReturnValue("token-123");
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));

    await fetchWithAuth("/threads");

    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/threads",
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
    expect((fetchSpy.mock.calls[0]?.[1] as RequestInit).headers).toBeInstanceOf(Headers);
    expect(((fetchSpy.mock.calls[0]?.[1] as RequestInit).headers as Headers).get("authorization")).toBe("Bearer token-123");
  });

  it("clears auth on 401 responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 401 }));

    await fetchWithAuth("/api/threads");

    expect(clearAuth).toHaveBeenCalled();
  });

  it("rejects absolute URLs", async () => {
    await expect(fetchWithAuth("https://example.com/api")).rejects.toThrow(/absolute URL/);
  });
});

describe("jsonOrThrow", () => {
  beforeEach(() => {
    reportClientError.mockReset();
  });

  it("normalizes API payload errors", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "Forbidden",
        error_code: "forbidden",
        request_id: "req-1",
      }),
      {
        status: 403,
        headers: { "content-type": "application/json" },
      },
    );

    await expect(jsonOrThrow(response)).rejects.toMatchObject<ApiError>({
      status: 403,
      detail: "Forbidden",
      errorCode: "forbidden",
      requestId: "req-1",
    });
  });

  it("reports 5xx failures", async () => {
    const response = new Response(
      JSON.stringify({
        detail: "Internal error",
        error_code: "internal_server_error",
        request_id: "req-500",
      }),
      {
        status: 503,
        headers: { "content-type": "application/json" },
      },
    );

    await expect(jsonOrThrow(response)).rejects.toBeInstanceOf(ApiError);
    expect(reportClientError).toHaveBeenCalledWith(expect.any(ApiError), {
      errorCode: "internal_server_error",
      requestId: "req-500",
      status: 503,
    });
  });
});
