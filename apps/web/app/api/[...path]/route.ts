import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

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
      "Missing NOA_API_URL (temporary fallback: NEXT_PUBLIC_API_URL)"
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

function filterResponseHeaders(src: Headers) {
  const out = new Headers();
  const connectionHeaderNames = getConnectionHeaderNames(src);
  for (const [key, value] of src) {
    const k = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(k)) continue;
    if (connectionHeaderNames.has(k)) continue;
    if (k === "set-cookie") continue;
    out.append(key, value);
  }
  return out;
}

async function proxy(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
) {
  const baseUrl = getBackendBaseUrl();
  const incomingUrl = new URL(request.url);

  const { path } = await ctx.params;

  const upstreamUrl = new URL(baseUrl);
  upstreamUrl.pathname = joinPaths(
    upstreamUrl.pathname,
    (path ?? []).join("/")
  );
  upstreamUrl.search = incomingUrl.search;

  const method = request.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD";

  const init: (RequestInit & { duplex?: "half" }) = {
    method,
    headers: filterRequestHeaders(request.headers),
    redirect: "manual",
    cache: "no-store",
  };

  if (hasBody) {
    init.body = request.body;
    init.duplex = "half";
  }

  const upstreamResponse = await fetch(upstreamUrl.toString(), init);

  const responseHeaders = filterResponseHeaders(upstreamResponse.headers);
  const responseConnectionHeaderNames = getConnectionHeaderNames(
    upstreamResponse.headers
  );
  const getSetCookie = (upstreamResponse.headers as unknown as {
    getSetCookie?: () => string[];
  }).getSetCookie;

  if (!responseConnectionHeaderNames.has("set-cookie")) {
    if (getSetCookie) {
      for (const cookie of getSetCookie()) {
        responseHeaders.append("set-cookie", cookie);
      }
    } else {
      const setCookie = upstreamResponse.headers.get("set-cookie");
      if (setCookie) responseHeaders.append("set-cookie", setCookie);
    }
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: responseHeaders,
  });
}

export function GET(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, ctx);
}

export function POST(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, ctx);
}

export function PUT(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, ctx);
}

export function PATCH(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, ctx);
}

export function DELETE(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> }
) {
  return proxy(request, ctx);
}
