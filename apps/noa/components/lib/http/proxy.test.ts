import { describe, expect, it, vi } from "vitest";

import { filterRequestHeaders, joinPaths, proxyRequest } from "./proxy";

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
});
