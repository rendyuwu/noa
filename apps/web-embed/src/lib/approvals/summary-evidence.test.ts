/**
 * What the copied block says about the reading a change was authorised against.
 *
 * `summary.test.ts` beside this file holds the record's own shape — the headline, the stamps, the
 * identity a ticket is answered from, both flavours — and asserts one whole output line for line.
 * This one holds the evidence block, the credential it must never carry, and the byte where the
 * card and the block last disagreed. Two files because one ran past this package's 300-line ceiling
 * for a `.ts`, and a section whose wording cannot be corrected without reddening an unrelated suite
 * is a section that stays wrong.
 *
 * Every assertion is a whole line: the distinctions this builder draws are distinctions between
 * sentences sharing most of their words.
 */

import { describe, expect, it } from 'vitest'

import { buildSummary } from './summary'
import {
  FIREWALL_EVIDENCE,
  summaryCard as card,
  summaryDelta as delta,
} from '../../../tests/support/summary-card'

/** Whole-line membership. A substring match cannot separate the sentences this builder draws. */
function expectLine(text: string, line: string): void {
  expect(text.split('\n')).toContain(`  ${line}`)
}

/** One card carrying a receipt, which is the ordinary case every assertion here reads. */
function recorded(after: Record<string, unknown> = {}) {
  return buildSummary(
    card({ receipt: { ok: true, before: {}, after, errorCode: null, delta: delta() } }),
  ).text
}

describe('the evidence block', () => {
  it('carries the gate’s heading, the server’s own lines and the cap they were read under', () => {
    // One of the four honesty properties the deleted rows carried: a claim resting on two of
    // thirty-four lines says so, on the line that names where the lines came from.
    const summary = buildSummary(card({ evidence: FIREWALL_EVIDENCE, receipt: null, run: null })).text

    expect(summary.split('\n')).toContain('Why it was blocked')
    expectLine(summary, 'DENY  203.0.113.44 # lfd')
    expectLine(summary, 'DENY  203.0.113.44 # manual')
    expectLine(
      summary,
      'Read from the server before the change ran; this is the first 2 of 34 lines.',
    )
  })

  it('says nothing extra about an uncapped reading', () => {
    // The separating case: without it, the cap sentence above would pass against a block that
    // appended one unconditionally, and the property that matters is the one that fires.
    const firewall = { matches: ['DENY  203.0.113.44 # lfd'], total_matches: 1, truncated: false }
    const summary = buildSummary(
      card({ evidence: { ...FIREWALL_EVIDENCE, firewall }, receipt: null, run: null }),
    ).text

    expectLine(summary, 'Read from the server before the change ran.')
    expect(summary).not.toContain('first 1 of')
  })

  it('emits no section at all where the gate published no heading', () => {
    // The same decision the card makes from the same key. An empty heading in a pasted block is
    // worse than on a screen: nobody reading the ticket a year later can tell it from a hole.
    expect(recorded()).not.toContain('Read from the server')
  })
})

describe('what the block refuses to carry', () => {
  it('records a delivered credential in the runner’s sentence and never the link', () => {
    // A reusable link is readable by everyone who reads the ticket it was pasted into, until it
    // expires — so the link stays on the card and the fact that one was delivered travels in the
    // sentence the runner composed, which is written where the deployment settings are legible.
    const url = 'https://yopass.example.com/#/s/8f2a-fetchable'
    const summary = buildSummary(
      card({
        toolName: 'proxmox_reset_vm_password',
        receipt: {
          ok: true,
          before: {},
          after: { message: 'The password was reset and a one-time link was sent.' },
          errorCode: null,
          delta: delta({ deliveredCredential: url }),
        },
      }),
    )

    expectLine(summary.text, 'The password was reset and a one-time link was sent.')
    expect(summary.text).not.toContain(url)
    expect(summary.html).not.toContain('yopass')
  })
})

describe('the embedded newline', () => {
  it('indents the second sentence of a runner message like the first', () => {
    // `renderText` indents per array element, so a `\n` inside one line used to put the second
    // sentence at column 0 while everything around it sat at two spaces. The repair is one split
    // before both flavours, because the newline is the contract and the renderers are what move.
    const summary = buildSummary(
      card({
        receipt: {
          ok: true,
          before: {},
          after: { message: 'Allowed 203.0.113.44 on whm-lab-1.\ncsf-beta did not answer.' },
          errorCode: null,
          delta: delta(),
        },
      }),
    )

    expectLine(summary.text, 'Allowed 203.0.113.44 on whm-lab-1.')
    expectLine(summary.text, 'csf-beta did not answer.')
    // And the same break in the rich flavour, where HTML would otherwise collapse it to a space.
    expect(summary.html).toContain('<li>csf-beta did not answer.</li>')
  })
})

