import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

describe("/api/assistant route wrapper", () => {
  const originalApiUrl = process.env.NOA_API_URL;

  beforeEach(() => {
    process.env.NOA_API_URL = "http://backend.test";
    vi.restoreAllMocks();
  });

  afterEach(() => {
    if (originalApiUrl === undefined) {
      delete process.env.NOA_API_URL;
    } else {
      process.env.NOA_API_URL = originalApiUrl;
    }
    vi.restoreAllMocks();
  });

  it("wraps the backend ack into assistant-transport update-state SSE events", async () => {
    const fetchCalls: Array<{ url: string; method: string | undefined }> = [];
    const liveStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: snapshot\ndata: {"type":"snapshot","snapshot":{"messages":[{"id":"m2","role":"assistant","parts":[{"type":"text","text":"Done"}]}],"isRunning":false}}\n\n',
          ),
        );
        controller.close();
      },
    });

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      fetchCalls.push({ url, method: init?.method });

      if (url === "http://backend.test/assistant") {
        return new Response(
          JSON.stringify({
            threadId: "thread-123",
            activeRunId: "run-456",
            runStatus: "starting",
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        );
      }

      if (url === "http://backend.test/assistant/threads/thread-123/state") {
        return new Response(
          JSON.stringify({
            messages: [{ id: "m1", role: "assistant", parts: [{ type: "text", text: "Hello" }] }],
            isRunning: true,
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        );
      }

      if (url === "http://backend.test/assistant/runs/run-456/live") {
        return new Response(liveStream, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }

      throw new Error(`Unexpected fetch: ${url}`);
    });

    const request = new Request("http://example.test/api/assistant", {
      method: "POST",
      headers: {
        authorization: "Bearer token",
        cookie: "session=abc",
        "content-type": "application/json",
      },
      body: JSON.stringify({ threadId: "thread-123", commands: [] }),
    });

    const response = await POST(request as any);
    const body = await response.text();

    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    expect(fetchCalls).toEqual([
      { url: "http://backend.test/assistant", method: "POST" },
      { url: "http://backend.test/assistant/threads/thread-123/state", method: "GET" },
      { url: "http://backend.test/assistant/runs/run-456/live", method: "GET" },
    ]);
    expect(body).toContain(
      'data: {"type":"update-state","operations":[{"type":"set","path":[],"value":{"messages":[{"id":"m1","role":"assistant","parts":[{"type":"text","text":"Hello"}]}],"isRunning":true}}]}'
    );
    expect(body).toContain(
      'data: {"type":"update-state","operations":[{"type":"set","path":[],"value":{"messages":[{"id":"m2","role":"assistant","parts":[{"type":"text","text":"Done"}]}],"isRunning":false}}]}'
    );
    expect(body).toContain("data: [DONE]");
  });

  it("closes the wrapper stream after the live route yields a terminal snapshot", async () => {
    const liveStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'event: delta\ndata: {"type":"delta","snapshot":{"messages":[{"id":"m2","role":"assistant","parts":[{"type":"text","text":"Done"}]}],"isRunning":false,"runStatus":"COMPLETED"}}\n\n',
          ),
        );
        controller.close();
      },
    });

    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "http://backend.test/assistant") {
        return new Response(JSON.stringify({ threadId: "thread-123", activeRunId: "run-456" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url === "http://backend.test/assistant/threads/thread-123/state") {
        return new Response(JSON.stringify({ messages: [], isRunning: true, runStatus: "RUNNING" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (url === "http://backend.test/assistant/runs/run-456/live") {
        return new Response(liveStream, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    const request = new Request("http://example.test/api/assistant", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ threadId: "thread-123", commands: [] }),
    });

    const response = await POST(request as any);
    const body = await response.text();

    expect(body).toContain('"runStatus":"COMPLETED"');
    expect(body).toContain("data: [DONE]");
  });
});
