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

type ProxyEnv = Record<string, string | undefined>;

type ProxyContext = {
  path: string[];
};

type ProxyFn = typeof fetch;

export function getConnectionHeaderNames(headers: Headers) {
  const value = headers.get("connection");
  const out = new Set<string>();

  if (!value) {
    return out;
  }

  for (const raw of value.split(",")) {
    const token = raw.trim().toLowerCase();
    if (token) {
      out.add(token);
    }
  }

  return out;
}

export function joinPaths(a: string, b: string) {
  const aSeg = a.split("/").filter(Boolean);
  const bSeg = b.split("/").filter(Boolean);
  return `/${[...aSeg, ...bSeg].join("/")}`;
}

export function getBackendBaseUrl(env: ProxyEnv = process.env) {
  const url = env.NOA_API_URL ?? env.NEXT_PUBLIC_API_URL;
  if (!url) {
    throw new Error(
      "Missing NOA_API_URL. Set NOA_API_URL to your backend base URL (NEXT_PUBLIC_API_URL is a legacy fallback).",
    );
  }
  return url;
}

export function filterRequestHeaders(src: Headers) {
  const out = new Headers();
  const connectionHeaderNames = getConnectionHeaderNames(src);

  for (const [key, value] of src) {
    const normalized = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(normalized)) continue;
    if (connectionHeaderNames.has(normalized)) continue;
    if (normalized === "host" || normalized === "content-length") continue;
    out.append(key, value);
  }

  return out;
}

export function filterResponseHeaders(src: Headers) {
  const out = new Headers();
  const connectionHeaderNames = getConnectionHeaderNames(src);

  for (const [key, value] of src) {
    const normalized = key.toLowerCase();
    if (HOP_BY_HOP_HEADERS.has(normalized)) continue;
    if (connectionHeaderNames.has(normalized)) continue;
    out.append(key, value);
  }

  return out;
}

export async function proxyRequest(
  request: Request,
  context: ProxyContext,
  env: ProxyEnv = process.env,
  fetchImpl: ProxyFn = fetch,
) {
  const incomingUrl = new URL(request.url);
  const upstreamUrl = new URL(getBackendBaseUrl(env));
  upstreamUrl.pathname = joinPaths(upstreamUrl.pathname, context.path.join("/"));
  upstreamUrl.search = incomingUrl.search;

  const method = request.method.toUpperCase();
  const init: RequestInit & { duplex?: "half" } = {
    method,
    headers: filterRequestHeaders(request.headers),
    redirect: "manual",
    cache: "no-store",
  };

  if (method !== "GET" && method !== "HEAD" && request.body) {
    init.body = request.body;
    init.duplex = "half";
  }

  const upstreamResponse = await fetchImpl(upstreamUrl.toString(), init);
  const headers = filterResponseHeaders(upstreamResponse.headers);

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers,
  });
}
