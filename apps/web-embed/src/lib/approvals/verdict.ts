/**
 * The words NOA puts on a change: the headline over it, the corner beside it, the sentence under it.
 *
 * **Two surfaces read this, which is why it is not a block inside either of them.** The card
 * (`app/approvals/[id]/card-view.tsx`) renders these at the screen, and the copied summary block
 * (`lib/approvals/summary.ts`) pastes them into a ticket. A wording that lived in the card would
 * have been copied into the summary the first time the summary needed it, and two copies of a
 * sentence about a measurement are two answers to "did this work" that drift the moment either is
 * edited.
 *
 * **Nothing here composes a sentence about a change.** The headline and the runner's message are
 * both authored in Python, beside the runner that holds its family's own vocabulary, and reach this
 * file as bytes to pass through. What this file owns is the *corner* — two words that are the same
 * on every family — and which of several measured strings a surface gets to show.
 *
 * **Its own file rather than the bottom of `delta.ts`, and the reason is headroom rather than
 * taste.** `delta.ts` is a little over 220 lines against this package's 300-line ceiling for a
 * `.ts`. The import runs one way — this file reads `delta.ts` for the four verification constants
 * and the delta's shape — so `delta.ts` keeps the zero imports it has always had, and the parser
 * stays readable without anything above it. The corner takes the status and the run as plain values
 * for the same reason: a receipt or a card type here would point this file back up at `card.ts`,
 * which already reads `delta.ts`.
 *
 * **The voice is the operator's, not the runner's.** Whoever reads these words is deciding whether
 * to go and look at the machine, so they say what NOA did and what it found, in the words someone
 * would use out loud.
 */

import {
  type ChangeDelta,
  VERIFICATION_MISMATCH,
  VERIFICATION_NOT_IN_FORCE,
  VERIFICATION_UNAVAILABLE,
  VERIFICATION_VERIFIED,
} from '@/lib/approvals/delta'
import { humanizeToolName } from '@/lib/approvals/tool-name'

/**
 * The key every CHANGE runner composes its own sentence under.
 *
 * A runner answers `{"ok": ..., "message": ...}` and a failure carries one too, and the receipt's
 * `after` half is that payload byte for byte (`core/approvals/execution.py`). So the sentence is
 * the runner's own, rendered rather than re-derived.
 *
 * `lib/approvals/decide.ts` also reads a `message`, and deliberately not this one: that is the
 * decision endpoint's error body, a different payload with a different owner.
 */
export const RUNNER_MESSAGE_KEY = 'message'

/**
 * The key the runner and the gate both name the change under.
 *
 * One spelling, two writers: the gate writes it into `approval_context` at request time
 * (`change_gate.py`, `EVIDENCE_HEADLINE`) and every runner writes it into the payload the receipt
 * carries. The card reads the runner's first and the gate's second, so a card reads back the words
 * that describe what actually happened where there are any.
 */
const HEADLINE_KEY = 'headline'

/**
 * The one status whose corner carries a second fact.
 *
 * `DENIED` and `EXPIRED` never reach a receipt — nothing ran — so their corner is the decision
 * alone, and `PENDING` has nothing to say about a change that has not started. The literal is a
 * second spelling of the API's `ActionRequestStatus`, which lives in `card.ts`; it is spelled here
 * rather than imported so this file stays below that one, and a case in `verdict.test.ts` binds it.
 */
const STATUS_APPROVED = 'APPROVED'

/**
 * What separates the decision from what the change then did.
 *
 * A middle dot rather than a comma or a dash: the two halves are facts of different kinds, and the
 * dot reads as a boundary rather than as a list or an aside.
 */
const CORNER_SEPARATOR = ' · '

/**
 * A payload value that is something to print, or `null`.
 *
 * **Whitespace-only is not a sentence and not a headline.** `after` and `evidence` are both
 * `Record<string, unknown>`, so `null`, a number and `'  '` are all representable and none of them
 * is something to put at an operator as NOA's own words. One rule for every caller here, because
 * two spellings of "blank" is exactly how a blank `message` once rendered an invisible paragraph on
 * the card while the copied block got it right.
 *
 * **It answers the value, never `value.trim()`.** A runner's own leading and trailing spaces are
 * the runner's wording, and rewriting them would change what both surfaces show on every input that
 * has any.
 */
export function spoken(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value : null
}

/** The runner's sentence, or `null` when the runner did not write one. */
export function runnerSentence(after: Record<string, unknown>): string | null {
  return spoken(after[RUNNER_MESSAGE_KEY])
}

/**
 * The change in the operator's words: the runner's headline, then the gate's, then the tool label.
 *
 * **The fallback is load-bearing and permanent.** Every `action_requests` row opened before the two
 * headline keys shipped carries neither, and those cards still have to render — a card that showed
 * a blank heading because the payload predates a field is a blank iframe by another route. The
 * humanised tool name is the label this app has always had, so the oldest card degrades to exactly
 * what it used to say rather than to nothing.
 *
 * The runner's wins over the gate's where both exist, and the order is the whole point: the gate
 * names what was *asked for* and the runner names what *happened*, so `Account suspended` replaces
 * `Suspend an account` the moment there is a run that can say so.
 */
export function cardHeadline(
  after: Record<string, unknown> | null,
  evidence: Record<string, unknown>,
  toolName: string,
): string {
  return (
    spoken(after?.[HEADLINE_KEY]) ?? spoken(evidence[HEADLINE_KEY]) ?? humanizeToolName(toolName)
  )
}

/**
 * What the change did, in the words that follow the decision — or `null` when there is nothing to
 * add, which is only ever a confirmed change.
 *
 * **Read off the verification state, never off the call's return.** `ok: false` means the call did
 * not come back with a success, which is not the same as the change not having happened: a mutation
 * that timed out may well have landed on the far side, and the runner says so by publishing
 * `unavailable` rather than inventing a reading. The mirrored defect is `not_in_force` — the
 * envelope says the write succeeded while the step that applies it never ran, and an operator told
 * "completed" does not go and run it.
 *
 * **Four states, not two.** `unavailable` is "NOA holds no measurement", `mismatch` is "NOA took a
 * measurement and it disagrees", `not_in_force` is "the write landed and the step applying it did
 * not". Collapsing any pair merges a non-answer with a failure, and they send an operator to
 * different places.
 *
 * **The fallback is what the four-state split exists for.** A state a later API grows reaches the
 * screen **as itself**, quoted, and undecidable here — which is why `verification` is a plain
 * string rather than a union. An unrecognised value quietly folded into the confirmed word would be
 * NOA claiming a measurement it does not have, and that is the one failure mode worth this much
 * care: the benign word is the one nobody goes and checks.
 *
 * **`running` is not `nothing recorded`, and the pair is why this takes the run at all.** "Nothing
 * recorded" is a statement about a run that finished and recorded nothing; printed over a live run
 * it is false, and it tells a reader the run is over when it is not.
 */
function changeReading(
  receipt: { ok: boolean; delta: ChangeDelta | null } | null,
  running: boolean,
): string | null {
  if (receipt === null) return running ? 'running' : 'nothing recorded'
  if (receipt.delta === null) return receipt.ok ? 'nothing recorded' : 'did not run'

  switch (receipt.delta.verification) {
    case VERIFICATION_VERIFIED:
      return null
    case VERIFICATION_UNAVAILABLE:
      return 'not confirmed'
    case VERIFICATION_MISMATCH:
      return 'did not happen'
    case VERIFICATION_NOT_IN_FORCE:
      return 'not live yet'
    default:
      return `NOA reported "${receipt.delta.verification}"`
  }
}

/**
 * The corner of the card: the decision an operator made, and what the change then did.
 *
 * **Two facts, never one word.** "Approved" beside a change NOA could not read back is an approval
 * pasted over an empty result, and the corner is the line a card is skimmed for.
 *
 * **It is the only thing separating a confirmed change from an unread one, by design.** The
 * headline is the runner's own and a confirmed suspension and one NOA could not read back both
 * publish `Account suspended`, because in both cases WHM accepted the call. A heading that hedged
 * would be a verdict word in a slot that names what was asked for, so the qualification lives here
 * and has to actually carry it.
 *
 * `statusLabel` is handed in already resolved (`lib/approvals/card.ts`) rather than imported, so
 * this file stays below `card.ts` in the import chain.
 */
export function outcomeCorner({
  status,
  statusLabel,
  receipt,
  running,
}: {
  status: string
  statusLabel: string
  /** The receipt's two readable halves. `null` until something has recorded what the change did. */
  receipt: { ok: boolean; delta: ChangeDelta | null } | null
  /** Whether NOA is still working on this one (`lib/approvals/poll.ts`, `isRunning`). */
  running: boolean
}): string {
  if (status !== STATUS_APPROVED) return statusLabel

  const reading = changeReading(receipt, running)
  return reading === null ? statusLabel : `${statusLabel}${CORNER_SEPARATOR}${reading}`
}
