import type { NextRequest } from 'next/server'

import { buildBackendUrl, createBackendInit, passthroughResponse } from '@/lib/proxy/http'

// `duplex: 'half'` for a streaming request body is Node's, not the edge runtime's.
export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

type RouteContext = { params: Promise<{ path: string[] }> }

/**
 * The admin panel's same-origin door to the NOA API (§T.50, §I.admin-web).
 *
 * The browser calls `/api/*` on this origin; the request is forwarded server-side to
 * `NOA_API_URL`, carrying the `noa_session` cookie the registrable domain put here. The
 * browser never reaches FastAPI directly (AGENTS.md), so there is no CORS surface to configure and
 * no second place the API's origin is written down.
 *
 * **Pass-through, not an allowlist — and that difference is deliberate.** `apps/web-embed` carries
 * a four-entry allowlist because the embed is the one NOA origin LibreChat may frame: a
 * pass-through there would put `/auth/login` and `/admin/*` inside that frame with the operator's
 * cookie. This app answers `frame-ancestors 'none'` (§T.49, V41, proven on the wire in
 * `tests/framing-live.server.test.ts`), so no document that is not this app can drive it, and the
 * surface it needs is `§I.admin-api` in full — users, roles, tokens, three server verticals, audit,
 * plus `/auth/*` and `/me/mcp-tokens`. An allowlist would have to be edited by §T.51–§T.55 to stay
 * correct, and a stale entry there fails as a 404 the panel cannot explain. `noa-old` shipped this
 * same pass-through for the same app.
 *
 * What keeps that width safe lives in `http.ts` and is tested there: `Authorization` is dropped
 * outbound, so `/api/mcp/` is reachable but inert; a traversal cannot escape the base prefix;
 * and no response header carries the internal backend host back to the browser.
 *
 * Every method §I.admin-api uses is exported, because a catch-all that omitted one would answer
 * Next's own 405 and read as "the API refused" instead of "this origin does not carry PATCH".
 */
async function proxy(request: NextRequest, ctx: RouteContext): Promise<Response> {
  const incomingUrl = new URL(request.url)
  const { path } = await ctx.params

  const upstreamUrl = buildBackendUrl((path ?? []).join('/'))
  upstreamUrl.search = incomingUrl.search

  const method = request.method.toUpperCase()
  const canHaveBody = method !== 'GET' && method !== 'HEAD'

  const init = createBackendInit(request, {
    method,
    body: canHaveBody ? request.body : null,
  })

  const upstreamResponse = await fetch(upstreamUrl.toString(), init)
  return passthroughResponse(upstreamResponse)
}

export function GET(request: NextRequest, ctx: RouteContext) {
  return proxy(request, ctx)
}

export function HEAD(request: NextRequest, ctx: RouteContext) {
  return proxy(request, ctx)
}

export function OPTIONS(request: NextRequest, ctx: RouteContext) {
  return proxy(request, ctx)
}

export function POST(request: NextRequest, ctx: RouteContext) {
  return proxy(request, ctx)
}

export function PUT(request: NextRequest, ctx: RouteContext) {
  return proxy(request, ctx)
}

export function PATCH(request: NextRequest, ctx: RouteContext) {
  return proxy(request, ctx)
}

export function DELETE(request: NextRequest, ctx: RouteContext) {
  return proxy(request, ctx)
}
