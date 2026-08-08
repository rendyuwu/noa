import type { NextRequest } from 'next/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { GET, POST, dynamic, runtime } from './route'

/**
 * The proxy route end to end, with `fetch` stubbed so every assertion is about what this app
 * sends and re-emits (§T.44).
 */

const ID = '9f1c2b7e-0000-4000-8000-000000000000'

const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) })
const asNext = (request: Request) => request as unknown as NextRequest

describe('/api/[...path] — the embed’s same-origin door', () => {
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

  it('runs on Node and is never cached', () => {
    // `duplex: 'half'` needs the Node runtime, and a cached proxy answer would
    // serve one operator's session state to the next request.
    expect(runtime).toBe('nodejs')
    expect(dynamic).toBe('force-dynamic')
  })

  it('preserves the method, upstream path and query string', async () => {
    const calls: Array<{ url: string; method?: string }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      calls.push({ url: String(input), method: init?.method })
      return new Response('ok', { status: 200 })
    })

    await GET(
      asNext(new Request('http://embed.test/api/action-requests/' + ID + '?poll=1')),
      ctx(['action-requests', ID]),
    )

    expect(calls[0]?.method).toBe('GET')
    expect(calls[0]?.url).toBe(`http://backend.test/action-requests/${ID}?poll=1`)
  })

  it('carries the browser’s session cookie upstream', async () => {
    // V40. Without this the card authenticates as nobody and every decision POST
    // answers 401 — the failure the whole proxy exists to prevent.
    let seen: Headers | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      seen = new Headers(init?.headers)
      return new Response(null, { status: 200 })
    })

    await GET(
      asNext(
        new Request('http://embed.test/api/auth/me', {
          headers: { cookie: 'noa_session=abc.def.ghi' },
        }),
      ),
      ctx(['auth', 'me']),
    )

    expect(seen?.get('cookie')).toBe('noa_session=abc.def.ghi')
  })

  it('re-emits every Set-Cookie value with its Domain attribute intact', async () => {
    // The `Domain=.noa.internal` scoping is what puts the session on the same
    // registrable domain as the admin app (V40); a proxy that dropped or rewrote
    // it would quietly confine the login to whichever origin answered.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, {
        status: 200,
        headers: [
          ['set-cookie', 'noa_session=abc; Domain=.noa.internal; Path=/; HttpOnly; SameSite=Lax'],
          ['set-cookie', 'other=1; Path=/'],
        ],
      }),
    )

    const res = await GET(asNext(new Request('http://embed.test/api/auth/me')), ctx(['auth', 'me']))

    expect(res.headers.getSetCookie()).toEqual([
      'noa_session=abc; Domain=.noa.internal; Path=/; HttpOnly; SameSite=Lax',
      'other=1; Path=/',
    ])
  })

  it('refuses to proxy the login route and never calls upstream', async () => {
    // V42: no login page, no LDAP form, no credential handling on this origin. The
    // `fetch` spy is the assertion that matters — a 404 produced upstream would
    // look identical from the outside and mean the opposite.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null))

    const res = await POST(
      asNext(new Request('http://embed.test/api/auth/login', { method: 'POST', body: '{}' })),
      ctx(['auth', 'login']),
    )

    expect(res.status).toBe(404)
    await expect(res.json()).resolves.toEqual({
      error_code: 'route_not_proxied',
      message: 'This route is not available on the embed origin.',
    })
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('refuses the admin surface the frameable origin must not reach', async () => {
    // V41: the admin app answers `frame-ancestors 'none'`. Proxying `/admin/*` here
    // would hand that surface back to anything inside the LibreChat frame.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null))

    const res = await GET(
      asNext(new Request('http://embed.test/api/admin/users')),
      ctx(['admin', 'users']),
    )

    expect(res.status).toBe(404)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('separates — an allowed route does reach upstream', async () => {
    // Without this the two refusal cases above pass just as well against a proxy
    // that forwards nothing at all (V87's shape).
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null))

    await POST(
      asNext(
        new Request(`http://embed.test/api/action-requests/${ID}/approve`, {
          method: 'POST',
          body: '{"reason":"typed by the operator","csrf":"v1.0.sig"}',
        }),
      ),
      ctx(['action-requests', ID, 'approve']),
    )

    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('forwards the decision body with duplex half and follows no redirect', async () => {
    let captured: (RequestInit & { duplex?: string }) | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      captured = init as RequestInit & { duplex?: string }
      return new Response(null, { status: 202 })
    })

    await POST(
      asNext(
        new Request(`http://embed.test/api/action-requests/${ID}/deny`, {
          method: 'POST',
          body: '{"reason":"not now","csrf":"v1.0.sig"}',
        }),
      ),
      ctx(['action-requests', ID, 'deny']),
    )

    expect(captured?.body).not.toBeNull()
    expect(captured?.duplex).toBe('half')
    expect(captured?.redirect).toBe('manual')
    expect(captured?.cache).toBe('no-store')
  })

  it('attaches no body to a GET', async () => {
    let captured: RequestInit | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      captured = init
      return new Response('ok', { status: 200 })
    })

    await GET(asNext(new Request('http://embed.test/api/auth/me')), ctx(['auth', 'me']))

    expect(captured?.body ?? null).toBeNull()
  })

  it('preserves x-request-id from the API response and strips hop-by-hop headers', async () => {
    // V73: the id in the body names a log line, and it only does that if the hop
    // does not eat the header.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{"status":"pending"}', {
        status: 200,
        headers: {
          'x-request-id': 'req-abc',
          'content-type': 'application/json',
          connection: 'keep-alive',
          'transfer-encoding': 'chunked',
        },
      }),
    )

    const res = await GET(
      asNext(new Request(`http://embed.test/api/action-requests/${ID}`)),
      ctx(['action-requests', ID]),
    )

    expect(res.headers.get('x-request-id')).toBe('req-abc')
    expect(res.headers.get('connection')).toBeNull()
    expect(res.headers.get('transfer-encoding')).toBeNull()
  })

  it('passes an upstream 401 through with its status and body', async () => {
    // V38: the card renders "cannot authenticate here" off this status. A proxy
    // that redirected to a login, or flattened the status, would leave a blank card
    // with a live Approve button.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{"error_code":"not_authenticated","message":"no session","request_id":"r1"}', {
        status: 401,
        headers: { 'content-type': 'application/json' },
      }),
    )

    const res = await GET(asNext(new Request('http://embed.test/api/auth/me')), ctx(['auth', 'me']))

    expect(res.status).toBe(401)
    await expect(res.json()).resolves.toMatchObject({ error_code: 'not_authenticated' })
  })

  it.each([403, 404, 409, 422, 500])('passes an upstream %i through unchanged', async (status) => {
    // 403 csrf_token_invalid, 409 change_reason_required / already_decided / expired
    // (V15, V28, V32) all have to arrive at the card as themselves.
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error_code: 'x' }), { status }),
    )

    const res = await POST(
      asNext(
        new Request(`http://embed.test/api/action-requests/${ID}/approve`, {
          method: 'POST',
          body: '{}',
        }),
      ),
      ctx(['action-requests', ID, 'approve']),
    )

    expect(res.status).toBe(status)
  })

  it('throws when NOA_API_URL is unset — no fallback, server-only', async () => {
    delete process.env.NOA_API_URL

    await expect(
      GET(asNext(new Request('http://embed.test/api/auth/me')), ctx(['auth', 'me'])),
    ).rejects.toThrow(/NOA_API_URL/)
  })
})
