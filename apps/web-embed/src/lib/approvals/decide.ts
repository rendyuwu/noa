/**
 * Sending one decision (§T.41, §T.42 — V15, V22, V39, V80).
 *
 * **A JS `fetch`, never a form submit.** V80 is not a style preference: the sandbox LibreChat
 * renders this frame under was measured at pin `45cc53c4` and is `allow-scripts
 * allow-same-origin` (plus `allow-popups` on one of the two render sites) with **`allow-forms`
 * absent**. A native `<form>` submit therefore dies silently inside the frame — no
 * error, no request, an operator clicking a button that does nothing. A same-origin `fetch` was
 * measured returning 200 from inside that same frame, with the `noa_session` cookie riding.
 *
 * **Same-origin, so the cookie rides and there is no CORS surface.** The path is this app's
 * `/api/*` proxy (§T.44), whose allowlist already carries both decision routes; the proxy adds the
 * hop to the API server-side. `credentials: 'same-origin'` is the default and is stated anyway,
 * because it is the whole mechanism.
 *
 * **The reason is not validated here.** V15 puts the gate on the endpoint — a blank reason is a
 * 409 `change_reason_required` from the API, under a row lock, checked against the same rule the
 * database CHECK holds. A client-side refusal in front of that would be a second definition of
 * "blank" (the API strips whitespace; so does the DB predicate) and two spellings of blank is one
 * too many. What this module does with a 409 is *show* it.
 */

import type { DecisionOutcome } from '@/lib/approvals/outcome'

export type DecisionKind = 'approve' | 'deny'

/** Body shape the API expects (§I.embed). Two fields, and neither is a status. */
type DecisionBody = {
  reason: string
  csrf: string
}

/**
 * Where a decision POST goes.
 *
 * A relative path on this origin — never `NOA_API_URL`, which is server-only and which a browser
 * cannot reach anyway (AGENTS.md, §T.44).
 */
export function decisionPath(actionRequestId: string, decision: DecisionKind): string {
  return `/api/action-requests/${encodeURIComponent(actionRequestId)}/${decision}`
}

async function readErrorCode(response: Response): Promise<{ errorCode: string; message: string }> {
  try {
    const body: unknown = await response.json()
    if (typeof body === 'object' && body !== null) {
      const record = body as Record<string, unknown>
      const errorCode = typeof record['error_code'] === 'string' ? record['error_code'] : ''
      const message = typeof record['message'] === 'string' ? record['message'] : ''
      if (errorCode) return { errorCode, message }
    }
  } catch {
    // Fall through: a refusal whose body is not JSON is still a refusal.
  }
  return { errorCode: `http_${response.status}`, message: '' }
}

/**
 * POST one decision and report what came back.
 *
 * Never throws for a refusal — a 403, 404 or 409 is an answer an operator has to read, and the
 * codes are the API's (`csrf_token_invalid`, `change_reason_required`,
 * `action_request_already_decided`, `action_request_expired`, `action_request_not_found`). A
 * network failure is its own outcome, because "NOA refused this" and "the request never arrived"
 * are different things to tell someone standing in front of a pending change.
 */
export async function submitDecision(input: {
  actionRequestId: string
  decision: DecisionKind
  reason: string
  csrf: string
}): Promise<DecisionOutcome> {
  const body: DecisionBody = { reason: input.reason, csrf: input.csrf }

  let response: Response
  try {
    response = await fetch(decisionPath(input.actionRequestId, input.decision), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      // The mechanism, stated: the cookie is what authenticates this.
      credentials: 'same-origin',
      cache: 'no-store',
    })
  } catch {
    return { kind: 'unreachable', decision: input.decision }
  }

  if (!response.ok) {
    const { errorCode, message } = await readErrorCode(response)
    return { kind: 'refused', decision: input.decision, errorCode, message }
  }

  return { kind: 'recorded', decision: input.decision }
}
