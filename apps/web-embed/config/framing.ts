/**
 * Who may frame this app, and nobody else (§T.45, V41).
 *
 * The embed is the one NOA origin LibreChat is allowed to put in an iframe, and the card it will
 * host carries the Approve button and the operator-typed reason (C8, V15). The decision POST is a
 * same-origin `fetch` riding the `noa_session` cookie (V22, V80), so a page that can frame this app
 * and land a click on it is the reachability this header exists to deny. The admin app answers
 * `frame-ancestors 'none'` for the same reason from the other side (V41, §T.49).
 *
 * No `X-Frame-Options`: CSP `frame-ancestors` supersedes it in every browser that supports both,
 * and `ALLOW-FROM` — the only XFO form that could name one origin — is dead. Its absence is
 * asserted rather than assumed, because adding it back would break framing in any client that
 * honours XFO over CSP.
 *
 * Lives beside `root-env.ts` rather than inside `next.config.ts` for the reason §T.44 put that one
 * here: the part that can be got wrong is the rule, and a rule inside the config cannot be tested
 * without importing the config's side effects.
 */

/**
 * The LibreChat origin as deployed (§I.embed, V41). Also the value `.env.example` and
 * `core/config.py::noa_librechat_origin` carry, so the three do not disagree.
 */
export const DEFAULT_LIBRECHAT_ORIGIN = 'https://chat.noa.internal'

/** Read at build time; see `resolveFrameAncestor`. */
export const LIBRECHAT_ORIGIN_ENV_VAR = 'NOA_LIBRECHAT_ORIGIN'

export const FRAMING_HEADER = 'Content-Security-Policy'

/**
 * Every path on this origin, the form Next's own CSP guide uses.
 *
 * Deliberately not a page-shaped pattern: config headers are applied during route resolution
 * without terminating the match, so this one entry covers the pages, the `/api/*` proxy, `/healthz`
 * and the 404 alike. A guard the next route has to remember is one it forgets (V83b's shape).
 */
export const FRAMING_SOURCE = '/(.*)'

/**
 * `scheme://host[:port]` and nothing else.
 *
 * A CSP host-source may legally carry wildcards and paths; this is narrower on purpose. The value
 * names exactly one deployment's chat origin, so a wildcard or a second origin in the variable is a
 * misconfiguration that would silently widen who may frame the approval card.
 */
const ORIGIN_PATTERN = /^https?:\/\/[a-z0-9-]+(\.[a-z0-9-]+)*(:\d+)?$/i

export type FramingHeaderEntry = {
  source: string
  headers: Array<{ key: string; value: string }>
}

/**
 * The origin allowed to frame this app.
 *
 * Absent or blank falls back to the pinned default: the header is never omitted and never widened
 * by a variable nobody set. An unusable value throws instead, which under `next build`/`next dev`
 * is a build or boot failure — the same rule §T.8 applies to the API's key guards, for the same
 * reason: a config error should stop the deploy, not surface later as a header that says something
 * nobody meant.
 */
export function resolveFrameAncestor(env: Record<string, string | undefined>): string {
  const raw = env[LIBRECHAT_ORIGIN_ENV_VAR]
  if (raw === undefined) return DEFAULT_LIBRECHAT_ORIGIN

  // Trailing slashes stripped, mirroring `core/config.py::_normalize_base_url`, so the API's copy
  // of this value and this one cannot normalise differently.
  const value = raw.trim().replace(/\/+$/, '')
  if (!value) return DEFAULT_LIBRECHAT_ORIGIN

  if (!ORIGIN_PATTERN.test(value)) {
    throw new Error(
      `${LIBRECHAT_ORIGIN_ENV_VAR} must be a single origin like ${DEFAULT_LIBRECHAT_ORIGIN} ` +
        `(scheme, host, optional port — no wildcard, no path, no second origin). Got: ${raw}`,
    )
  }

  return value
}

/** The `headers()` entry `next.config.ts` returns. */
export function buildFramingHeaders(
  env: Record<string, string | undefined>,
): FramingHeaderEntry[] {
  return [
    {
      source: FRAMING_SOURCE,
      headers: [
        { key: FRAMING_HEADER, value: `frame-ancestors ${resolveFrameAncestor(env)}` },
      ],
    },
  ]
}
