import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ApprovalReceipt } from '@/lib/approvals/card'
import {
  type ChangeDelta,
  VERIFICATION_MISMATCH,
  VERIFICATION_NOT_IN_FORCE,
  VERIFICATION_UNAVAILABLE,
  VERIFICATION_VERIFIED,
} from '@/lib/approvals/delta'

import { Fact, Outcome } from './outcome-view'

/**
 * The receipt section, and the absences it has to keep apart.
 *
 * **Every absence rule here needs a negative control or it measures nothing.** "An absent facet
 * renders nothing" passes perfectly against a renderer that drops that facet unconditionally, so
 * each of those specs is paired with a fixture where the facet is *present* — and where it is a
 * present `false`, because a measured negative and a missing measurement are the two things this
 * section exists to tell apart.
 *
 * Fixtures are built here rather than taken from the shared card fixtures: what is under test is a
 * component that takes a receipt, and a body that had to go through the card parser first would put
 * a second subject between the delta and the assertion.
 */

/** Every facet absent — the shape a runner that measured nothing beyond its identity publishes. */
function delta(overrides: Partial<ChangeDelta> = {}): ChangeDelta {
  return {
    identity: { server: 'alpha', username: 'acmeco' },
    verification: VERIFICATION_VERIFIED,
    verificationCause: null,
    changedFields: null,
    listDelta: null,
    backends: null,
    unanswered: null,
    deliveredCredential: null,
    newValues: null,
    bound: null,
    ...overrides,
  }
}

function receipt(overrides: Partial<ApprovalReceipt> = {}): ApprovalReceipt {
  return {
    ok: true,
    before: { suspended: false },
    after: { suspended: true, suspended_at: '2026-09-11T09:31:00+00:00' },
    errorCode: null,
    delta: delta(),
    ...overrides,
  }
}

function renderOutcome(overrides: Partial<ApprovalReceipt> = {}) {
  return render(<Outcome receipt={receipt(overrides)} />)
}

/** The verification sentence as it reaches the screen, whatever state produced it. */
function verificationLine(container: HTMLElement): string {
  return container.querySelector('[data-noa-verification]')?.textContent ?? ''
}

/** The state behind that sentence, as the DOM carries it for a reader checking a screenshot. */
function verificationState(container: HTMLElement): string {
  return container.querySelector('[data-noa-verification]')?.getAttribute('data-noa-verification') ?? ''
}

/** The headline row — the value beside the `Outcome` label, not merely the word somewhere. */
function outcomeLine(container: HTMLElement): string {
  const label = Array.from(container.querySelectorAll('dt')).find(
    (node) => node.textContent === 'Outcome',
  )
  return label?.nextElementSibling?.textContent ?? ''
}

describe('Outcome', () => {
  afterEach(cleanup)

  it('gives each verification state its own sentence, and never folds an unknown one into verified', () => {
    // Four states, four sentences — the merge this forbids is "unavailable" and "mismatch" both
    // printing as "not confirmed", which puts a non-answer and a measured disagreement on the card
    // as one word. The fifth is the state a later API grows: it must reach the screen as itself and
    // be undecidable, never fail open into the verified sentence.
    const states = [
      VERIFICATION_VERIFIED,
      VERIFICATION_UNAVAILABLE,
      VERIFICATION_MISMATCH,
      VERIFICATION_NOT_IN_FORCE,
      'refuted_by_neighbour',
    ]

    const sentences = states.map((verification) => {
      const { container } = render(<Outcome receipt={receipt({ delta: delta({ verification }) })} />)
      const line = verificationLine(container)
      cleanup()
      return line
    })

    expect(new Set(sentences).size).toBe(5)
    // Named rather than left to the count above: these are the two most likely to be collapsed, and
    // a set of five would still be five if some other pair were merged and a sixth string appeared.
    expect(sentences[1]).not.toBe(sentences[2])
    expect(sentences[0]).not.toBe(sentences[1])
    // The unknown state is on the card verbatim, so an operator and an administrator quote the same
    // word at each other.
    expect(sentences[4]).toContain('refuted_by_neighbour')
  })

  it('renders one line per changed field, old and new', () => {
    const { container } = renderOutcome({
      delta: delta({
        changedFields: [
          { field: 'suspended', old: false, new: true },
          { field: 'reason_code', old: 'none', new: 'abuse' },
        ],
      }),
    })

    expect(screen.getByText('suspended: false → true')).toBeTruthy()
    expect(screen.getByText('reason_code: none → abuse')).toBeTruthy()
    // One row is one line: nothing here is a column that a narrow frame could pull out of line.
    expect(container.querySelectorAll('li')).toHaveLength(2)
  })

  it('says nothing changed for an empty field list, and says something else for no delta at all', () => {
    // The distinction most likely to be got wrong, and the one that is invisible in a rendered card
    // unless the card spells it out. `[]` is a claim: NOA read the target and nothing had moved.
    // `null` delta is the absence of a claim, and a change may well have landed.
    const { unmount } = renderOutcome({ delta: delta({ changedFields: [] }) })
    expect(screen.getByText('Nothing changed.')).toBeTruthy()
    unmount()

    renderOutcome({ delta: null, ok: false, errorCode: 'postflight_unavailable' })
    expect(screen.getByText(/no delta recorded/i)).toBeTruthy()
    expect(screen.queryByText('Nothing changed.')).toBeNull()
    // And the named cause is on the card verbatim beside it: an absent delta with no code at all
    // would leave an operator with nothing to quote.
    expect(screen.getByText('postflight_unavailable')).toBeTruthy()
  })

  it('labels the API error code as an error code, never as a reason', () => {
    // A reason in this repository is one thing: the justification an operator types at decision
    // time, which the model never authors, relays or sees. One word must not name two facts.
    renderOutcome({ ok: false, errorCode: 'ssh_sudo_required' })

    expect(screen.getByText('Error code')).toBeTruthy()
    expect(screen.queryByText('Reason')).toBeNull()
    expect(screen.getByText('ssh_sudo_required')).toBeTruthy()
  })

  it('shows both spellings of a normalised target', () => {
    // The operator typed one string and the target system holds another: a mail gateway keeps
    // `198.51.100.7` and `198.51.100.7/32` as two lines and one entry. Showing one of the two would
    // decide for the operator which spelling they get to check against the box.
    //
    // The keys are the ones `pmg_whitelist_runner.py::_common` actually publishes, not invented
    // ones — a fixture that renamed them would pass here and describe a record no runner sends.
    // Rendering the identity as it stands is also what makes this work with no special case: the
    // pair is in the record because the runner put it there.
    renderOutcome({
      delta: delta({
        identity: {
          server: 'mail-1',
          action: 'add',
          target: '198.51.100.7',
          normalized_target: '198.51.100.7/32',
        },
      }),
    })

    expect(screen.getByText('198.51.100.7')).toBeTruthy()
    expect(screen.getByText('198.51.100.7/32')).toBeTruthy()
  })

  it('renders per-backend rows even when the change did not complete', () => {
    // Those falses were earned by a postflight that answered. A refused change still measured which
    // backend refused it and which ones answered, and gating the rows on the envelope's `ok` would
    // drop the evidence exactly where an operator most needs it.
    renderOutcome({
      ok: false,
      errorCode: 'firewall_partial',
      delta: delta({
        verification: VERIFICATION_MISMATCH,
        backends: [
          { name: 'csf-alpha', driven: true, answered: true, verdict: 'blocked', errorCode: null },
          { name: 'csf-beta', driven: false, answered: false, verdict: null, errorCode: 'ssh_sudo_required' },
        ],
      }),
    })

    expect(screen.getByText('csf-alpha: ran the change, answered, says blocked')).toBeTruthy()
    // The negative control for the row above: a present `false` reads differently from a present
    // `true`, so a renderer that printed the same row for both would go red here.
    expect(screen.getByText('csf-beta: not driven, silent, ssh_sudo_required')).toBeTruthy()
  })

  it('renders nothing at all for a backend list nobody recorded', () => {
    // The absence half of the pair above. Absent is absent: never "no backends", never a cross.
    const { container } = renderOutcome({ delta: delta({ backends: null }) })

    expect(screen.queryByText('Backends')).toBeNull()
    expect(container.querySelectorAll('li')).toHaveLength(0)
  })

  it('says an empty backend list outright, and never as a heading over nothing', () => {
    // Three states, three renderings, and the middle one is the point. `[]` is a change that drove
    // no backend and heard from none — the empty-gather shape — so it must not render as the
    // absent case, and a heading with no rows under it reads as a renderer that broke rather than
    // as a measurement.
    const { container } = renderOutcome({ delta: delta({ backends: [] }) })

    expect(screen.getByText('No backend was driven, and none answered.')).toBeTruthy()
    expect(screen.queryByText('Backends')).toBeNull()
    expect(container.querySelectorAll('ul')).toHaveLength(0)
  })

  it('names the source that did not answer, and prints no such line when they all did', () => {
    const { unmount } = renderOutcome({
      delta: delta({
        verification: VERIFICATION_UNAVAILABLE,
        unanswered: ['csf-beta'],
      }),
    })
    // By name, never as a count: "one backend was silent" does not say which server to go and look
    // at.
    expect(screen.getByText(/csf-beta/)).toBeTruthy()
    expect(screen.getByText(/no answer from/i)).toBeTruthy()
    unmount()

    // The separating case. Without it, "the silent source is named" would pass against a renderer
    // that printed the line unconditionally.
    const answered = renderOutcome({ delta: delta({ unanswered: [] }) })
    expect(screen.queryByText(/no answer from/i)).toBeNull()
    answered.unmount()

    // And the third state, pinned rather than defaulted into: `null` is a family with no
    // per-source accounting to make, which is not a silent source either. It renders the same
    // nothing as `[]` — the two are folded here on purpose, and the reasoning is at the fold. This
    // case is what makes that a decision rather than an accident of the fixture default.
    renderOutcome({ delta: delta({ unanswered: null }) })
    expect(screen.queryByText(/no answer from/i)).toBeNull()
  })

  it('ships the bound of a capped reading, and separates a full one from an absent one', () => {
    const { unmount } = renderOutcome({
      delta: delta({ bound: { total: 240, truncated: true } }),
    })
    expect(screen.getByText(/240 lines, and the reading was cut short/)).toBeTruthy()
    unmount()

    // Present and `false`: a reading that covered everything is a different claim from one that was
    // cut, and both are different from a bound nobody recorded.
    const full = renderOutcome({ delta: delta({ bound: { total: 12, truncated: false } }) })
    expect(screen.getByText(/12 lines, all of them/)).toBeTruthy()
    full.unmount()

    renderOutcome({ delta: delta({ bound: null }) })
    expect(screen.queryByText(/lines/)).toBeNull()
  })

  it('renders a list delta with the target system spelling of each line', () => {
    renderOutcome({
      delta: delta({
        listDelta: { added: ['1.2.3.4/32'], removed: ['10.0.0.0/8'], totalEntries: 42 },
      }),
    })

    expect(screen.getByText('added 1.2.3.4/32')).toBeTruthy()
    expect(screen.getByText('removed 10.0.0.0/8')).toBeTruthy()
    expect(screen.getByText('42')).toBeTruthy()
  })

  it('says an empty list delta explicitly, rather than rendering an empty block', () => {
    // The no-op a runner discovers only after the approval: it re-read and found the world already
    // as the operator wanted it. An empty block would read as a renderer that broke.
    renderOutcome({
      delta: delta({ listDelta: { added: [], removed: [], totalEntries: null } }),
    })

    expect(screen.getByText(/nothing entered or left the list/i)).toBeTruthy()
    expect(screen.queryByText('Entries in the list')).toBeNull()
  })

  it('carries the delivered-credential slot and the new values, and neither when absent', () => {
    const { unmount } = renderOutcome({
      delta: delta({
        deliveredCredential: 'https://yopass.noa.internal/#/s/token',
        newValues: { expires_at: '2026-09-12T09:00:00+00:00' },
      }),
    })
    // The facet's presence is the whole claim: a credential may now be live.
    expect(screen.getByText('Credential delivered')).toBeTruthy()
    expect(screen.getByText('https://yopass.noa.internal/#/s/token')).toBeTruthy()
    expect(screen.getByText('expires_at')).toBeTruthy()
    unmount()

    renderOutcome()
    expect(screen.queryByText('Credential delivered')).toBeNull()
    expect(screen.queryByText('Now set')).toBeNull()
  })

  it('names the cause of a non-answer, and carries none on an answer', () => {
    const { unmount } = renderOutcome({
      delta: delta({
        verification: VERIFICATION_UNAVAILABLE,
        verificationCause: 'postflight_unavailable',
      }),
    })
    expect(screen.getByText('postflight_unavailable')).toBeTruthy()
    unmount()

    renderOutcome()
    expect(screen.queryByText('Because')).toBeNull()
  })

  it('puts the exact value behind a rendered one where a caller supplies it', () => {
    // The card renders a relative time and keeps the absolute one reachable from the same row. The
    // tooltip belongs to the value, not the label: a hover over "Opened" would explain the wrong
    // half.
    const { container } = render(
      <Fact label="Opened" value="4 minutes ago" title="2026-09-11T09:00:00+00:00" />,
    )

    expect(container.querySelector('dd')?.title).toBe('2026-09-11T09:00:00+00:00')
    expect(container.querySelector('dt')?.title).toBe('')
  })

  it('still renders the verdict and what the runner reported', () => {
    // The section's other job, unchanged by the delta: the envelope's own answer, and the after
    // half of the receipt as data rather than as the word "done".
    renderOutcome()

    expect(screen.getByText('Completed')).toBeTruthy()
    expect(screen.getByText('suspended_at')).toBeTruthy()
    expect(screen.getByText('2026-09-11T09:31:00+00:00')).toBeTruthy()
  })

  it('headlines an unmeasured outcome as unknown, beside the state that says why', () => {
    // The incident this case exists for: a suspension WHM completed, reported to the operator as
    // a failure because the call timed out on the way back. The envelope's `ok: false` is true —
    // the call did not return a success — and "did not complete" is the claim NOA cannot make
    // over a block saying it holds no reading. The top line is the one an operator acts on.
    const { container } = renderOutcome({
      ok: false,
      errorCode: 'timeout',
      delta: delta({ verification: VERIFICATION_UNAVAILABLE, verificationCause: 'timeout' }),
    })

    expect(outcomeLine(container)).toBe('Outcome unknown')
    // Headline and state, asserted on the same render: the word above and the sentence below have
    // to be answers to the same receipt, and a card whose top line was reconciled by hand could
    // pass the first of these with the second saying something else.
    expect(verificationState(container)).toBe(VERIFICATION_UNAVAILABLE)
    expect(verificationLine(container)).toContain('neither confirmed nor refuted')
  })

  it('does not headline a change that was written but never applied as completed', () => {
    // The same defect mirrored. Here the envelope says the write succeeded, so the old headline
    // did too — over a sentence explaining that the step which applies it never ran. An operator
    // told "completed" does not go and run it.
    const { container } = renderOutcome({
      ok: true,
      delta: delta({ verification: VERIFICATION_NOT_IN_FORCE }),
    })

    expect(outcomeLine(container)).not.toBe('Completed')
    expect(outcomeLine(container)).toBe('Not in force')
  })

  it('leaves a verified receipt reading as the success it is', () => {
    // The negative control, and it is what keeps every spec around it from passing against a
    // headline that stopped reading the receipt at all and simply printed one word.
    const { container } = renderOutcome()

    expect(outcomeLine(container)).toBe('Completed')
  })

  it('headlines a contradicted reading the same way whichever way the call returned', () => {
    // A contradicted reading arrives with `ok: false` today, where the envelope agrees and there
    // is nothing to reconcile. The pair is the point: the same delta over a payload reporting
    // success headlined "completed" above a sentence saying NOA read the target back and it
    // disagrees, which is the same self-contradiction the unmeasured and never-applied cases were
    // fixed for. A headline that disagrees with the verification block printed beneath it is the
    // defect, so the headline reads the verification state rather than the call's return.
    const { container: refused } = renderOutcome({
      ok: false,
      errorCode: 'postflight_mismatch',
      delta: delta({ verification: VERIFICATION_MISMATCH }),
    })
    expect(outcomeLine(refused)).toBe('Did not complete')
    cleanup()

    const { container: reportedOk } = renderOutcome({
      ok: true,
      delta: delta({ verification: VERIFICATION_MISMATCH }),
    })
    expect(outcomeLine(reportedOk)).not.toBe('Completed')
    expect(outcomeLine(reportedOk)).toBe('Did not complete')
    // Asserted on the same render as the headline: the word above and the sentence below have to
    // be answers to one receipt.
    expect(verificationState(reportedOk)).toBe(VERIFICATION_MISMATCH)
    expect(verificationLine(reportedOk)).toContain('disagrees')
  })

  it('falls back to the envelope when there is no delta to read', () => {
    // A receipt with no delta is NOA having no statement about what moved, which is compatible
    // with a change that landed — but there is nothing here to headline it with, so the envelope
    // is the only thing there is to report and it reports exactly that.
    const { container } = renderOutcome({ ok: false, errorCode: 'ssh_failed', delta: null })

    expect(outcomeLine(container)).toBe('Did not complete')
  })
})
