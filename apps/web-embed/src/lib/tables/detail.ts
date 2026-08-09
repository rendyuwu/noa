import { buildBackendUrl } from '@/lib/proxy/http'
import { type ResultTableLoad, parseResultTable } from '@/lib/tables/table'

/**
 * Loading one parked table, server-side (§T.56, §I.embed).
 *
 * **Server-side, not from the browser**, for the reason `lib/approvals/detail.ts` gives one surface
 * over: the HTML that reaches the frame is already the authenticated page, so there is no moment
 * where an operator looks at an empty box while a client fetch decides who they are. It also means
 * this app's proxy allowlist stays at the four entries §T.44 pinned — nothing here polls, because a
 * parked table does not change once it is written.
 *
 * The browser still never calls FastAPI directly (AGENTS.md). This runs inside the Next server on
 * the embed origin.
 *
 * **The cookie is forwarded and `Authorization` is not.** Every surface this app reaches is
 * cookie-authenticated (V22, V40); a bearer token is LibreChat's to send (C5), never a browser's,
 * and this loader adds no header a caller could use to relay one — the same departure §T.44(d)
 * makes in the proxy and §T.41(c) makes in the card's loader.
 *
 * **Four outcomes, because four of them render differently** — `ResultTableLoad` says which.
 */

export type { ResultTableLoad } from '@/lib/tables/table'

/** What `loadResultTable` needs off the incoming request. */
export type ResultTableRequest = {
  /** The raw `Cookie` header. `null` when the browser sent none — a guaranteed 401. */
  cookie: string | null
}

function upstreamUrl(token: string): string {
  // Encoded, then joined by `buildBackendUrl`, which also refuses anything that escapes the
  // backend's base prefix (§T.44). The token is not shape-checked here: the API owns what an
  // absent, malformed, foreign or expired token answers and answers all four alike, so a check in
  // front of it would be a second, more talkative judge.
  return buildBackendUrl(`tables/${encodeURIComponent(token)}`).toString()
}

/**
 * Fetch one table for the operator whose cookie this is.
 *
 * `cache: 'no-store'`: the answer is one operator's data behind one operator's session, and a
 * cached copy would be somebody else's rows.
 */
export async function loadResultTable(
  token: string,
  request: ResultTableRequest,
): Promise<ResultTableLoad> {
  const headers = new Headers({ accept: 'application/json' })
  if (request.cookie) headers.set('cookie', request.cookie)

  let response: Response
  try {
    response = await fetch(upstreamUrl(token), {
      method: 'GET',
      headers,
      cache: 'no-store',
      redirect: 'manual',
    })
  } catch {
    // The API is unreachable from this server. Reported as its own outcome rather than as a 404:
    // "does not exist" and "could not be asked" send an operator to different people.
    return { kind: 'unavailable', status: 0 }
  }

  if (response.status === 401) return { kind: 'unauthenticated' }
  if (response.status === 404) return { kind: 'not-found' }
  if (!response.ok) return { kind: 'unavailable', status: response.status }

  let body: unknown
  try {
    body = await response.json()
  } catch {
    return { kind: 'unavailable', status: response.status }
  }

  const table = parseResultTable(body)
  // A 200 whose body is not a table is a broken deployment, not an empty table. Rendering the
  // headings over nothing would tell an operator the listing came back empty (V38's family).
  if (table === null) return { kind: 'unavailable', status: response.status }

  return { kind: 'table', table }
}
