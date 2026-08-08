import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

import { buildBackendUrl, createBackendInit, passthroughResponse } from '@/lib/proxy/http'
import {
  ROUTE_NOT_PROXIED,
  ROUTE_NOT_PROXIED_STATUS,
  resolveProxyTarget,
} from '@/lib/proxy/routes'

// `duplex: 'half'` for a streaming request body is Node's, not the edge runtime's.
export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

type RouteContext = { params: Promise<{ path: string[] }> }

/**
 * The embed's same-origin door to the NOA API (§T.44, §I.embed).
 *
 * The browser calls `/api/*` on this origin; the request is forwarded server-side to
 * `NOA_API_URL`, carrying the `noa_session` cookie the registrable domain put here (V40). The
 * browser never reaches FastAPI directly (AGENTS.md), which is what lets the decision POST be a
 * same-origin `fetch` from inside the frame (V22, V80) with no CORS surface to configure.
 *
 * Only `GET` and `POST` are exported: every other method gets Next's own 405, so the method
 * restriction costs nothing to hold. Which paths are carried is `routes.ts`'s decision.
 */
async function proxy(request: NextRequest, ctx: RouteContext): Promise<Response> {
  const { path } = await ctx.params
  const segments = path ?? []

  const target = resolveProxyTarget(request.method, segments)
  if (!target.allowed) {
    // Refused here, before any upstream call. That ordering is the property: "this
    // origin does not carry the login route" (V42) is a fact about a request that
    // was never sent, not about a response that came back.
    return NextResponse.json(
      {
        error_code: ROUTE_NOT_PROXIED,
        message: 'This route is not available on the embed origin.',
      },
      { status: ROUTE_NOT_PROXIED_STATUS },
    )
  }

  const incomingUrl = new URL(request.url)
  const upstreamUrl = buildBackendUrl(target.path)
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

export function POST(request: NextRequest, ctx: RouteContext) {
  return proxy(request, ctx)
}
