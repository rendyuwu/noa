import { describe, expect, it, vi } from "vitest";

import { filterRequestHeaders, getBackendBaseUrl, joinPaths, proxyRequest } from "./proxy";

describe("filterRequestHeaders", () => {
  it("drops hop-by-hop and connection-scoped headers", () => {
    const headers = new Headers({
      authorization: "Bearer token",
      connection: "x-remove-me",
      "content-length": "10",
      host: "localhost:3000",
      "x-keep-me": "yes",
      "x-remove-me": "no",
    });

    const filtered = filterRequestHeaders(headers);

    expect(filtered.get("authorization")).toBe("Bearer token");
    expect(filtered.get("x-keep-me")).toBe("yes");
    expect(filtered.get("host")).toBeNull();
    expect(filtered.get("content-length")).toBeNull();
    expect(filtered.get("x-remove-me")).toBeNull();
  });
});

describe("joinPaths", () => {
  it("joins path segments without duplicate slashes", () => {
    expect(joinPaths("/api/", "/threads/123")).toBe("/api/threads/123");
  });
});

describe("getBackendBaseUrl", () => {
  it("rejects a missing NOA_API_URL without using a public fallback", () => {
    expect(() => getBackendBaseUrl({})).toThrow(/Missing NOA_API_URL/);
  });
});

describe("proxyRequest", () => {
  it("passes through streaming response bodies without buffering", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode("event: message\n"));
        controller.enqueue(encoder.encode("data: first-token\n\n"));
        controller.close();
      },
    });

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: {
          "content-type": "text/event-stream",
        },
      }),
    );

    const response = await proxyRequest(
      new Request("http://localhost:3000/api/assistant/stream?threadId=1"),
      { path: ["assistant", "stream"] },
      { NOA_API_URL: "http://backend:8000" },
      fetchMock,
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://backend:8000/assistant/stream?threadId=1",
      expect.objectContaining({ method: "GET" }),
    );
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    expect(await response.text()).toContain("first-token");
  });

  it("uses the auth cookie as upstream bearer auth", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));

    await proxyRequest(
      new Request("http://localhost:3000/api/threads", {
        headers: { cookie: "noa_session=cookie-token" },
      }),
      { path: ["threads"] },
      { NOA_API_URL: "http://backend:8000" },
      fetchMock,
    );

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Headers).get("authorization")).toBe("Bearer cookie-token");
  });

  it("rejects state-changing requests with a missing csrf token", async () => {
    const response = await proxyRequest(
      new Request("http://localhost:3000/api/admin/users", {
        method: "POST",
        headers: { cookie: "noa_session=cookie-token; noa_csrf=cookie-csrf" },
        body: JSON.stringify({ email: "user@example.com" }),
      }),
      { path: ["admin", "users"] },
      { NOA_API_URL: "http://backend:8000" },
      vi.fn(),
    );

    expect(response.status).toBe(403);
  });
});
