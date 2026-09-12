/**
 * The words NOA puts on a finished change: the headline, and the sentence under it.
 *
 * **Two surfaces read this, which is why it is not a block inside either of them.** The card
 * (`app/approvals/[id]/outcome-view.tsx`) renders the verdict at the screen, and the copied summary
 * block (`lib/approvals/summary.ts`) pastes it into a ticket. A wording that lived in the card
 * would have been copied into the summary the first time the summary needed it, and two copies of
 * a sentence about a measurement are two answers to "did this work" that drift the moment either
 * is edited.
 *
 * **Its own file rather than the bottom of `delta.ts`, and the reason is headroom rather than
 * taste.** `delta.ts` is a little over 220 lines against this package's 300-line ceiling for a
 * `.ts`, and what lives here is roughly seventy lines with the notes that make it readable. Landing
 * them there would leave a handful of lines of room, and a file with a handful of lines of room is
 * one the next edit has to reorganise before it can start. The import runs one way — this file
 * reads `delta.ts` for the four verification constants and the delta's shape — so `delta.ts` keeps
 * the zero imports it has always had, and the parser stays readable without anything above it.
 *
 * **The voice is the operator's, not the runner's.** Whoever reads these sentences is deciding
 * whether to go and look at the machine, so they say what NOA did and what it found, in the words
 * someone would use out loud. The error code beside them is the API's own string and is never
 * translated — that is the word quoted to an administrator.
 */

import {
  type ChangeDelta,
  VERIFICATION_MISMATCH,
  VERIFICATION_NOT_IN_FORCE,
  VERIFICATION_UNAVAILABLE,
  VERIFICATION_VERIFIED,
} from '@/lib/approvals/delta'

/**
 * The four states verification has, plus the one this build has never heard of.
 *
 * **Four, not two.** `unavailable` is "NOA holds no measurement" and `mismatch` is "NOA took a
 * measurement and it disagrees" — collapsing those into one "not confirmed" merges a non-answer
 * with a failure, and they send an operator to different places. `not_in_force` is a third thing
 * again: the write landed and the step that applies it did not, which is neither a change nor a
 * refusal, and an operator told "failed" would go and re-add an entry that is already in the
 * config.
 */
const VERIFICATION_TEXT: Record<string, string> = {
  [VERIFICATION_VERIFIED]: 'Confirmed: NOA looked afterwards and the change is there.',
  [VERIFICATION_UNAVAILABLE]:
    'Not checked: NOA could not look afterwards, so this is neither confirmed nor ruled out.',
  [VERIFICATION_MISMATCH]: 'Does not match: NOA looked afterwards and the change is not there.',
  [VERIFICATION_NOT_IN_FORCE]:
    'Not live yet: the change was saved, and the step that puts it into effect did not run.',
}

/**
 * The sentence for a verification state, or the state itself when it is not one of the four.
 *
 * **The fallback is load-bearing, and it is why `verification` is a plain string rather than a
 * union.** A state a later API grows must reach the screen **as itself** and be undecidable here.
 * Anything else fails open — an unrecognised value quietly rendered as the confirmed sentence would
 * be NOA claiming a measurement it does not have, which is the one failure mode the four-state
 * split exists to prevent. Failing open as itself is what keeps a state nobody has written a
 * sentence for from folding into the benign one.
 */
export function verificationText(state: string): string {
  return VERIFICATION_TEXT[state] ?? `NOA reported "${state}", and this card cannot say what it means.`
}

/**
 * The headline, and it reads the delta rather than the envelope alone.
 *
 * **Not the decision an operator made.** `statusLabel` in `lib/approvals/card.ts` answers whether
 * the request was approved, denied or left to expire; this answers whether the change worked, and
 * on a surface that outlives the card — a summary pasted into a ticket — those two must not be the
 * same line. "Approved" beside an error code reads as a change that happened.
 *
 * **A third word, because two were a claim NOA cannot make.** `ok: false` means the call did not
 * come back with a success, which is not the same as the change not having happened: a mutation
 * that timed out may well have landed on the far side, and the runner says so by publishing
 * `unavailable` rather than inventing a reading. Headlining that as "did not complete" while the
 * block below says NOA holds no measurement is the card contradicting itself, and an operator
 * reads the top line.
 *
 * **`not_in_force` moves the headline in the other direction**, and it is the same defect
 * mirrored: the envelope says the write succeeded, so the old headline said so too, over a
 * sentence explaining that the step which applies the change never ran. An operator told
 * "completed" does not go and run it.
 *
 * **`mismatch` is named here rather than left to fall through**, and naming it changes nothing for
 * the way it arrives today: a contradicted reading comes with `ok: false`, and "did not complete"
 * is what the envelope said anyway. It changes the other direction. The same delta over a payload
 * reporting success headlined "completed" above a sentence saying NOA looked afterwards and the
 * change is not there — the same self-contradiction the two states above are here to remove, and
 * the rule is one rule: the headline reads the verification state, not the call's return. A mapping
 * with a hole in it is worse than the mapping, because the next state added gets its shape copied
 * from this one.
 *
 * No delta at all falls through to the envelope, which is the only thing there is to report.
 *
 * It takes the two things it reads rather than the receipt that carries them, so that the file
 * holding the delta's shape stays the bottom of the import chain: a receipt type here would point
 * this file back up at `card.ts`, which already reads `delta.ts`.
 */
export function outcomeText(delta: ChangeDelta | null, ok: boolean): string {
  switch (delta?.verification) {
    case VERIFICATION_UNAVAILABLE:
      return 'Outcome unknown'
    case VERIFICATION_NOT_IN_FORCE:
      return 'Not in force'
    case VERIFICATION_MISMATCH:
      return 'Did not complete'
    default:
      return ok ? 'Completed' : 'Did not complete'
  }
}

/**
 * The key every CHANGE runner composes its own sentence under.
 *
 * A runner answers `{"ok": ..., "message": ...}` and a failure carries one too, and the receipt's
 * `after` half is that payload byte for byte (`core/approvals/execution.py`). So the sentence is
 * the runner's own, promoted out of the key list rather than re-derived — and the readers of this
 * key share the constant so they cannot drift onto different spellings of it.
 *
 * `lib/approvals/decide.ts` also reads a `message`, and deliberately not this one: that is the
 * decision endpoint's error body, a different payload with a different owner.
 */
export const RUNNER_MESSAGE_KEY = 'message'

/**
 * The runner's sentence, or `null` when the runner did not write one.
 *
 * **One definition, because two surfaces ask the question and they must not answer differently.**
 * The card renders this as a paragraph (`app/approvals/[id]/outcome-view.tsx`) and then filters
 * the key out of the raw-key list underneath on the strength of having rendered it; the copied
 * summary block (`lib/approvals/summary.ts`) prints it under the headline. When the two disagreed,
 * a whitespace-only `message` rendered an invisible paragraph on the card AND was dropped from the
 * key list under it — the byte reached no surface at all, while the summary got it right.
 *
 * **Whitespace-only is not a sentence.** `after` is a `Record<string, unknown>`, so `null`, a
 * number and `'  '` are all representable, and none of them is something to print at an operator
 * as the runner's own words.
 *
 * **It answers `message`, never `message.trim()`.** The runner's own leading and trailing spaces
 * are the runner's wording, and rewriting them would change what both surfaces show on every input
 * that has any — which is a different change from this one.
 *
 * **Known ceiling**: a whitespace-only `message` now survives into the raw-key rows on both
 * surfaces, where it renders as a legible key with a blank-looking value — `factText` on the card
 * and `renderValue` in the summary both pass a whitespace string through as itself. The byte
 * reaches a surface and its key is findable, which is the part that was broken; making the value
 * itself legible is a second change to what an operator sees and is not made here.
 */
export function runnerSentence(after: Record<string, unknown>): string | null {
  const message = after[RUNNER_MESSAGE_KEY]
  return typeof message === 'string' && message.trim() !== '' ? message : null
}
