/**
 * Reading one card again, until there is nothing left to wait for (the approval card polls
 * to terminal — state in DB, one URL through receipt).
 *
 * **The state lives in the database, never in a connection**. An approve returns 202 with a
 * `tool_run_id` and the change runs somewhere else entirely; the only way for this frame to learn
 * how it went is to ask again. So the card asks again — through the same-origin proxy, with the
 * same cookie, at the entry the proxy planted for exactly this and the card page did not use
 * (that page reads its detail server-side).
 *
 * **Two speeds, because two different things are being waited on.** While a request is PENDING the
 * only thing that can change is the TTL sweep making it EXPIRED, which nobody is standing by
 * for — but which must land before an operator clicks an Approve the decision door would refuse
 * (a 401 renders explicit, never a blank card). While a run is in flight somebody *is* watching, and 15 seconds of blank waiting
 * after clicking Approve reads as a card that broke.
 *
 * **The run poll is capped and the pending poll is not**, and that asymmetry is deliberate. A
 * PENDING card has a server-side terminator on a deadline this app knows: the sweep flips it at the
 * TTL, the next poll reads a terminal status and stops. A STARTED run's terminator is the
 * reaper, which runs on an interval of its own and only after a cutoff — so a run whose executor
 * died is minutes from moving, and one polled every two seconds until it does would be polled for
 * as long as the frame is open.
 */

import { type ApprovalCard, type ApprovalCardLoad, parseApprovalCard } from '@/lib/approvals/card'

/**
 * Between polls while the request is still PENDING.
 *
 * Slow on purpose: nothing is executing, and the one transition available is an expiry whose
 * deadline is an hour out by default (`APPROVAL_PENDING_TTL_SECONDS`).
 */
export const POLL_INTERVAL_PENDING_MS = 15_000

/** Between polls while a run is in flight. Somebody clicked Approve and is watching the card. */
export const POLL_INTERVAL_RUN_MS = 2_000

/**
 * How many times a single run is polled before the card stops asking (≈5 minutes at the interval
 * above). See the module docstring: a run whose executor died moves when the reaper next runs,
 * which is not on a timescale anybody watches a frame for.
 */
export const RUN_POLL_LIMIT = 150

/**
 * Where a poll goes: this origin's proxy, never `NOA_API_URL`.
 *
 * That variable is server-only and a browser could not reach the API with it anyway (AGENTS.md,
 * the proxy route). The id is encoded and not shape-checked — requester-match owns what an
 * absent, malformed or foreign id answers and answers all three alike.
 */
export function pollPath(actionRequestId: string): string {
  return `/api/action-requests/${encodeURIComponent(actionRequestId)}`
}

/**
 * Whether NOA is still working on this one.
 *
 * `APPROVED` with no run at all counts: the `tool_runs` row lands in the same transaction as the
 * decision, so a card without one is a read that straddled the commit rather than a change that
 * will never run — asking again is the right response to it, and giving up is not.
 */
export function isRunning(card: ApprovalCard): boolean {
  if (card.status !== 'APPROVED') return false
  return card.run === null || card.run.status === 'STARTED'
}

/**
 * Whether anything about this card can still change.
 *
 * An **unrecognised** status lands here as terminal, which is the conservative direction: a build
 * that does not know what a status means also does not know what would end it, and a poll with no
 * stopping condition is worse than a card an operator reloads. `statusLabel` shows the raw value,
 * so the two of them together say "this is what the row holds, and this app cannot follow it".
 */
export function isTerminal(card: ApprovalCard): boolean {
  if (card.status === 'PENDING') return false
  return !isRunning(card)
}

/** How long to wait before asking again. */
export function pollIntervalMs(card: ApprovalCard): number {
  return isRunning(card) ? POLL_INTERVAL_RUN_MS : POLL_INTERVAL_PENDING_MS
}

/**
 * Whether the card has watched one run for long enough and should stop asking.
 *
 * Only a run is capped — a PENDING request has a server-side terminator in the expiry sweep,
 * on a deadline the card is already showing. Giving up is reported as "still running, reload to
 * check", never as a failure: NOA has no evidence the change failed, only that it has not been told
 * the change finished, and the receipt that would say either way is not written yet.
 */
export function isStalled(card: ApprovalCard, runPolls: number): boolean {
  return isRunning(card) && runPolls >= RUN_POLL_LIMIT
}

/**
 * Ask once, from the browser.
 *
 * The same four outcomes the server-side load has (`lib/approvals/card.ts`), because a poll can
 * discover every one of them *after* the first render: a session that expired under an open frame
 * answers 401, and that state renders explicit rather than a card left standing with a live
 * Approve button on it.
 *
 * A transient failure is `unavailable` and is deliberately **not** terminal — the caller keeps
 * asking. "NOA could not be reached just now" and "there is nothing more to wait for" are different
 * facts, and only one of them ends the lifecycle this URL owns.
 */
export async function fetchApprovalCard(actionRequestId: string): Promise<ApprovalCardLoad> {
  let response: Response
  try {
    response = await fetch(pollPath(actionRequestId), {
      method: 'GET',
      headers: { accept: 'application/json' },
      // The mechanism, stated: the cookie is what authenticates this.
      credentials: 'same-origin',
      cache: 'no-store',
    })
  } catch {
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
  // A 200 whose body is not a card is a broken deployment, not an empty card — the same judgement
  // the server-side loader makes, for the same reason (no blank card).
  if (card === null) return { kind: 'unavailable', status: response.status }

  return { kind: 'card', card }
}
