import { describe, expect, it } from 'vitest'

import { VERIFICATION_VERIFIED, parseChangeDelta } from './delta'

/**
 * Reading the delta a runner published beside its envelope.
 *
 * The properties worth separating here are all about what the parser must *not* invent. A facet
 * the runner omitted is a measurement nobody took, and every way of filling it in — `{}`, `[]`,
 * `false` — reads on the screen exactly like a measurement that was taken. So the specs below are
 * mostly about absence surviving the parse.
 *
 * The wire bodies are snake_case because that is what `core/approvals/delta.py` writes into the
 * receipt's JSONB, and they are read through the real parser rather than hand-built as
 * `ChangeDelta` values — a fixture that bypassed it could describe a delta no runner can publish.
 */

/** The two fields every delta carries. Everything else is a facet a family fills or leaves out. */
const MINIMAL = {
  identity: { server: 'alpha', username: 'acmeco' },
  verification: VERIFICATION_VERIFIED,
}

/** One delta filling every facet at once, which `whm_firewall_release_and_allow` comes closest to. */
const FULL = {
  identity: { server: 'alpha', address: '203.0.113.7' },
  verification: 'unavailable',
  verification_cause: 'backend_silent',
  changed_fields: [{ field: 'verdict', old: 'blocked', new: 'allowed' }],
  list_delta: { added: ['203.0.113.7'], removed: [], total_entries: 12 },
  backends: [
    { name: 'csf', driven: true, answered: true, verdict: 'allowed' },
    { name: 'iptables', driven: true, answered: false, error_code: 'ssh_sudo_required' },
  ],
  unanswered: ['iptables'],
  delivered_credential: 'https://yopass.internal/#/s/abc',
  new_values: { expires_at: '2026-08-08T11:00:00+00:00' },
  bound: { total: 20, truncated: true },
}

describe('parseChangeDelta', () => {
  it('maps every facet the runner sends, snake to camel', () => {
    const delta = parseChangeDelta(FULL)

    expect(delta).toEqual({
      identity: { server: 'alpha', address: '203.0.113.7' },
      verification: 'unavailable',
      verificationCause: 'backend_silent',
      changedFields: [{ field: 'verdict', old: 'blocked', new: 'allowed' }],
      listDelta: { added: ['203.0.113.7'], removed: [], totalEntries: 12 },
      backends: [
        { name: 'csf', driven: true, answered: true, verdict: 'allowed', errorCode: null },
        {
          name: 'iptables',
          driven: true,
          answered: false,
          verdict: null,
          errorCode: 'ssh_sudo_required',
        },
      ],
      unanswered: ['iptables'],
      deliveredCredential: 'https://yopass.internal/#/s/abc',
      newValues: { expires_at: '2026-08-08T11:00:00+00:00' },
      bound: { total: 20, truncated: true },
    })
  })

  it('leaves every absent facet absent', () => {
    // The rule the whole shape rests on: a facet the runner omitted is one nothing measured, and
    // `{}` or `[]` in its place is a measurement this app invented. Asserted facet by facet, not
    // by comparing against a whole object, so a facet added later with a fabricated default is
    // still caught here rather than passing as "the shape changed".
    const delta = parseChangeDelta(MINIMAL)

    expect(delta?.identity).toEqual({ server: 'alpha', username: 'acmeco' })
    expect(delta?.verification).toBe('verified')
    expect(delta?.verificationCause).toBeNull()
    expect(delta?.changedFields).toBeNull()
    expect(delta?.listDelta).toBeNull()
    expect(delta?.backends).toBeNull()
    expect(delta?.unanswered).toBeNull()
    expect(delta?.deliveredCredential).toBeNull()
    expect(delta?.newValues).toBeNull()
    expect(delta?.bound).toBeNull()
  })

  it('reads an empty changed_fields as an empty list', () => {
    // "Nothing moved, and NOA has grounds for saying so" — a no-op that was re-read, or a branch
    // that failed before issuing a write.
    expect(parseChangeDelta({ ...MINIMAL, changed_fields: [] })?.changedFields).toEqual([])
  })

  it('reads an absent changed_fields as no answer at all', () => {
    // The other half of the pair, and the point of writing the two as separate specs: this is
    // "NOA cannot say", and a parser that collapsed it into the `[]` above would publish an
    // explicit "nothing changed" about a write that may well have landed.
    expect(parseChangeDelta(MINIMAL)?.changedFields).toBeNull()
    expect(parseChangeDelta({ ...MINIMAL, changed_fields: [] })?.changedFields).not.toBeNull()
  })

  it('carries a verification state this build has never heard of, verbatim', () => {
    // Never narrowed to the four known ones. An unknown state must reach the screen as itself and
    // be undecidable there; mapping it to `verified` would be the one direction that fails open,
    // and mapping it to "unknown" would leave the card and the audit row printing different words
    // for one value.
    expect(parseChangeDelta({ ...MINIMAL, verification: 'partially_rolled_back' })?.verification)
      .toBe('partially_rolled_back')
  })

  it.each([
    null,
    undefined,
    42,
    'verified',
    [],
    {},
    { verification: 'verified' },
    { identity: {}, verification: 'verified' },
    { identity: { server: 'alpha' } },
    { identity: { server: 'alpha' }, verification: 7 },
  ])('reads %o as no delta rather than throwing', (value: unknown) => {
    // A receipt carrying something that is not a delta renders as a receipt with no delta, which
    // is a state this card already has. The two required fields are required for a reason: a
    // delta that cannot say what it is about describes nothing, and one that cannot say whether
    // it was confirmed is exactly the claim a partial answer must not be allowed to make.
    expect(parseChangeDelta(value)).toBeNull()
  })

  it('drops a facet it cannot read rather than inventing a measurement', () => {
    // A malformed facet answers "NOA cannot say", never an empty list — which would be the
    // explicit claim that nothing moved.
    const delta = parseChangeDelta({
      ...MINIMAL,
      changed_fields: 'moved',
      list_delta: 'added one',
      backends: { csf: true },
      bound: { truncated: true },
    })

    expect(delta?.changedFields).toBeNull()
    expect(delta?.listDelta).toBeNull()
    expect(delta?.backends).toBeNull()
    expect(delta?.bound).toBeNull()
  })

  it('reads a list of rows it cannot read as no answer, never as nothing moved', () => {
    // The fail-open this shape exists to prevent. A non-empty `changed_fields` whose every row is
    // unreadable is a measurement that arrived and cannot be read — and `[]` would publish the
    // runner's explicit "I compared, and nothing moved", which is a claim about the machine that
    // nobody made. The card prints that as "Nothing changed", so the fabrication reaches a screen.
    expect(parseChangeDelta({ ...MINIMAL, changed_fields: [{ old: 1, new: 2 }] })?.changedFields)
      .toBeNull()

    // Same rule one facet over: an empty backend list says the change was driven through no source
    // at all, which is not what "sources were reported and cannot be read" means.
    expect(parseChangeDelta({ ...MINIMAL, backends: [{ driven: true, answered: true }] })?.backends)
      .toBeNull()

    // And the separating case, restated here because the two inputs are one line apart in the
    // parser: an empty list is still the runner's own "nothing moved" and survives as `[]`.
    expect(parseChangeDelta({ ...MINIMAL, changed_fields: [] })?.changedFields).toEqual([])
  })

  it('drops a row nothing can name', () => {
    // A change to a field with no name, and a backend with no name, are rows an operator cannot
    // act on. The named ones beside them still render — a bad row is not grounds for dropping the
    // measurement it arrived with.
    const delta = parseChangeDelta({
      ...MINIMAL,
      changed_fields: [{ old: 1, new: 2 }, { field: 'suspended', old: false, new: true }],
      backends: [{ driven: true, answered: true }, { name: 'csf', driven: true, answered: true }],
    })

    expect(delta?.changedFields).toEqual([{ field: 'suspended', old: false, new: true }])
    expect(delta?.backends?.map((backend) => backend.name)).toEqual(['csf'])
  })

  it('reads a backend that cannot say it answered as one that did not', () => {
    // Fail-closed on both booleans: silence is not evidence the change took.
    const delta = parseChangeDelta({
      ...MINIMAL,
      backends: [{ name: 'csf', driven: 'yes', answered: 'yes' }],
    })

    expect(delta?.backends).toEqual([
      { name: 'csf', driven: false, answered: false, verdict: null, errorCode: null },
    ])
  })

  it('reads a list the runner did not count to the end as uncounted', () => {
    // `total_entries` is omitted when the runner read only the lines matching its target, and a
    // `0` there would say the list is empty.
    const delta = parseChangeDelta({ ...MINIMAL, list_delta: { added: ['1.2.3.4'], removed: [] } })

    expect(delta?.listDelta).toEqual({ added: ['1.2.3.4'], removed: [], totalEntries: null })
  })
})
