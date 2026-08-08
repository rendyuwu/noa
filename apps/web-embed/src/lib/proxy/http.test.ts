import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  buildBackendUrl,
  copySetCookies,
  filterRequestHeaders,
  filterResponseHeaders,
  passthroughResponse,
  rewriteLocationHeader,
} from './http'

/**
 * The proxy primitives, proven against this copy (§T.44).
 *
 * Every guard here was ported from `noa-old` (C13). V69 is why the tests came with it: upstream
 * provenance is not evidence that a control works — B2 shipped an inert host-key pin precisely
 * because the port trusted the source. So each case below exercises the mechanism in this file,
 * not the one it was copied from.
 */

describe('proxy/http', () => {
  const original = process.env.NOA_API_URL

  beforeEach(() => {
    // A base with a path prefix so prefix-preservation assertions are meaningful.
    process.env.NOA_API_URL = 'http://backend.test/v1'
  })

  afterEach(() => {
    if (original === undefined) delete process.env.NOA_API_URL
    else process.env.NOA_API_URL = original
  })

  describe('filterRequestHeaders — what the browser sends onward', () => {
    it('forwards the browser Cookie header upstream unchanged', () => {
      // V40: the session rides on `noa_session`, scoped to the registrable domain
      // so it reaches this app as well as the admin one. If the proxy dropped or
      // rewrote it, every call from the card would authenticate as nobody and the
      // whole decision path (V22) would answer 401.
      const out = filterRequestHeaders(
        new Headers({ cookie: 'noa_session=abc.def.ghi; other=1' }),
      )

      expect(out.get('cookie')).toBe('noa_session=abc.def.ghi; other=1')
    })

    it('drops the Authorization header — this origin does not relay bearer tokens', () => {
      // MCP tokens are LibreChat's to send (C5) and never a browser's. Forwarding
      // the header would make the embed origin a relay for one.
      const out = filterRequestHeaders(
        new Headers({ authorization: 'Bearer noa_live_token', cookie: 'noa_session=x' }),
      )

      expect(out.get('authorization')).toBeNull()
      expect(out.get('cookie')).toBe('noa_session=x')
    })

    it('strips hop-by-hop headers, the Host header and a stale Content-Length', () => {
      const out = filterRequestHeaders(
        new Headers({
          host: 'embed.noa.internal',
          connection: 'keep-alive, x-custom',
          'keep-alive': 'timeout=5',
          'content-length': '19',
          'x-custom': 'named by connection, so hop-by-hop too',
          'content-type': 'application/json',
        }),
      )

      expect(out.get('host')).toBeNull()
      expect(out.get('connection')).toBeNull()
      expect(out.get('keep-alive')).toBeNull()
      expect(out.get('content-length')).toBeNull()
      expect(out.get('x-custom')).toBeNull()
      expect(out.get('content-type')).toBe('application/json')
    })
  })

  describe('copySetCookies — the session coming back', () => {
    it('preserves every Set-Cookie value with its Domain attribute intact', () => {
      // The `Domain=.noa.internal` scoping is V40 itself. A proxy that re-emitted
      // the cookie without it would confine the session to whichever origin
      // happened to answer, and the admin app would stop seeing the same login.
      const from = new Headers([
        ['set-cookie', 'noa_session=abc; Domain=.noa.internal; Path=/; HttpOnly; SameSite=Lax'],
        ['set-cookie', 'other=1; Path=/'],
      ])
      const to = new Headers()

      copySetCookies(from, to)

      expect(to.getSetCookie()).toEqual([
        'noa_session=abc; Domain=.noa.internal; Path=/; HttpOnly; SameSite=Lax',
        'other=1; Path=/',
      ])
    })
  })

  describe('filterResponseHeaders (decoded encoding/length)', () => {
    it('drops stale content-encoding and content-length when the body was decoded', () => {
      const out = filterResponseHeaders(
        new Headers({
          'content-encoding': 'gzip',
          'content-length': '42',
          'content-type': 'application/json',
        }),
      )

      expect(out.get('content-encoding')).toBeNull()
      expect(out.get('content-length')).toBeNull()
      expect(out.get('content-type')).toBe('application/json')
    })

    it('preserves content-length when the upstream sent no content-encoding', () => {
      const out = filterResponseHeaders(
        new Headers({ 'content-length': '42', 'content-type': 'application/json' }),
      )

      expect(out.get('content-length')).toBe('42')
      expect(out.get('content-type')).toBe('application/json')
    })

    it('preserves content-encoding/length for an encoding undici does not decode (zstd)', () => {
      // undici only transparently decompresses gzip/deflate/br. A zstd body is
      // forwarded still-compressed, so stripping its header would relabel
      // compressed bytes as identity and corrupt the response silently.
      const out = filterResponseHeaders(
        new Headers({ 'content-encoding': 'zstd', 'content-length': '42' }),
      )

      expect(out.get('content-encoding')).toBe('zstd')
      expect(out.get('content-length')).toBe('42')
    })
  })

  describe('rewriteLocationHeader (redirect Location)', () => {
    const requestUrl = 'http://backend.test/v1/some/path'

    it('rewrites an absolute internal Location to /api/..., preserving query and fragment', () => {
      expect(rewriteLocationHeader('http://backend.test/v1/foo?x=1#frag', requestUrl)).toBe(
        '/api/foo?x=1#frag',
      )
    })

    it('resolves a relative Location against the upstream request URL then rewrites', () => {
      expect(rewriteLocationHeader('/v1/foo/bar?x=1', requestUrl)).toBe('/api/foo/bar?x=1')
    })

    it('forwards an external-origin Location as its resolved absolute URL', () => {
      expect(rewriteLocationHeader('https://accounts.example.com/o/oauth', requestUrl)).toBe(
        'https://accounts.example.com/o/oauth',
      )
    })

    it('resolves a protocol-relative Location to an absolute URL (no app-origin open redirect)', () => {
      // "//evil.example" must NOT be returned raw: on an app-origin response the
      // browser resolves it against the app origin -> open redirect, attributable
      // to the one NOA origin LibreChat is allowed to frame (V41).
      expect(rewriteLocationHeader('//evil.example/x', requestUrl)).toBe('http://evil.example/x')
    })

    it('strips the internal host from a same-origin Location that is off the base prefix', () => {
      // Same backend origin but outside the exposed /v1 prefix: there is no /api
      // mapping, and the browser cannot reach the internal host anyway. Reduce it
      // to a root-relative path rather than leak NOA_API_URL.
      expect(rewriteLocationHeader('http://backend.test/other', requestUrl)).toBe('/other')
    })
  })

  describe('buildBackendUrl — a traversal cannot escape the base prefix', () => {
    it('strips ../ dot-segments so a traversal stays under the base prefix', () => {
      expect(buildBackendUrl('../../admin').toString()).toBe('http://backend.test/v1/admin')
    })

    it('strips encoded traversal segments joined from the catch-all route', () => {
      // /api/%2e%2e/%2e%2e/admin decodes to segments ['..','..','admin'], which
      // the route joins into this string before building the upstream URL.
      expect(buildBackendUrl(['..', '..', 'admin'].join('/')).toString()).toBe(
        'http://backend.test/v1/admin',
      )
    })

    it('throws when a backslash traversal segment escapes the base prefix', () => {
      // URL.pathname normalizes "\" -> "/" and resolves ".." AFTER the raw-string
      // segment filter runs, so this single segment escapes /v1 once assigned.
      expect(() => buildBackendUrl('x\\..\\..\\..\\admin')).toThrow(/base prefix/)
    })

    it('leaves a normal nested path unchanged under the base prefix', () => {
      expect(buildBackendUrl('action-requests/42/approve').toString()).toBe(
        'http://backend.test/v1/action-requests/42/approve',
      )
    })

    it('refuses to build a URL when NOA_API_URL is unset (server-only, no fallback)', () => {
      delete process.env.NOA_API_URL

      expect(() => buildBackendUrl('auth/me')).toThrow(/NOA_API_URL/)
    })
  })

  describe('passthroughResponse — host-bearing headers and odd statuses', () => {
    it('scrubs the internal backend origin from a Refresh header', () => {
      const upstream = {
        headers: new Headers({ refresh: '0; url=http://backend.test/v1/moved' }),
        status: 200,
        statusText: 'OK',
        url: 'http://backend.test/v1/some/path',
        body: null,
      } as unknown as Response

      expect(passthroughResponse(upstream).headers.get('refresh')).toBe('0; url=/api/moved')
    })

    it('scrubs the internal backend origin from a Link header', () => {
      const upstream = {
        headers: new Headers({ link: '<http://backend.test/v1/next>; rel="next"' }),
        status: 200,
        statusText: 'OK',
        url: 'http://backend.test/v1/some/path',
        body: null,
      } as unknown as Response

      expect(passthroughResponse(upstream).headers.get('link')).toBe('</api/next>; rel="next"')
    })

    it('maps an upstream status 0 to 502 without throwing, still rewriting Location', () => {
      // undici can surface a spec opaque-redirect as status 0; new Response()
      // rejects status 0, so passthrough must remap it. A real Response cannot be
      // constructed with status 0, so mock the shape passthrough reads.
      const upstream = {
        headers: new Headers({ location: 'http://backend.test/v1/moved' }),
        status: 0,
        statusText: '',
        url: 'http://backend.test/v1/some/path',
        body: null,
      } as unknown as Response

      const res = passthroughResponse(upstream)

      expect(res.status).toBe(502)
      expect(res.headers.get('location')).toBe('/api/moved')
    })
  })
})
