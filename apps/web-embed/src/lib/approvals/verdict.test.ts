import { describe, expect, it } from 'vitest'

import {
  type ChangeDelta,
  VERIFICATION_MISMATCH,
  VERIFICATION_NOT_IN_FORCE,
  VERIFICATION_UNAVAILABLE,
  VERIFICATION_VERIFIED,
} from './delta'
import { cardHeadline, outcomeCorner, runnerSentence } from './verdict'

/**
 * The corner, the headline and the sentence — the three strings every surface of a card shares.
 *
 * **The corner is asserted as a whole string, never as a substring.** Every answer it gives starts
 * with the same word, so a `toContain('Approved')` passes against all nine of them and against a
 * function that stopped reading the receipt entirely.
 *
 * **Each absence rule has its negative control.** "An unknown state is quoted" would pass against a
 * corner that quoted every state, so the four known ones are asserted beside it; "a blank headline
 * falls back" would pass against one that always fell back, so the two live sources are asserted
 * beside that.
 */

function delta(verification: string): ChangeDelta {
  return {
    identity: { server: 'alpha' },
    verification,
    verificationCause: null,
    changedFields: null,
    listDelta: null,
    backends: null,
    unanswered: null,
    deliveredCredential: null,
    newValues: null,
    bound: null,
  }
}

/** An approved card's corner, which is the only status whose corner carries a second fact. */
function corner(
  receipt: { ok: boolean; delta: ChangeDelta | null } | null,
  running = false,
): string {
  return outcomeCorner({ status: 'APPROVED', statusLabel: 'Approved', receipt, running })
}

describe('outcomeCorner, what the change did beside the decision', () => {
  it.each([
    [VERIFICATION_VERIFIED, 'Approved'],
    [VERIFICATION_UNAVAILABLE, 'Approved · not confirmed'],
    [VERIFICATION_MISMATCH, 'Approved · did not happen'],
    [VERIFICATION_NOT_IN_FORCE, 'Approved · not live yet'],
  ])('reads %s off the delta as %s', (verification, expected) => {
    // Four states, four corners. The merge this forbids is `unavailable` and `mismatch` printing
    // alike, which puts a non-answer and a measured disagreement on the card as one word — and
    // `not_in_force` reading as the confirmed one, which is a change an operator never goes to
    // finish.
    expect(corner({ ok: true, delta: delta(verification) })).toBe(expected)
  })

  it('reads the verification state and never the call’s return', () => {
    // The incident this exists for: a suspension WHM completed, reported as a failure because the
    // call timed out on the way back. `ok: false` is true — the call did not return a success — and
    // "did not happen" is a claim NOA cannot make while it holds no reading. The mirror is the
    // second pair: an envelope reporting success over a reading that contradicts it.
    expect(corner({ ok: false, delta: delta(VERIFICATION_UNAVAILABLE) })).toBe(
      'Approved · not confirmed',
    )
    expect(corner({ ok: true, delta: delta(VERIFICATION_MISMATCH) })).toBe(
      'Approved · did not happen',
    )
  })

  it('quotes a verification state this build has never heard of, rather than folding it in', () => {
    // The one failure mode the four-state split exists to prevent. An unrecognised value rendered
    // as the benign word would be NOA claiming a measurement it does not have, on the line nobody
    // goes and checks.
    expect(corner({ ok: true, delta: delta('refuted_by_neighbour') })).toBe(
      'Approved · NOA reported "refuted_by_neighbour"',
    )
  })

  it('separates a receipt with no delta by what the envelope said', () => {
    // A receipt carrying no delta is NOA having no statement about what moved, which is compatible
    // with a change that landed — so the envelope is the only thing there is to report.
    expect(corner({ ok: true, delta: null })).toBe('Approved · nothing recorded')
    expect(corner({ ok: false, delta: null })).toBe('Approved · did not run')
  })

  it('keeps a live run apart from a run that finished and recorded nothing', () => {
    // "Nothing recorded" is a statement about a run that is over. Printed over one still in flight
    // it is false, and it tells a reader there is nothing more to wait for. This pair is the whole
    // reason the corner takes the run at all.
    expect(corner(null, true)).toBe('Approved · running')
    expect(corner(null, false)).toBe('Approved · nothing recorded')
  })

  it.each([
    ['DENIED', 'Denied'],
    ['EXPIRED', 'Expired without an answer'],
    ['PENDING', 'Awaiting your decision'],
  ])('leaves a %s corner as the decision alone', (status, label) => {
    // Nothing ran, so there is nothing to say about a change. A `nothing recorded` here would read
    // as a change that was attempted and came back empty.
    expect(outcomeCorner({ status, statusLabel: label, receipt: null, running: false })).toBe(label)
  })
})

describe('cardHeadline, the change in the operator’s words', () => {
  it('prefers what happened over what was asked for', () => {
    // The runner names the outcome and the gate names the request, so the runner's wins the moment
    // there is a run that can say anything.
    expect(cardHeadline({ headline: 'Account suspended' }, { headline: 'Suspend an account' }, 'x')).toBe(
      'Account suspended',
    )
  })

  it('falls back to the gate’s, and then to the humanised tool name', () => {
    // **The last fallback is permanent.** Every `action_requests` row opened before the two headline
    // keys shipped carries neither, and those cards still have to render — a blank heading because
    // the payload predates a field is a blank card by another route.
    expect(cardHeadline(null, { headline: 'Suspend an account' }, 'whm_suspend_account')).toBe(
      'Suspend an account',
    )
    expect(cardHeadline(null, {}, 'whm_firewall_release_and_allow')).toBe(
      'Firewall Release And Allow',
    )
  })

  it.each([[null], [42], ['  ']])('treats %s under the key as no headline at all', (value) => {
    // `after` and `evidence` are both `Record<string, unknown>`, so all three are representable and
    // none is something to put at an operator as a heading. Whitespace is the one that used to slip
    // through: it renders as an invisible heading rather than as an obvious hole.
    expect(cardHeadline({ headline: value }, { headline: value }, 'whm_suspend_account')).toBe(
      'Suspend Account',
    )
  })
})

describe('runnerSentence', () => {
  it('takes the runner’s own bytes, spaces included', () => {
    expect(runnerSentence({ message: ' acmeco is suspended.\nIts sessions were closed. ' })).toBe(
      ' acmeco is suspended.\nIts sessions were closed. ',
    )
  })

  it.each([[null], [42], ['  '], [undefined]])('answers null for %s', (value) => {
    // Same rule as the headline, and one function answering for both: when the two spelled it out
    // separately they disagreed on whitespace, and a blank sentence rendered an invisible paragraph.
    expect(runnerSentence({ message: value })).toBeNull()
  })
})
