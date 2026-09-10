/**
 * Where an operator signs in, when this app cannot let them (§T.43 — V38, V42).
 *
 * **This app has no login page and this module does not add one.** The card authenticates with the
 * `noa_session` cookie and the MCP bearer token never reaches a browser, so a 401 in
 * the frame means the operator has a working LibreChat token and no NOA browser session. V42 says
 * the answer to that is a pointer, never a form: sign in where NOA already has a login — the admin
 * app — and come back. The cookie is scoped to the registrable domain, so a session minted there
 * rides to this origin.
 *
 * **The whole URL, not an origin plus a path.** `apps/admin-web` (§T.47–§T.50) is unbuilt, so a
 * hard-coded `/login` here would be this package guessing at another package's routing table, and a
 * deployment that put the login elsewhere would need a code change to say so.
 *
 * **Read at request time, not baked.** `output: 'standalone'` never executes `next.config.ts` at
 * runtime, so resolving this in the config the way §T.45 resolves the framing origin would freeze it
 * at build — and unlike that header, this value is not one a runtime variable must be prevented from
 * changing. It is server-side and deliberately not `NEXT_PUBLIC_*`: the page resolves it and passes
 * it down, so no build inlines it and no client bundle carries it.
 */

/** Read from the repo-root `.env` in dev (`config/root-env.ts`), from the environment in prod. */
export const SIGN_IN_ENV_VAR = 'NOA_SIGN_IN_URL'

/** `http`/`https` and nothing else. See `resolveSignInUrl`. */
const ALLOWED_PROTOCOLS = new Set(['http:', 'https:'])

/**
 * The sign-in address, or `null` when there is not a usable one.
 *
 * **`null` renders no link at all**, and that is the point of returning it rather than a default: a
 * link to a host nobody deployed is a door that goes nowhere, and on an operator's own machine a
 * stale `http://localhost:3000` could be *anything*. The 401 state says what is true without it —
 * the same judgement `canDecide` makes about the Approve button (`lib/approvals/card.ts`): an
 * affordance that cannot work is worse than none, because it reads as an action that was refused.
 *
 * **The protocol allowlist is a security boundary, not tidiness.** This value becomes an `href` in
 * the operator's document, so `javascript:` would be script execution configured by environment
 * variable, and `data:` a page of someone else's authorship on this app's frame. Anything that is
 * not `http`/`https` is refused rather than sanitised.
 *
 * Embedded credentials are refused for a related reason: `https://user:pass@host/` renders as a
 * link that puts a secret in the document, in the browser's history and in any screenshot of the
 * card (V8's shape, V26's shape).
 */
export function resolveSignInUrl(env: Record<string, string | undefined>): string | null {
  const raw = env[SIGN_IN_ENV_VAR]
  if (typeof raw !== 'string') return null

  const value = raw.trim()
  if (!value) return null

  let url: URL
  try {
    // No base: a relative path is not an address a new top-level tab can be opened at, and
    // resolving one against this origin would point the operator at a login page V42 says this app
    // does not have.
    url = new URL(value)
  } catch {
    return null
  }

  if (!ALLOWED_PROTOCOLS.has(url.protocol)) return null
  if (url.username || url.password) return null

  return url.toString()
}
