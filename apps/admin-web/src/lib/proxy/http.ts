import type { NextRequest } from 'next/server'

/**
 * Same-origin proxy primitives (§T.50, §I.admin-web).
 *
 * Ported from `noa-old` branch `MCP`, `apps/web-bigsu/src/lib/proxy/http.ts`, per C13/V69: copy
 * the mature layer, do not rewrite it. Every guard below closes a defect that was found and fixed
 * there — an encoded path traversal, an open redirect attributable to this origin, the internal
 * backend host leaking to the browser, and a decompression mismatch that truncates responses.
 * V69 also says provenance is not evidence: `http.test.ts` re-proves each one against this copy
 * rather than citing the old repo.
 *
 * `apps/web-embed` carries the same file. That is a COPY, not a shared module: the two web apps
 * are independent packages with their own lockfiles, own CI and own deploy artifact, and
 * `eslint.config.mjs` refuses an import across that line. Duplication across the boundary is the
 * boundary working — the alternative is an alias AGENTS.md forbids.
 */

// Hop-by-hop headers are connection-scoped and must never be forwarded across
// the proxy boundary (RFC 7230 §6.1).
const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-connection',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

function getConnectionHeaderNames(headers: Headers): Set<string> {
  const value = headers.get('connection')
  const out = new Set<string>()
  if (!value) return out

  for (const raw of value.split(',')) {
    const token = raw.trim().toLowerCase()
    if (token) out.add(token)
  }

  return out
}

// Server-only upstream base. Browsers never see this — they call same-origin
// /api/* which this proxy forwards (AGENTS.md: "Browser ⊥ call FastAPI direct").
// There is no NEXT_PUBLIC_* fallback: NOA_API_URL is the single server-only
// source, and the repo-root `.env` is the single place local dev sets it
// (`next.config.ts` loads that file via `config/root-env.ts`).
export function getBackendBaseUrl(): string {
  const url = process.env.NOA_API_URL
  if (!url) {
    throw new Error(
      'Missing NOA_API_URL. Set NOA_API_URL to your backend base URL (server-only).',
    )
  }
  return url
}

// Split into path segments, dropping empty as well as "." / ".." segments so
// an encoded traversal (e.g. /api/%2e%2e/%2e%2e/admin) cannot resolve past the
// upstream base prefix once the joined path is assigned to URL.pathname.
function pathSegments(path: string): string[] {
  return path.split('/').filter((seg) => seg !== '' && seg !== '.' && seg !== '..')
}

function joinPaths(a: string, b: string): string {
  return '/' + [...pathSegments(a), ...pathSegments(b)].join('/')
}

export function buildBackendUrl(pathname: string): URL {
  const upstreamUrl = new URL(getBackendBaseUrl())
  const basePrefix = upstreamUrl.pathname.replace(/\/+$/, '')
  upstreamUrl.pathname = joinPaths(upstreamUrl.pathname, pathname)

  // Defense in depth: pathSegments strips literal "." / ".." from the RAW
  // string, but URL.pathname percent-decodes ("%2e%2e") and normalizes
  // backslashes ("x\..\..") AFTER that filter runs, so an encoded or backslash
  // traversal can still resolve past the base prefix once assigned above.
  // Re-check the fully-normalized path and refuse anything that escaped the
  // prefix rather than forward a request outside the intended /api surface.
  if (
    basePrefix &&
    upstreamUrl.pathname !== basePrefix &&
    !upstreamUrl.pathname.startsWith(basePrefix + '/')
  ) {
    throw new Error('Proxy path escaped the backend base prefix')
  }

  return upstreamUrl
}

export function filterRequestHeaders(src: Headers): Headers {
  const out = new Headers()
  const connectionHeaderNames = getConnectionHeaderNames(src)
  for (const [key, value] of src) {
    const k = key.toLowerCase()
    if (HOP_BY_HOP_HEADERS.has(k)) continue
    if (connectionHeaderNames.has(k)) continue
    if (k === 'host') continue
    if (k === 'content-length') continue
    // Departure from the `noa-old` port, and the same one `apps/web-embed` made.
    // Every surface this panel reaches is cookie-authenticated; bearer
    // tokens are MCP-only and are sent by LibreChat's server, never by a
    // browser. Dropping the header means the admin origin cannot be used to relay
    // one — including at `/api/mcp/`, which this proxy does carry.
    if (k === 'authorization') continue
    out.append(key, value)
  }
  return out
}

// Encodings undici/Node fetch transparently decompresses. Anything else (e.g.
// `zstd`, or a codec this undici build does not know) is forwarded still-encoded,
// so its Content-Encoding/Content-Length must be preserved.
const UNDICI_DECODED_ENCODINGS = new Set(['gzip', 'x-gzip', 'deflate', 'br'])

export function filterResponseHeaders(src: Headers): Headers {
  const out = new Headers()
  const connectionHeaderNames = getConnectionHeaderNames(src)
  // undici transparently decompresses gzip/deflate/br upstream bodies, so when
  // the body was decoded the bytes we re-emit are already plain. Drop the stale
  // encoding and its (now-wrong, compressed) Content-Length — otherwise the
  // browser tries to gunzip plain bytes (net::ERR_CONTENT_DECODING_FAILED) and
  // truncates. The platform recomputes the length or falls back to chunked.
  //
  // Only strip when EVERY listed encoding is one undici actually decodes: a
  // pass-through encoding (zstd, unknown) keeps its still-compressed body, so
  // stripping its header would relabel compressed bytes as identity and corrupt
  // the response silently (no ERR_CONTENT_DECODING_FAILED to signal it).
  const encoding = src.get('content-encoding')?.trim().toLowerCase()
  const decoded =
    !!encoding && encoding.split(',').every((part) => UNDICI_DECODED_ENCODINGS.has(part.trim()))
  for (const [key, value] of src) {
    const k = key.toLowerCase()
    if (HOP_BY_HOP_HEADERS.has(k)) continue
    if (connectionHeaderNames.has(k)) continue
    // Set-Cookie is copied separately to preserve multiple values.
    if (k === 'set-cookie') continue
    if (decoded && (k === 'content-encoding' || k === 'content-length')) continue
    out.append(key, value)
  }
  return out
}

// Copy every Set-Cookie value. `headers.get("set-cookie")` collapses multiple
// cookies into one comma-joined string, so prefer getSetCookie() when present.
// The attributes ride untouched: `Domain=.noa.internal` is what puts the session
// on the same registrable domain as the embed, and rewriting it here would
// silently unscope the cookie the whole panel depends on.
export function copySetCookies(from: Headers, to: Headers): void {
  const connectionHeaderNames = getConnectionHeaderNames(from)
  if (connectionHeaderNames.has('set-cookie')) return

  const withGetSetCookie = from as unknown as { getSetCookie?: () => string[] }
  if (typeof withGetSetCookie.getSetCookie === 'function') {
    for (const cookie of withGetSetCookie.getSetCookie()) {
      to.append('set-cookie', cookie)
    }
    return
  }

  const setCookie = from.get('set-cookie')
  if (setCookie) to.append('set-cookie', setCookie)
}

export type BackendInit = RequestInit & { duplex?: 'half' }

export function createBackendInit(
  request: NextRequest | Request,
  options: { method?: string; body?: BodyInit | null } = {},
): BackendInit {
  const init: BackendInit = {
    method: options.method ?? request.method.toUpperCase(),
    headers: filterRequestHeaders(request.headers),
    redirect: 'manual',
    cache: 'no-store',
  }

  if (options.body != null) {
    init.body = options.body
    init.duplex = 'half'
  }

  return init
}

// Rewrite a redirect Location that targets the internal backend (NOA_API_URL
// origin + base path) into the app-relative /api/... path the browser can
// follow same-origin. Relative Locations resolve against the upstream request
// URL. External redirects (different origin, or outside the base prefix) pass
// through untouched.
export function rewriteLocationHeader(location: string, requestUrl: string): string {
  const base = new URL(getBackendBaseUrl())
  let target: URL
  try {
    target = new URL(location, requestUrl || base)
  } catch {
    return location
  }

  // Off-origin: the proxy forwards it, but as the fully-resolved absolute URL.
  // Returning the raw value would let a protocol-relative ("//evil.com") or
  // backslash-obfuscated ("/\\evil.com") Location be re-interpreted against the
  // APP origin by the browser — an open redirect attributable to the origin an
  // operator signs in to. `target.href` is the unambiguous absolute form the
  // backend actually meant.
  if (target.origin !== base.origin) return target.href

  const basePrefix = base.pathname.replace(/\/+$/, '')
  if (
    basePrefix &&
    target.pathname !== basePrefix &&
    !target.pathname.startsWith(basePrefix + '/')
  ) {
    // Internal origin but outside the exposed base prefix: there is no /api
    // mapping and the browser cannot reach the internal host anyway, so strip
    // the origin to a root-relative path rather than leak the NOA_API_URL host.
    return `${target.pathname}${target.search}${target.hash}`
  }

  const rest = basePrefix ? target.pathname.slice(basePrefix.length) : target.pathname
  return `/api${rest}${target.search}${target.hash}`
}

// Scrub any occurrence of the internal backend origin from a header value that
// embeds a URL but is not a bare Location (Refresh: "N; url=<abs>", Link:
// "<abs>; rel=..."). Maps origin+basePrefix -> /api and a bare origin -> root so
// the NOA_API_URL host never reaches the browser (and a Refresh cannot navigate
// it there).
function scrubBackendOriginHeader(headers: Headers, name: string): void {
  const value = headers.get(name)
  if (!value) return
  const base = new URL(getBackendBaseUrl())
  if (!value.includes(base.origin)) return
  const basePrefix = base.pathname.replace(/\/+$/, '')
  const scrubbed = value
    .split(base.origin + basePrefix)
    .join('/api')
    .split(base.origin)
    .join('')
  headers.set(name, scrubbed)
}

// Re-emit an upstream response: status, statusText, safe headers, and every
// Set-Cookie, preserving the streaming/binary body untouched. Any header that
// carries the internal backend origin (Location, Content-Location, Refresh,
// Link) is rewritten so the NOA_API_URL host is never leaked to the browser.
//
// The status is passed through as-is on purpose: a 401 has to arrive at the
// browser as a 401 so `fetchWithAuth` runs the session-expiry flow (V6's
// per-request `is_active` re-read is what makes that 401 meaningful), and
// `x-request-id` rides along with the rest of the safe headers so the id in the
// body still names a log line.
export function passthroughResponse(upstream: Response): Response {
  const headers = filterResponseHeaders(upstream.headers)
  copySetCookies(upstream.headers, headers)

  // redirect:'manual' surfaces the real 3xx on current Node; Location (and the
  // informational Content-Location) point at the internal backend host, so
  // rewrite them to a same-origin /api/... path rather than leak that host.
  for (const name of ['location', 'content-location']) {
    const value = headers.get(name)
    if (value) headers.set(name, rewriteLocationHeader(value, upstream.url))
  }
  scrubBackendOriginHeader(headers, 'refresh')
  scrubBackendOriginHeader(headers, 'link')

  // Some undici versions instead return a spec opaque-redirect (status 0);
  // new Response() rejects status 0 with a RangeError, so fall back to 502
  // rather than 500 the whole request.
  const status = upstream.status === 0 ? 502 : upstream.status

  return new Response(upstream.body, {
    status,
    statusText: upstream.statusText,
    headers,
  })
}
