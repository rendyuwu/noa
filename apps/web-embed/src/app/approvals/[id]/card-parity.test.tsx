import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'

import { buildSummary } from '@/lib/approvals/summary'

import { CardView } from './card-view'
import { CARD_ID, FIREWALL_EVIDENCE, approvalCard, approvedBody, receiptBody } from '../../../../tests/support/approval-card'

/**
 * The card and the block copied off it, compared byte for byte.
 *
 * **This is the check the whole shared-body arrangement exists for.** The card renders the strings
 * and the copied block pastes them, and if the two ever assembled their own the surface would be
 * two statements of one measurement — the argument `verdict.ts` already makes for the verdict
 * words, applied to the body. Every other spec in this package asserts one side or the other; this
 * one asserts that they are the same side.
 *
 * **The comparison is the rendered DOM against the pasted text, never model against model.** Two
 * calls into `lib/approvals/body.ts` would agree with each other by construction and would go on
 * agreeing after a renderer started re-wording what it was handed.
 *
 * **The fixture carries a `\n` in the runner's message on purpose.** That is the byte where the two
 * halves last disagreed about where a sentence ends: the card collapsed it to a space and the block
 * put the second sentence at column 0. Comparing the joined strings is what makes one repair prove
 * the other — the card holds one node containing the newline, the block holds two indented lines,
 * and the two are equal only if both repairs landed.
 */

const MESSAGE = 'Allowed 203.0.113.24 on alpha.\ncsf-beta did not answer, so this is not confirmed.'

/**
 * The body as the block carries it: the lines of its first two sections, de-indented.
 *
 * Section order is the builder's own contract (`lib/approvals/summary.ts`) — the record's title,
 * then the headline and its sentence, then the evidence block, then the stamps and the identity a
 * ticket is answered from. The last two are what the block adds and the card deliberately does not
 * have, so the body is everything before them.
 */
function pastedBody(text: string): string {
  return text
    .split('\n\n')
    .slice(1, 3)
    .flatMap((block) => block.split('\n').slice(1))
    .map((line) => line.slice(2))
    .join('\n')
}

/** The same body as the card drew it: the sentence, the server's own lines, the provenance line. */
function renderedBody(): string {
  const main = screen.getByRole('main')
  const statement = main.querySelector('[data-noa-statement]')?.textContent ?? ''
  const evidence = main.querySelector('[data-noa-evidence]')
  const lines = Array.from(evidence?.querySelectorAll('li') ?? []).map((row) => row.textContent)
  // `p:last-of-type`, not `p`: the block renders its note — "NOA read the server and found no
  // matching lines" — as a `<p>` BEFORE the closing one on both empty states, so the plain
  // selector compares the note against the closing line and this helper fails for a reason that
  // is not parity. Written that way it bound the lines-present state alone, which is one of the
  // block's three.
  const closing = evidence?.querySelector('p:last-of-type')?.textContent ?? ''

  return [statement, ...lines, closing].join('\n')
}

afterEach(cleanup)

it('renders and pastes one body, byte for byte', () => {
  const body = approvedBody(
    { status: 'COMPLETED' },
    receiptBody({
      after: { headline: 'IP unblocked — 203.0.113.24', message: MESSAGE },
      delta: { identity: { server: 'alpha' }, verification: 'unavailable' },
    }),
    { tool_name: 'whm_firewall_release_and_allow', evidence: FIREWALL_EVIDENCE },
  )
  const card = approvalCard(body)

  render(
    <CardView initial={{ kind: 'card', card }} actionRequestId={CARD_ID} signInUrl={null} frameOrigin={null} />,
  )

  // The fixture really does exercise all three pieces, so the equality below is not two empty
  // strings agreeing: a sentence with a break in it, a verbatim server line, and the provenance
  // line under it.
  const rendered = renderedBody()
  expect(rendered).toContain('\n')
  expect(rendered).toContain('DENY  203.0.113.24 # lfd: too many login failures')
  expect(rendered).toContain('Read from the server before the change ran.')

  expect(rendered).toBe(pastedBody(buildSummary(card).text))
})

it('puts the same headline and corner on both, and the block adds the decision to it', () => {
  // The header is arranged differently on the two — a heading and a corner line on the card, one
  // joined heading in the block — so it is compared by its parts rather than by equality.
  const card = approvalCard(approvedBody({ status: 'COMPLETED' }, receiptBody()))

  render(
    <CardView initial={{ kind: 'card', card }} actionRequestId={CARD_ID} signInUrl={null} frameOrigin={null} />,
  )

  const heading = screen.getByRole('heading', { level: 1 }).textContent ?? ''
  const corner = screen.getByRole('main').querySelector('[class*="corner"] p')?.textContent ?? ''

  expect(buildSummary(card).text.split('\n')).toContain(`${heading} — ${corner}`)
})
