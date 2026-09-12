import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ApprovalReceipt } from '@/lib/approvals/card'
import { type ChangeDelta, VERIFICATION_VERIFIED } from '@/lib/approvals/delta'

import { Outcome } from './outcome-view'

/**
 * The runner's own sentence, and the key list under it.
 *
 * A second file beside `outcome-view.test.tsx` because that one is at 416 lines against this
 * package's 450-line ceiling for a `.tsx`, and because the two have different subjects: that one is
 * the delta and the absences it has to keep apart, this one is what reaches the payload block once
 * the blocks above have stated some of it. Its fixtures are rebuilt here rather than shared —
 * fifteen lines against a type the compiler checks on both sides, which is cheaper than a support
 * module two suites would then have to agree about.
 *
 * **The rule the whole file exists for: a value may be restated, but it may never leave.** The
 * block prints `after` minus what was already said above it, and every removal below is paired with
 * the case where the thing above declined to render — because a filter that ran unconditionally
 * would pass every "it was not printed twice" assertion and lose the value outright.
 */

/** Every facet absent, as in the sibling suite: the shape a runner that measured little publishes. */
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

function renderOutcome(overrides: Partial<ApprovalReceipt> = {}) {
  const receipt: ApprovalReceipt = {
    ok: true,
    before: { suspended: false },
    after: { ok: true },
    errorCode: null,
    delta: delta(),
    ...overrides,
  }
  return render(<Outcome receipt={receipt} />)
}

/**
 * The "Reported by the runner" block as printed key → value.
 *
 * Scoped to that block rather than to the card, because a key filtered out of it is very often
 * printed above it — that is the point of the filter, and a card-wide assertion could not tell
 * "moved upstairs" from "gone". It reads the rendered pairs rather than the payload, so a key that
 * survives the filter and then fails to render still counts as absent.
 *
 * It throws rather than answering `{}` when the block is missing. Every absence assertion below
 * would otherwise pass against a section that stopped rendering altogether.
 */
function reportedRows(container: HTMLElement): Record<string, string> {
  const heading = Array.from(container.querySelectorAll('h3')).find(
    (node) => node.textContent === 'Reported by the runner',
  )
  const block = heading?.parentElement
  if (!block) throw new Error('the reported-by-the-runner heading has no block around it')

  const values = Array.from(block.querySelectorAll('dd'))
  return Object.fromEntries(
    Array.from(block.querySelectorAll('dt')).map((key, index) => [
      key.textContent ?? '',
      values[index]?.textContent ?? '',
    ]),
  )
}

/** The `<p>` carrying exactly this text, wherever in the section it ended up. */
function paragraph(container: HTMLElement, text: string): HTMLParagraphElement | undefined {
  return Array.from(container.querySelectorAll('p')).find((node) => node.textContent === text)
}

describe('Outcome, what the runner reported', () => {
  afterEach(cleanup)

  it('promotes the runner’s sentence out of the key list and renders it as a sentence', () => {
    const { container } = renderOutcome({
      after: { ok: true, message: 'acmeco is suspended on alpha.', suspended: true },
    })

    const sentence = paragraph(container, 'acmeco is suspended on alpha.')
    expect(sentence).toBeTruthy()
    // Outside every fact list, which is what makes it a sentence rather than a value: a `<dd>` is
    // rendered in the monospace an operator checks character by character against a target system.
    expect(sentence?.closest('dl')).toBeNull()
    // The class is read off the verification sentence already on the card rather than spelled out
    // here, so the binding holds whatever the stylesheet names it.
    expect(sentence?.className).toBe(container.querySelector('[data-noa-verification]')?.className)

    // And it is not also a row underneath: one fact, one place on the card.
    const rows = reportedRows(container)
    expect(rows['message']).toBeUndefined()
    expect(rows['suspended']).toBe('true')
  })

  it('keeps a message the sentence could not render as a row instead', () => {
    // `after` is `Record<string, unknown>`, so a runner answering a number — or `null`, or an empty
    // string — is representable, and the sentence above declines all three. The row is then the only
    // surface the value has, and a key list that dropped `message` unconditionally would take it
    // off the card entirely rather than merely printing it once.
    const { container, unmount } = renderOutcome({ after: { ok: false, message: 42 } })

    expect(reportedRows(container)['message']).toBe('42')
    expect(paragraph(container, '42')).toBeUndefined()
    unmount()

    // The collision the condition has to survive, and the reason "the sentence rendered" is one
    // variable read by both: `message` is a name a delta may itself carry, and filtering by the
    // delta's key names alone would take this row off the card by the other arm of the condition
    // while the sentence was declining to print it.
    const named = renderOutcome({
      after: { ok: false, message: null },
      delta: delta({ identity: { server: 'alpha', message: 'the machine refused' } }),
    })
    expect(reportedRows(named.container)['message']).toBe('null')
  })

  it('drops what the blocks above stated, reading their names off the delta itself', () => {
    // Three removals, one per block that states a key above this one: the identity row, the changed
    // fields, and "Now set". The names are never typed into the renderer — they come out of
    // whichever runner answered, so a hand-kept list would print a duplicate the day one is renamed.
    const { container } = renderOutcome({
      after: {
        server: 'alpha',
        suspended: true,
        expires_at: '2026-09-12T09:00:00+00:00',
        untouched: 'kept',
      },
      delta: delta({
        identity: { server: 'alpha' },
        changedFields: [{ field: 'suspended', old: false, new: true }],
        newValues: { expires_at: '2026-09-12T09:00:00+00:00' },
      }),
    })

    const rows = reportedRows(container)
    expect(rows['server']).toBeUndefined()
    expect(rows['suspended']).toBeUndefined()
    expect(rows['expires_at']).toBeUndefined()
    // The separating case. Without it the three above pass just as well against a filter that
    // emptied the block, and an empty block reads as a renderer that broke rather than as a card
    // that said everything once.
    expect(rows['untouched']).toBe('kept')
  })

  it('leaves the facts the sentences above restate in the block as well', () => {
    // Stated above as sentences rather than as keys — the verdict word, the verification line — so
    // no key-name filter reaches them, and the restatement survives by design. This block is where
    // an operator finds the byte the runner actually sent, whatever prose was made of it.
    const { container } = renderOutcome({
      after: { ok: true, status: 'completed', verified: true, message: 'Done.' },
    })

    const rows = reportedRows(container)
    expect(rows['ok']).toBe('true')
    expect(rows['status']).toBe('completed')
    expect(rows['verified']).toBe('true')
  })
})
