/**
 * Nobody may frame this app.
 *
 * The admin panel holds the operator's session and every admin mutation behind it, so a document
 * that can put this app in a frame and land a click on it is exactly the reachability this header
 * exists to deny. It is a property of the app: not frameable, by anyone.
 *
 * The embed answers the mirror of this — `frame-ancestors <LibreChat origin>` — because it has one
 * legitimate parent. This app has none, so there is nothing to configure: no variable, no
 * default to fall back to, no value an environment could widen. `buildFramingHeaders` therefore
 * takes no argument at all, which is the point rather than an omission — an unused `env` parameter
 * is where a knob later grows.
 *
 * No `X-Frame-Options`: CSP `frame-ancestors` supersedes it in every browser that supports both,
 * and a `SAMEORIGIN` added later would be read *instead* of this header by a client that honours
 * XFO first — which is a different, weaker policy. Its absence is asserted rather than assumed.
 *
 * Lives beside `root-env.ts` rather than inside `next.config.ts` for the reason that one is here:
 * the part that can be got wrong is the rule, and a rule inside the config cannot be tested
 * without importing the config's side effects.
 */

export const FRAMING_HEADER = 'Content-Security-Policy'

/**
 * The CSP keyword, quotes included.
 *
 * `'none'` is a keyword source and must be quoted; a bare `none` is a *host* source — CSP would
 * read it as a hostname called "none", match nothing against it, and the policy would then say
 * "only a host nobody can reach may frame this", which happens to behave the same way today and
 * would not survive the first reader who trusts it. The quotes are part of the value.
 */
export const FRAME_ANCESTORS = "'none'"

/**
 * Every path on this origin, the form Next's own CSP guide uses.
 *
 * Deliberately not a page-shaped pattern: config headers are applied during route resolution
 * without terminating the match, so this one entry covers the pages, `/healthz`, the 404 and the
 * `/api/*` proxy to come. A guard the next route has to remember is one it forgets — one seam,
 * never per-route code.
 */
export const FRAMING_SOURCE = '/(.*)'

export type FramingHeaderEntry = {
  source: string
  headers: Array<{ key: string; value: string }>
}

/** The `headers()` entry `next.config.ts` returns. */
export function buildFramingHeaders(): FramingHeaderEntry[] {
  return [
    {
      source: FRAMING_SOURCE,
      headers: [{ key: FRAMING_HEADER, value: `frame-ancestors ${FRAME_ANCESTORS}` }],
    },
  ]
}
