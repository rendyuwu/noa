import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ApprovalReceipt } from '@/lib/approvals/card'
import { type ChangeDelta, VERIFICATION_VERIFIED } from '@/lib/approvals/delta'
import { type EvidenceBlock, evidenceBlock } from '@/lib/approvals/evidence'

import { EvidenceBlockView, Fact, Outcome, Statement } from './outcome-view'

/**
 * The pieces a card is drawn from, at the render.
 *
 * What each piece *says* is decided in `lib/` and asserted there — the corner in `verdict.test.ts`,
 * the block's strings in `evidence.test.ts`. This file is the other half: that the strings reach
 * the DOM unchanged, in the shape the frame needs them in.
 *
 * **The line-break rule is split across two lanes on purpose.** jsdom computes no styles, so
 * "`white-space: pre-line` turns the runner's `\n` into a visible break" is a claim it cannot make
 * and would silently pass. What it *can* prove is the half a component can break: that the newline
 * reaches the DOM at all, rather than being split or stripped on the way. The rendering half is in
 * `e2e/card-evidence.browser.e2e.ts`, against a real computed style.
 */

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
    before: {},
    after: { message: 'acmeco is suspended on alpha.' },
    errorCode: null,
    delta: delta(),
    ...overrides,
  }
}

/** A block built through the real resolver, so no spec here can describe one it cannot produce. */
function block(evidence: Record<string, unknown>): EvidenceBlock {
  const resolved = evidenceBlock(evidence)
  if (resolved === null) throw new Error('fixture evidence carries no heading')
  return resolved
}

describe('Statement', () => {
  it('renders the runner’s bytes with nothing between them and the DOM', () => {
    // The rule the whole surface rests on: no text here is authored at runtime. A renderer that
    // trimmed, re-wrapped or re-punctuated would be composing a sentence about a machine it never
    // read, and this is the assertion that would go red first.
    const sentence = 'Allowed 203.0.113.24 on alpha.\ncsf-beta did not answer.'
    const { container } = render(<Statement text={sentence} />)

    expect(container.querySelector('p')?.textContent).toBe(sentence)
  })

  it('leaves the line break in the document rather than splitting the sentence', () => {
    // The newline is the contract — four of the five composing families spell it that way — so the
    // renderer moves and the runners do not. Splitting here would be this component deciding where
    // a runner's sentence ends; the stylesheet renders the break instead.
    const { container } = render(<Statement text={'first.\nsecond.'} />)

    expect(container.querySelectorAll('p')).toHaveLength(1)
    expect(container.textContent).toContain('\n')
  })
})

describe('EvidenceBlockView', () => {
  it('renders the heading and every line, one row each', () => {
    const lines = ['DENY  203.0.113.24 # lfd', 'DENY  203.0.113.24 # manual']
    const { container } = render(
      <EvidenceBlockView block={block({ evidence_heading: 'Why it was blocked', firewall: { matches: lines } })} />,
    )

    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Why it was blocked')
    expect(Array.from(container.querySelectorAll('li')).map((row) => row.textContent)).toEqual(lines)
  })

  it('keeps a line the server holds twice, rather than collapsing it to one', () => {
    // A firewall reading concatenates two backends' matches and both can carry the same rule.
    // Collapsing the pair would understate what is on the box — and a naive key would do it
    // silently, which is why the key carries the index.
    const same = 'DENY  203.0.113.24'
    const { container } = render(
      <EvidenceBlockView block={block({ evidence_heading: 'x', firewall: { matches: [same, same] } })} />,
    )

    expect(container.querySelectorAll('li')).toHaveLength(2)
  })

  it('says which empty it is, and never draws a heading over an empty list', () => {
    // A heading with no rows under it reads as a renderer that broke. The two sentences are the
    // model's, so the card and the copied block cannot disagree about which empty this was.
    const { container, unmount } = render(
      <EvidenceBlockView block={block({ evidence_heading: 'What was on the list', matches: [] })} />,
    )
    expect(screen.getByText('NOA read the server and found no matching lines.')).toBeTruthy()
    expect(container.querySelectorAll('ul')).toHaveLength(0)
    unmount()

    render(<EvidenceBlockView block={block({ evidence_heading: 'What was on the list' })} />)
    expect(screen.getByText('NOA has no reading of the server to show here.')).toBeTruthy()
  })

  it('states the cap of a capped reading, and nothing extra on a complete one', () => {
    // One of the four honesty properties the old delta section carried: a claim resting on twenty
    // of thirty-four lines says so. The uncapped case is what stops this passing against a block
    // that appends the cap sentence unconditionally.
    const capped = {
      evidence_heading: 'Why it was blocked',
      firewall: { matches: ['a', 'b'], total_matches: 34, truncated: true },
    }
    const { unmount } = render(<EvidenceBlockView block={block(capped)} />)
    expect(
      screen.getByText('Read from the server before the change ran; this is the first 2 of 34 lines.'),
    ).toBeTruthy()
    unmount()

    render(
      <EvidenceBlockView
        block={block({ ...capped, firewall: { matches: ['a', 'b'], total_matches: 2, truncated: false } })}
      />,
    )
    expect(screen.getByText('Read from the server before the change ran.')).toBeTruthy()
  })
})

describe('Outcome, what only a finished change has', () => {
  it('renders the delivered credential’s link, and nothing at all without one', () => {
    // The facet's presence is the whole claim: a credential may now be live. NOA never held the old
    // value and must not record the new one, so there is nothing to pair it with.
    const url = 'https://yopass.noa.internal/#/s/token'
    const { unmount } = render(
      <Outcome receipt={receipt({ delta: delta({ deliveredCredential: url }) })} />,
    )
    expect(screen.getByText('Credential delivered')).toBeTruthy()
    expect(screen.getByText(url)).toBeTruthy()
    unmount()

    // The separating case, and it is also the whole of what this component draws now: a receipt
    // with no credential adds no block at all.
    const { container } = render(<Outcome receipt={receipt()} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders the link once, not once per surface it used to appear on', () => {
    // It used to be here twice — as this row and again as a `yopass_url` key in the dump of
    // everything the runner reported. The dump is gone and so is the second copy.
    const url = 'https://yopass.noa.internal/#/s/token'
    render(
      <Outcome
        receipt={receipt({
          after: { yopass_url: url, message: 'Password reset.' },
          delta: delta({ deliveredCredential: url }),
        })}
      />,
    )

    expect(screen.getAllByText(url)).toHaveLength(1)
  })
})

describe('Fact', () => {
  it('puts the exact value behind a rendered one on the value, never on the label', () => {
    // The card renders a relative time and keeps the absolute one reachable from the same row. A
    // hover over "Opened" would explain the wrong half.
    const { container } = render(
      <Fact label="Opened" value="4 minutes ago" title="2026-09-11T09:00:00+00:00" />,
    )

    expect(container.querySelector('dd')?.title).toBe('2026-09-11T09:00:00+00:00')
    expect(container.querySelector('dt')?.title).toBe('')
  })
})

afterEach(cleanup)
