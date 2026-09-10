import { buildBackendUrl } from '@/lib/proxy/http'
import { type ApprovalCardLoad, parseApprovalCard } from '@/lib/approvals/card'

/**
 * Loading one approval card, server-side (§T.41, §I.embed).
 *
 * **Server-side, not from the browser**, and the reason is what the page then is: the HTML that
 * reaches the frame is already the authenticated card, so there is no moment where an operator
 * looks at an empty box while a client fetch decides whether they are signed in — and the CSRF
 * token arrives as part of the render rather than through a second round trip a page could
 * make without ever having been allowed to read the request.
 *
 * The browser still never calls FastAPI directly (AGENTS.md). This runs inside the Next server on
 * the embed origin; the *decision* POST the card then makes goes out through the same-origin
 * allowlisted proxy (§T.44), which is the path V22 names.
 *
 * **The cookie is forwarded and `Authorization` is not.** Every surface this app reaches is
 * cookie-authenticated; a bearer token is LibreChat's to send, never a browser's,
 * and this loader adds no header a caller could use to relay one — the same departure §T.44(d)
 * makes in the proxy.
 *
 * **Four outcomes, because four of them render differently** — `ApprovalCardLoad` says which, and it
 * lives in `lib/approvals/card.ts` because the browser-side poll (§T.42) answers the same four
 * questions and the card switches on the result once. Re-exported here so this module still
 * reads as the loader's whole contract.
 */

export type { ApprovalCardLoad } from '@/lib/approvals/card'

/** What `loadApprovalCard` needs off the incoming request. */
export type ApprovalCardRequest = {
  /** The raw `Cookie` header. `null` when the browser sent none — a guaranteed 401. */
  cookie: string | null
}

function upstreamUrl(actionRequestId: string): string {
  // Encoded, then joined by `buildBackendUrl`, which also refuses anything that escapes the
  // backend's base prefix (§T.44). The id is not shape-checked here: V27 owns what an absent,
  // malformed or foreign id answers and answers all three alike, so a UUID test in front of it
  // would be a second, more talkative judge.
  return buildBackendUrl(`action-requests/${encodeURIComponent(actionRequestId)}`).toString()
}

/**
 * Fetch one card for the operator whose cookie this is.
 *
 * `cache: 'no-store'`: the answer is one operator's session state and carries a freshly minted
 * CSRF token, so a cached copy would be another operator's card, or a stale token, or both.
 */
export async function loadApprovalCard(
  actionRequestId: string,
  request: ApprovalCardRequest,
): Promise<ApprovalCardLoad> {
  const headers = new Headers({ accept: 'application/json' })
  if (request.cookie) headers.set('cookie', request.cookie)

  let response: Response
  try {
    response = await fetch(upstreamUrl(actionRequestId), {
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

  const card = parseApprovalCard(body)
  // A 200 whose body is not a card is a broken deployment, not an empty card. Rendering the
  // fields as blanks would put an Approve button on top of nothing (V38's family).
  if (card === null) return { kind: 'unavailable', status: response.status }

  return { kind: 'card', card }
}
