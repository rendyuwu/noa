/**
 * What this origin proxies, and nothing else (§T.44, §I.embed).
 *
 * The embed is the only NOA origin LibreChat may frame (V41; the admin app answers
 * `frame-ancestors 'none'`). A pass-through proxy would therefore put the whole API inside that
 * frame with the operator's session cookie — including `POST /auth/login`, which V42 says this
 * app does not have, and `/admin/*`, which is the reachability `frame-ancestors 'none'` exists to
 * deny. So the allowlist sits at the mechanism (V83b's shape): a route the card needs is added
 * here on purpose, or it does not exist on this origin at all.
 *
 * The id segment is NOT validated here. V27 owns what an absent, malformed or another operator's
 * id answers, and it answers all three alike; a shape check in the proxy would put a second, more
 * talkative judge in front of it.
 */

/** Refused with this, and never forwarded. See `ROUTE_NOT_PROXIED_STATUS`. */
export const ROUTE_NOT_PROXIED = 'route_not_proxied'

/**
 * 404, not 403. This app genuinely does not route the path, and a refusal code that varied by
 * cause would make the embed origin an existence oracle for the API's surface.
 */
export const ROUTE_NOT_PROXIED_STATUS = 404

type Rule = {
  method: 'GET' | 'POST'
  /** Literal segments; `null` matches exactly one non-empty segment of any value. */
  shape: readonly (string | null)[]
  /** The §V or §T this entry exists for — read by nothing, kept so a deletion has to argue. */
  why: string
}

const ALLOWED: readonly Rule[] = [
  // Identity for the card's header and for the 401 state (V38, V42). Cookie-only (§I.admin-api).
  { method: 'GET', shape: ['auth', 'me'], why: 'V38/V42 — identity, and the 401 state' },
  // The card's detail read. Built API-side at §T.41, where the *page* reads it server-side
  // instead; this entry is the one the browser polls through, so the card follows its run to a
  // terminal state without being told the outcome by whoever started it (§T.42, V29 —
  // `lib/approvals/poll.ts`).
  { method: 'GET', shape: ['action-requests', null], why: '§T.42 — polling the run (V29)' },
  // The decision itself: the only path an approve/deny may travel (V22, C18).
  {
    method: 'POST',
    shape: ['action-requests', null, 'approve'],
    why: '§T.41, V22 — cookie POST from a NOA-origin document',
  },
  {
    method: 'POST',
    shape: ['action-requests', null, 'deny'],
    why: '§T.41, V22 — same door, same guards (V15 wants a reason either way)',
  },
]

export type ProxyTarget = { allowed: true; path: string } | { allowed: false }

function matches(shape: readonly (string | null)[], segments: readonly string[]): boolean {
  if (shape.length !== segments.length) return false

  return shape.every((expected, index) => {
    const actual = segments[index]
    if (!actual) return false
    return expected === null ? true : expected === actual
  })
}

/**
 * Resolve a browser call on `/api/*` to the upstream path, or refuse it.
 *
 * Refusal is total: the caller must not reach upstream at all, so that "this origin does not
 * carry the login route" is a fact about the request that was never sent, not about a response
 * that came back.
 */
export function resolveProxyTarget(method: string, segments: readonly string[]): ProxyTarget {
  const wanted = method.toUpperCase()
  const rule = ALLOWED.find((entry) => entry.method === wanted && matches(entry.shape, segments))

  return rule ? { allowed: true, path: segments.join('/') } : { allowed: false }
}

/** The allowlist itself, for the test that pins what this origin exposes. */
export const ALLOWED_ROUTES: readonly Rule[] = ALLOWED
