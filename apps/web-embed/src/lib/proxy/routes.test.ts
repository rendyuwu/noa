import { describe, expect, it } from 'vitest'

import { ALLOWED_ROUTES, resolveProxyTarget } from './routes'

/**
 * The allowlist is the guard (§T.44, §I.embed). These cases are what keeps it from widening by
 * accident — a new entry has to break the pinned table below to land.
 */

const ID = '9f1c2b7e-0000-4000-8000-000000000000'

describe('resolveProxyTarget — allows exactly the embed’s four calls', () => {
  it.each([
    ['GET', ['auth', 'me']],
    ['GET', ['action-requests', ID]],
    ['POST', ['action-requests', ID, 'approve']],
    ['POST', ['action-requests', ID, 'deny']],
  ] as const)('allows %s /%s', (method: string, segments: readonly string[]) => {
    expect(resolveProxyTarget(method, segments)).toEqual({
      allowed: true,
      path: segments.join('/'),
    })
  })

  it.each([
    // V42: this app has no login page, no LDAP form and no credential handling. A
    // pass-through proxy would put one on the origin LibreChat frames.
    ['POST', ['auth', 'login'], 'the login route V42 says does not exist here'],
    ['POST', ['auth', 'logout'], 'logout — the embed has no session controls'],
    // V41: the admin surface is reachable from the admin origin, which is not frameable.
    ['GET', ['admin', 'users'], 'the admin surface'],
    ['POST', ['admin', 'users', '1', 'tokens'], 'token minting'],
    ['GET', ['me', 'mcp-tokens'], 'the caller’s own MCP tokens'],
    // The MCP endpoint authenticates with a bearer token, never a cookie.
    ['POST', ['mcp'], 'the MCP transport'],
    ['GET', ['health'], 'the API liveness probe — this app has its own'],
    // Right path, wrong method: the shape is matched with the method, not after it.
    ['POST', ['auth', 'me'], 'a write to a read-only route'],
    ['GET', ['action-requests', ID, 'approve'], 'a decision by GET'],
    // Shape mismatches: an extra or missing segment is not the route.
    ['GET', ['action-requests'], 'the collection nobody exposed'],
    ['GET', ['action-requests', ID, 'receipt'], 'a sub-resource that is not built'],
    ['POST', ['action-requests', ID, 'approve', 'extra'], 'a longer path than the rule'],
    ['GET', ['auth', 'me', 'extra'], 'a longer path than the rule'],
    ['GET', [], 'the bare /api root'],
  ] as const)('refuses %s /%s — %s', (method: string, segments: readonly string[], _why: string) => {
    expect(resolveProxyTarget(method, segments)).toEqual({ allowed: false })
  })

  it('refuses an empty path segment rather than collapsing it', () => {
    // `/api/action-requests//approve` splits to an empty middle segment. Treating it
    // as a match would forward `action-requests//approve` upstream.
    expect(resolveProxyTarget('POST', ['action-requests', '', 'approve'])).toEqual({
      allowed: false,
    })
  })

  it('matches the method case-insensitively, as HTTP does not promise a case', () => {
    expect(resolveProxyTarget('get', ['auth', 'me'])).toEqual({
      allowed: true,
      path: 'auth/me',
    })
  })

  it('exposes four routes and no more', () => {
    // Pinned deliberately, and nothing has moved it: §T.46 settled that the CSRF token rides in the
    // card's own detail response, so there was no minting route to allowlist, and the poll uses the
    // `GET action-requests/<id>` entry planted here rather than a new one. §T.56 was predicted to
    // widen it and did not — the table surface reads server-side and never polls, so the browser
    // needs no route to it at all. A surface that *does* need one has to change this number, which
    // is the point.
    expect(
      ALLOWED_ROUTES.map((rule) => `${rule.method} ${rule.shape.map((s) => s ?? '<id>').join('/')}`),
    ).toEqual([
      'GET auth/me',
      'GET action-requests/<id>',
      'POST action-requests/<id>/approve',
      'POST action-requests/<id>/deny',
    ])
  })
})
