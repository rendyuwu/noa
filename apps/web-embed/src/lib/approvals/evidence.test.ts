import { describe, expect, it } from 'vitest'

import { evidenceBlock, evidenceBlockLines } from './evidence'

/**
 * The target system's own text, as the gate's evidence carries it.
 *
 * **The key's presence is the decision, so both halves of that are asserted.** "A heading draws a
 * block" passes against a renderer that draws one for every card; the case beside it supplies a
 * payload with lines and no heading, which is exactly what a PMG *add* sends, and demands nothing
 * at all.
 *
 * **A capped reading states its cap and an uncapped one says nothing**, which needs the same pair:
 * the truncated case alone would pass against a block that appended the cap sentence always.
 */

const FIREWALL_LINES = ['DENY  203.0.113.24 # lfd', 'DENY  203.0.113.24 # manual']

function firewall(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    evidence_heading: 'Why it was blocked',
    firewall: { matches: FIREWALL_LINES, total_matches: 2, truncated: false, ...over },
  }
}

describe('evidenceBlock, whether there is a block at all', () => {
  it('draws nothing without the gate’s heading, however much there is to draw', () => {
    // A PMG add: the address is not on the list, which is why it is being added, and that emptiness
    // is the before-state. An empty heading over nothing reads as a renderer that broke.
    expect(evidenceBlock({ matches: [], total_entries: 312 })).toBeNull()
    // And the separating case — the same shape with a heading is a block.
    expect(evidenceBlock({ evidence_heading: 'What was on the list', matches: [] })).not.toBeNull()
  })

  it('treats a blank heading as no heading', () => {
    expect(evidenceBlock({ evidence_heading: '  ', matches: FIREWALL_LINES })).toBeNull()
  })

  it('takes the heading from the gate, verbatim', () => {
    // Never a tool-name-to-heading table in here: the words are each family's own, written in
    // Python beside the runner that holds them.
    expect(evidenceBlock(firewall())?.heading).toBe('Why it was blocked')
    expect(evidenceBlock({ evidence_heading: 'What was allowed' })?.heading).toBe('What was allowed')
  })
})

describe('evidenceBlock, the lines', () => {
  it('carries a firewall reading verbatim and uncut', () => {
    expect(evidenceBlock(firewall())?.lines).toEqual(FIREWALL_LINES)
  })

  it('reads a PMG entry as the spelling PMG printed, not the canonical twin', () => {
    // Two spellings of one address really are two lines on a mail gateway, and the one an operator
    // can check against the box is the one the box printed.
    const block = evidenceBlock({
      evidence_heading: 'What was on the list',
      matches: [{ cidr: '198.51.100.7', normalized: '198.51.100.7/32' }],
    })

    expect(block?.lines).toEqual(['198.51.100.7'])
  })

  it('keeps a line it cannot read rather than dropping it', () => {
    // A line this app cannot parse is still a line the gate read from the server; dropping it would
    // understate the reading the decision rests on.
    const block = evidenceBlock({ evidence_heading: 'What was allowed', matches: [{ odd: 1 }] })

    expect(block?.lines).toEqual(['{"odd":1}'])
  })

  it('keeps a reading that came back empty apart from one nobody took', () => {
    // The distinction that is invisible in a rendered block unless the block spells it out. `[]` is
    // the gate having looked; an absent key is no look having happened, and a change may well have
    // been about something the reading would have found.
    const measured = evidenceBlock({ evidence_heading: 'What was on the list', matches: [] })
    const unread = evidenceBlock({ evidence_heading: 'What was on the list' })

    expect(measured?.lines).toBeNull()
    expect(unread?.lines).toBeNull()
    expect(measured?.note).toBe('NOA read the server and found no matching lines.')
    expect(unread?.note).toBe('NOA has no reading of the server to show here.')
    expect(measured?.note).not.toBe(unread?.note)
  })

  it('leaves note empty when there are lines to show', () => {
    // The separating case for the pair above: without it, both notes could be printed beneath a
    // list of lines and nothing here would say so.
    expect(evidenceBlock(firewall())?.note).toBeNull()
  })
})

describe('evidenceBlock, the closing line', () => {
  it('states the provenance of the reading and nothing else when it was complete', () => {
    expect(evidenceBlock(firewall())?.closing).toBe('Read from the server before the change ran.')
  })

  it('states the cap on a capped reading, by both numbers', () => {
    // The count shown against the count matched. Without the second, "this address was blocked and
    // is now allowed" reads as a statement about every line the firewall holds for it.
    const block = evidenceBlock(firewall({ total_matches: 34, truncated: true }))

    expect(block?.closing).toBe(
      'Read from the server before the change ran; this is the first 2 of 34 lines.',
    )
  })

  it('says a reading was cut short even when the total is unreadable', () => {
    // Dropping the whole clause because one field could not be read is how an honesty property
    // leaves without anyone deciding it should. The cap is stated; its size is not invented.
    const block = evidenceBlock(firewall({ total_matches: null, truncated: true }))

    expect(block?.closing).toBe('Read from the server before the change ran; the reading was cut short.')
  })
})

describe('evidenceBlockLines, the block as a ticket carries it', () => {
  it('puts the reading above its provenance', () => {
    expect(evidenceBlockLines(evidenceBlock(firewall())!)).toEqual([
      ...FIREWALL_LINES,
      'Read from the server before the change ran.',
    ])
  })

  it('puts the note in the reading’s place when there are no lines', () => {
    expect(evidenceBlockLines(evidenceBlock({ evidence_heading: 'What was on the list' })!)).toEqual([
      'NOA has no reading of the server to show here.',
      'Read from the server before the change ran.',
    ])
  })
})
