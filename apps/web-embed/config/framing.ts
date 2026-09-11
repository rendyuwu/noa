/**
 * Who may frame this app, and nobody else.
 *
 * The embed is the one NOA origin LibreChat is allowed to put in an iframe, and the card it will
 * host carries the Approve button and the operator-typed reason. The decision POST is a
 * same-origin `fetch` riding the `noa_session` cookie, so a page that can frame this app
 * and land a click on it is the reachability this header exists to deny. The admin app answers
 * `frame-ancestors 'none'` for the same reason from the other side.
 *
 * No `X-Frame-Options`: CSP `frame-ancestors` supersedes it in every browser that supports both,
 * and `ALLOW-FROM` — the only XFO form that could name one origin — is dead. Its absence is
 * asserted rather than assumed, because adding it back would break framing in any client that
 * honours XFO over CSP.
 *
 * Lives beside `root-env.ts` rather than inside `next.config.ts` for the same reason:
 * the part that can be got wrong is the rule, and a rule inside the config cannot be tested
 * without importing the config's side effects.
 */

/**
 * The LibreChat origin as deployed (the embed app's contract). Also the value `.env.example` and
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
 * and the 404 alike. A guard the next route has to remember is one it forgets — same shape as a
 * gate that lives anywhere but one place.
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
 * One origin, normalised — or `null` when the variable carries nothing at all.
 *
 * **One validator, two reads, and the two reads are two different variables.** The
 * `frame-ancestors` header reads the server-side `NOA_LIBRECHAT_ORIGIN` below; the origin a sizing
 * message is posted to reads the build-inlined `NEXT_PUBLIC_NOA_LIBRECHAT_ORIGIN`
 * (`src/lib/embed/frame-origin.ts`). Pointing the header at the public name instead would be
 * silent, not loud: a build or a compose that set only the private name would emit the development
 * default while reading as configured. What the two consumers share is the parsing, so the parsing
 * — and only it — lives here.
 *
 * `null` for both unset and blank, so each caller applies its own policy rather than inheriting one
 * this function chose: the header falls back to the pinned default (never omitted, never widened by
 * a variable nobody set), the message target refuses to post at all.
 *
 * **A value that is set and unusable throws, and the throw belongs here rather than in either
 * caller.** Under `next build`/`next dev` it is a build or boot failure, which is what a config
 * error should be — the same rule the API's key guards follow. Folding it into `null` here would
 * turn a malformed production CSP into a silent development default, the one failure this module
 * exists to stop. The message-target path wraps the call and swallows it for its own stated reason;
 * the header path lets it propagate.
 */
export function normalizeFrameOrigin(
  raw: string | undefined,
  variable: string,
): string | null {
  if (raw === undefined) return null

  // Trailing slashes stripped, mirroring `core/config.py::_normalize_base_url`, so the API's copy
  // of this value and this one cannot normalise differently.
  const value = raw.trim().replace(/\/+$/, '')
  if (!value) return null

  if (!ORIGIN_PATTERN.test(value)) {
    throw new Error(
      `${variable} must be a single origin like ${DEFAULT_LIBRECHAT_ORIGIN} ` +
        `(scheme, host, optional port — no wildcard, no path, no second origin). Got: ${raw}`,
    )
  }

  return value
}

/**
 * The origin allowed to frame this app.
 *
 * Absent or blank falls back to the pinned default: the header is never omitted and never widened
 * by a variable nobody set. An unusable value throws instead, which under `next build`/`next dev`
 * is a build or boot failure — the same rule applies to the API's key guards, for the same
 * reason: a config error should stop the deploy, not surface later as a header that says something
 * nobody meant.
 */
export function resolveFrameAncestor(env: Record<string, string | undefined>): string {
  return (
    normalizeFrameOrigin(env[LIBRECHAT_ORIGIN_ENV_VAR], LIBRECHAT_ORIGIN_ENV_VAR) ??
    DEFAULT_LIBRECHAT_ORIGIN
  )
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
