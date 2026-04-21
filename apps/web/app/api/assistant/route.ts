import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-connection",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

type AssistantRunAckResponse = {
  threadId: string;
  activeRunId?: string | null;
  runStatus?: string | null;
};

type AssistantLiveEvent = {
  type?: unknown;
  snapshot?: unknown;
};

function getConnectionHeaderNames(headers: Headers) {
  const value = headers.get("connection");
  const out = new Set<string>();
  if (!value) return out;

  for (const raw of value.split(",")) {
    const token = raw.trim().toLowerCase();
    if (token) out.add(token);
  }

  return out;
}

function getBackendBaseUrl() {
  const url = process.env.NOA_API_URL ?? process.env.NEXT_PUBLIC_API_URL;
  if (!url) {
    throw new Error(
      "Missing NOA_API_URL. Set NOA_API_URL to your backend base URL (NEXT_PUBLIC_API_URL is a legacy fallback).",
    );
  }
  return url;
}

function joinPaths(a: string, b: string) {
  const aSeg = a.split("/").filter(Boolean);
  const bSeg = b.split("/").filter(Boolean);
  return "/" + [...aSeg, ...bSeg].join("/");
}

function filterRequestHeaders(src: Headers) {
  const out = new Headers();
  const connectionHeaderNames = getConnectionHeaderNames(src);
  for (const [key, value] of src) {
    const k = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(k)) continue;
    if (connectionHeaderNames.has(k)) continue;
    if (k === "host") continue;
    if (k === "content-length") continue;
    out.append(key, value);
  }
  return out;
}

function buildBackendUrl(pathname: string) {
  const upstreamUrl = new URL(getBackendBaseUrl());
  upstreamUrl.pathname = joinPaths(upstreamUrl.pathname, pathname);
  return upstreamUrl;
}

function createBackendInit(
  request: NextRequest,
  options: {
    method?: string;
    body?: BodyInit | null;
  } = {},
): RequestInit & { duplex?: "half" } {
  const init: RequestInit & { duplex?: "half" } = {
    method: options.method ?? request.method.toUpperCase(),
    headers: filterRequestHeaders(request.headers),
    redirect: "manual",
    cache: "no-store",
  };

  if (options.body != null) {
    init.body = options.body;
    init.duplex = "half";
  }

  return init;
}

function encodeSseData(data: string) {
  return `data: ${data}\n\n`;
}

function createUpdateStateEvent(snapshot: unknown) {
  return encodeSseData(
    JSON.stringify({
      type: "update-state",
      operations: [{ type: "set", path: [], value: snapshot }],
    }),
  );
}

async function* readSseEvents(stream: ReadableStream<Uint8Array>) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split(/\r?\n\r?\n/);
      buffer = events.pop() ?? "";

      for (const eventChunk of events) {
        yield eventChunk;
      }
    }

    if (buffer.trim()) {
      yield buffer;
    }
  } finally {
    reader.releaseLock();
  }
}

function getEventData(chunk: string) {
  const data = chunk
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");

  return data || null;
}

function asObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }

  return value as Record<string, unknown>;
}

async function fetchCanonicalState(request: NextRequest, threadId: string) {
  const response = await fetch(
    buildBackendUrl(`/assistant/threads/${threadId}/state`).toString(),
    createBackendInit(request, { method: "GET" }),
  );

  if (!response.ok) {
    throw response;
  }

  return response.json();
}

export async function POST(request: NextRequest) {
  const requestBody = await request.text();
  const upstreamResponse = await fetch(
    buildBackendUrl("/assistant").toString(),
    createBackendInit(request, { body: requestBody }),
  );

  if (!upstreamResponse.ok) {
    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
      headers: upstreamResponse.headers,
    });
  }

  const ack = (await upstreamResponse.json()) as AssistantRunAckResponse;
  const canonicalState = await fetchCanonicalState(request, ack.threadId);
  const encoder = new TextEncoder();

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      controller.enqueue(encoder.encode(createUpdateStateEvent(canonicalState)));

      if (typeof ack.activeRunId === "string" && ack.activeRunId.trim()) {
        const liveResponse = await fetch(
          buildBackendUrl(`/assistant/runs/${ack.activeRunId}/live`).toString(),
          createBackendInit(request, { method: "GET" }),
        );

        if (!liveResponse.ok) {
          throw new Error(`Live stream failed (${liveResponse.status})`);
        }

        if (liveResponse.body) {
          for await (const eventChunk of readSseEvents(liveResponse.body)) {
            const data = getEventData(eventChunk);
            if (!data) {
              continue;
            }

            const event = JSON.parse(data) as AssistantLiveEvent;
            if (event.type !== "snapshot" && event.type !== "delta") {
              continue;
            }

            const snapshot = asObject(event.snapshot);
            if (!snapshot) {
              continue;
            }

            controller.enqueue(encoder.encode(createUpdateStateEvent(snapshot)));
          }
        }
      }

      controller.enqueue(encoder.encode(encodeSseData("[DONE]")));
      controller.close();
    },
    cancel() {
      request.signal.throwIfAborted?.();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
