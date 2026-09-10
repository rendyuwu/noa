/**
 * What an operator is told after a decision (§T.41).
 *
 * Split out of `decide.ts` so the wording is testable without a `fetch` and so the client
 * component imports one thing rather than two — the sentence an operator reads after clicking
 * Approve is part of the card's job, not an afterthought of the transport.
 *
 * **Every refusal names what to do next**, because each one has a different remedy: reload the
 * card, type a reason, ask for the change again, or nothing at all. The API's `message` is already
 * written that way (`core.approvals.errors`), so it is preferred whenever it arrives — these
 * sentences are the fallback for a refusal that reached us without one.
 */

import type { DecisionKind } from '@/lib/approvals/decide'

export type DecisionOutcome =
  /** The API accepted it. 202 for an approve, 200 for a deny — both mean "recorded". */
  | { kind: 'recorded'; decision: DecisionKind }
  /** The API answered, and said no. `errorCode` is theirs, never invented here. */
  | { kind: 'refused'; decision: DecisionKind; errorCode: string; message: string }
  /** The POST never got an answer. Not the same as a refusal, and must not read as one. */
  | { kind: 'unreachable'; decision: DecisionKind }

const RECORDED: Record<DecisionKind, string> = {
  approve: 'Approved. NOA is running the change now — this card will show the outcome.',
  deny: 'Denied. Nothing was changed.',
}

const REFUSAL_FALLBACKS: Record<string, string> = {
  change_reason_required: 'Type why this change is being made or refused.',
  csrf_token_invalid: 'This approval card is no longer valid. Reload it and try again.',
  action_request_already_decided:
    'This request has already been decided. Reload to see the outcome.',
  action_request_expired: 'This request expired before it was answered. Ask for the change again.',
  action_request_not_found: 'This request does not exist, or it is not yours to decide.',
}

/**
 * One sentence for one outcome.
 *
 * The API's own `message` wins when there is one: it is the operator-safe text V8 requires and the
 * remedy `core.approvals.errors` chose, and duplicating that wording here would give NOA two
 * answers to the same refusal that can drift apart.
 */
export function describeDecision(outcome: DecisionOutcome): string {
  if (outcome.kind === 'recorded') return RECORDED[outcome.decision]

  if (outcome.kind === 'unreachable') {
    return 'NOA could not be reached, so nothing was recorded. Try again.'
  }

  if (outcome.message) return outcome.message
  return (
    REFUSAL_FALLBACKS[outcome.errorCode] ??
    `NOA refused this decision (${outcome.errorCode}). Reload the card and try again.`
  )
}
