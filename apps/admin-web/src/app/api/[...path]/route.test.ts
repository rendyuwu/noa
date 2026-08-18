import type { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT } from './route'

/**
 * The one door (§T.50, §I.admin-web).
 *
 * Ported from `noa-old` with its tests (C13, V69). What is asserted here is the hop itself: the
 * method, the upstream path, the query, the body, and the response coming back unaltered — plus
 * the two properties that make a pass-through proxy safe on this origin, which is where the shape
 * differs from the embed's allowlist (§T.44).
 */

const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) })
const asNext = (request: Request) => request as unknown as NextRequest

describe('/api/[...path] — the admin panel’s same-origin proxy', () => {
  const original = process.env.NOA_API_URL

  beforeEach(() => {
    process.env.NOA_API_URL = 'http://backend.test'
    vi.restoreAllMocks()
  })

  afterEach(() => {
    if (original === undefined) delete process.env.NOA_API_URL
    else process.env.NOA_API_URL = original
    vi.restoreAllMocks()
  })

  it('preserves the method, upstream path and query string', async () => {
    const calls: Array<{ url: string; method?: string }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      calls.push({ url: String(input), method: init?.method })
      return new Response('ok', { status: 200 })
    })

    const request = new Request('http://app.test/api/admin/audit/tool-runs?status=FAILED&limit=25')
    await GET(asNext(request), ctx(['admin', 'audit', 'tool-runs']))

    expect(calls[0]?.method).toBe('GET')
    expect(calls[0]?.url).toBe('http://backend.test/admin/audit/tool-runs?status=FAILED&limit=25')
  })

  it('carries every method §I.admin-api uses', async () => {
    // PATCH, PUT and DELETE are the ones the embed's proxy never needed. Omitting one here would
    // answer Next's own 405, which the panel would surface as "the API refused" rather than "this
    // origin does not carry that method".
    const seen: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      seen.push(init?.method ?? '')
      return new Response(null, { status: 204 })
    })

    const handlers = [
      [GET, 'GET'],
      [HEAD, 'HEAD'],
      [OPTIONS, 'OPTIONS'],
      [POST, 'POST'],
      [PUT, 'PUT'],
      [PATCH, 'PATCH'],
      [DELETE, 'DELETE'],
    ] as const

    for (const [handler, method] of handlers) {
      const request = new Request('http://app.test/api/admin/users/42', {
        method,
        ...(method === 'GET' || method === 'HEAD' ? {} : { body: 'payload' }),
      })
      await handler(asNext(request), ctx(['admin', 'users', '42']))
    }

    expect(seen).toEqual(['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE'])
  })

  it('forwards the session cookie and never an Authorization header (V40, C5)', async () => {
    // The pass-through does carry `/api/mcp/`, so the Authorization drop is what makes that
    // reachability inert: an MCP bearer is LibreChat's to send, never a browser's (C5). The cookie
    // assertion beside it is the negative control — a proxy that forwarded no credential at all
    // would satisfy the absence and authenticate as nobody.
    let headers: Headers | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      headers = new Headers(init?.headers)
      return new Response(null, { status: 401 })
    })

    const request = new Request('http://app.test/api/mcp/', {
      method: 'POST',
      body: '{"method":"tools/list"}',
      headers: {
        authorization: 'Bearer noa_live_token',
        cookie: 'noa_session=abc.def.ghi',
      },
    })
    await POST(asNext(request), ctx(['mcp']))

    expect(headers?.get('authorization')).toBeNull()
    expect(headers?.get('cookie')).toBe('noa_session=abc.def.ghi')
  })

  it('forwards a streaming/binary request body with duplex half', async () => {
    let capturedInit: (RequestInit & { duplex?: string }) | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedInit = init as RequestInit & { duplex?: string }
      return new Response(null, { status: 200 })
    })

    const request = new Request('http://app.test/api/auth/login', {
      method: 'POST',
      body: new Uint8Array([1, 2, 3, 4]),
      // @ts-expect-error duplex is required by Node for a stream body.
      duplex: 'half',
    })
    await POST(asNext(request), ctx(['auth', 'login']))

    expect(capturedInit?.body).not.toBeNull()
    expect(capturedInit?.duplex).toBe('half')
    expect(capturedInit?.redirect).toBe('manual')
  })

  it('does not attach a body to a GET', async () => {
    let capturedInit: RequestInit | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      capturedInit = init
      return new Response('ok', { status: 200 })
    })

    await GET(asNext(new Request('http://app.test/api/auth/me')), ctx(['auth', 'me']))

    expect(capturedInit?.body ?? null).toBeNull()
  })

  it('preserves the status and the request id, and strips hop-by-hop headers (V73)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('body', {
        status: 201,
        statusText: 'Created',
        headers: {
          'x-request-id': 'req-abc',
          'content-type': 'application/json',
          connection: 'keep-alive',
          'transfer-encoding': 'chunked',
        },
      }),
    )

    const res = await POST(
      asNext(new Request('http://app.test/api/admin/roles', { method: 'POST' })),
      ctx(['admin', 'roles']),
    )

    expect(res.status).toBe(201)
    expect(res.headers.get('x-request-id')).toBe('req-abc')
    expect(res.headers.get('connection')).toBeNull()
    expect(res.headers.get('transfer-encoding')).toBeNull()
  })

  it('does not follow a redirect, and rewrites its Location to this origin', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 302, headers: { location: 'http://backend.test/moved' } }),
    )

    const res = await GET(asNext(new Request('http://app.test/api/x')), ctx(['x']))

    expect(res.status).toBe(302)
    expect(res.headers.get('location')).toBe('/api/moved')
  })

  it('preserves every Set-Cookie value — the login and the logout clear alike (V40, V6)', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, {
        status: 200,
        headers: [
          ['set-cookie', 'noa_session=abc; Domain=.noa.internal; Path=/; HttpOnly; SameSite=Lax'],
          ['set-cookie', 'other=1; Path=/'],
        ],
      }),
    )

    const res = await POST(
      asNext(new Request('http://app.test/api/auth/login', { method: 'POST', body: '{}' })),
      ctx(['auth', 'login']),
    )
    const cookies = (res.headers as unknown as { getSetCookie: () => string[] }).getSetCookie()

    expect(cookies).toContain(
      'noa_session=abc; Domain=.noa.internal; Path=/; HttpOnly; SameSite=Lax',
    )
    expect(cookies).toContain('other=1; Path=/')
  })

  it('passes through a 204 with an empty body', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))

    const res = await DELETE(
      asNext(new Request('http://app.test/api/admin/users/42', { method: 'DELETE' })),
      ctx(['admin', 'users', '42']),
    )

    expect(res.status).toBe(204)
    await expect(res.text()).resolves.toBe('')
  })

  it.each([401, 403, 409, 500])('passes upstream %i through unchanged', async (status) => {
    // 401 in particular: `fetchWithAuth` keys the session-expiry flow off it, so a proxy that
    // normalised statuses would turn an expired session into an unexplained error state.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error_code: 'nope' }), { status }),
    )

    const res = await GET(asNext(new Request('http://app.test/api/auth/me')), ctx(['auth', 'me']))

    expect(res.status).toBe(status)
  })

  it('refuses to run without NOA_API_URL — there is no NEXT_PUBLIC_* fallback', async () => {
    delete process.env.NOA_API_URL
    process.env.NEXT_PUBLIC_API_URL = 'http://legacy.test'

    await expect(
      GET(asNext(new Request('http://app.test/api/auth/me')), ctx(['auth', 'me'])),
    ).rejects.toThrow(/NOA_API_URL/)

    delete process.env.NEXT_PUBLIC_API_URL
  })
})
