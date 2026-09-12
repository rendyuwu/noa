/**
 * The block an operator pastes into a ticket, as a record: its headline, its sentence, its stamps
 * and the identifier it is answered from.
 *
 * What the block says about the **measurement** — the field diffs, the list, the per-backend
 * accounting, the sources that went quiet — is `summary-measurements.test.ts` beside this file.
 * Two files because one ran past this package's 300-line ceiling for a `.ts`, and the seam is the
 * one the builder already has: this file asserts the shape a reader meets, that one asserts what a
 * runner measured.
 *
 * The first case asserts the **whole** text output, line for line, column padding included — so the
 * alignment is measured once, here, and every case after it may ignore it. Everything after asserts
 * whole lines rather than substrings, because the distinctions this builder draws are distinctions
 * between sentences sharing most of their words.
 *
 * Timestamps are compared as rendered strings — safe, because the instants are fixtures and this
 * file reads the wall clock nowhere. The process zone is moved off Jakarta for the reason
 * `lib/format/jakarta-time.test.ts` moves it: a stamp matching on a machine already in the zone
 * proves nothing about a fixed one. The stamps carry no offset, so the zone is named by the heading
 * above them, and a case below asserts that it is named exactly once.
 */

process.env.TZ = 'America/New_York'

import { describe, expect, it } from 'vitest'

import type { ApprovalCard, ApprovalReceipt } from '@/lib/approvals/card'
import type { ChangeDelta } from '@/lib/approvals/delta'

import { buildSummary } from './summary'

const REQUEST_ID = '9f1c2b7e-0000-4000-8000-000000000000'
const RUN_ID = '7c2f0a11-0000-4000-8000-000000000001'
const LIBRECHAT_USER_ID = '65f1a0c3d9e4b2a7f0c1d2e3'
const CONVERSATION_REF = 'conv-2f8a41'
const CREATED = '2026-09-09T06:48:01Z'
const EXPIRES = '2026-09-09T07:03:01Z'
const DECIDED = '2026-09-09T06:52:10Z'
const RUN_DONE = '2026-09-09T06:52:13.400Z'

function card(overrides: Partial<ApprovalCard> = {}): ApprovalCard {
  return {
    actionRequestId: REQUEST_ID,
    toolName: 'whm_suspend_account',
    status: 'APPROVED',
    conversationRef: CONVERSATION_REF,
    requester: { email: 'ops@example.com', librechatUserId: LIBRECHAT_USER_ID },
    arguments: { server: 'whm-lab-1', username: 'alice' },
    evidence: {},
    createdAt: CREATED,
    expiresAt: EXPIRES,
    decidedAt: DECIDED,
    run: {
      toolRunId: RUN_ID,
      status: 'COMPLETED',
      resultSummary: '{"ok": true}',
      createdAt: '2026-09-09T06:52:11Z',
      completedAt: RUN_DONE,
    },
    receipt: null,
    csrf: null,
    ...overrides,
  }
}

function delta(overrides: Partial<ChangeDelta> = {}): ChangeDelta {
  return {
    identity: { server: 'whm-lab-1', username: 'alice' },
    verification: 'verified',
    verificationCause: null,
    changedFields: [{ field: 'suspended', old: false, new: true }],
    listDelta: null,
    backends: null,
    unanswered: null,
    deliveredCredential: null,
    newValues: null,
    bound: null,
    ...overrides,
  }
}

/** One card carrying a receipt, which is the ordinary case every assertion here reads. */
function recorded(after: Record<string, unknown> = {}, over: Partial<ApprovalReceipt> = {}) {
  const receipt = { ok: true, before: {}, after, errorCode: null, delta: delta(), ...over }
  return buildSummary(card({ receipt })).text
}

/** Whole-line membership. A substring match cannot separate the sentences this builder draws. */
function expectLine(text: string, line: string): void {
  expect(text.split('\n')).toContain(`  ${line}`)
}

/** The same, with the column padding collapsed: alignment is asserted once, by the first case. */
function expectRow(text: string, row: string): void {
  expect(text.split('\n').map((line) => line.replace(/\s+/g, ' ').trim())).toContain(row)
}

/** A heading sits at the left margin. Membership here is also the claim that it is one. */
function expectHeading(text: string, heading: string): void {
  expect(text.split('\n')).toContain(heading)
}

it('renders a completed account change exactly, whole output', () => {
  expect(recorded({ message: '`alice` is now suspended.' })).toBe(
    [
      'NOA approval record',
      '',
      'Suspend Account — Approved, Completed',
      '  `alice` is now suspended.',
      '',
      'What changed',
      '  server=whm-lab-1, username=alice',
      '  suspended: no → yes',
      '  Confirmed: NOA looked afterwards and the change is there.',
      '  Unanswered sources: not measured.',
      '',
      'When — all times Jakarta (WIB)',
      '  Requested: 2026-09-09 13:48:01',
      '  Decided:   2026-09-09 13:52:10',
      '  Finished:  2026-09-09 13:52:13',
      '',
      'For support',
      '  Requested by: ops@example.com',
      '  Run id:       7c2f0a11-0000-4000-8000-000000000001',
      '  Tool:         whm_suspend_account',
    ].join('\n'),
  )
})

describe('buildSummary, the headline', () => {
  it('never states the decision without what the change then did', () => {
    // A receipt NOA could not verify, pasted as "Approved", folds the non-answer into the benign
    // value — and the headline is the line a ticket is skimmed for.
    const unverified = recorded({}, { ok: false, delta: delta({ verification: 'unavailable' }) })

    expectHeading(recorded(), 'Suspend Account — Approved, Completed')
    expectHeading(unverified, 'Suspend Account — Approved, Outcome unknown')
  })

  it('says nothing was recorded when an approved request has no receipt at all', () => {
    // The executor died, or the reaper has not written one yet. "Approved" over an empty result
    // reads as a change that happened.
    const summary = buildSummary(card({ receipt: null })).text

    expectHeading(summary, 'Suspend Account — Approved')
    expectLine(summary, 'Nothing has recorded what this change did.')
    expect(summary).not.toContain('Completed')
  })

  it('separates a receipt carrying no delta from no receipt at all', () => {
    const summary = recorded({}, { delta: null, errorCode: 'timeout' })

    expectLine(summary, 'Not measured. No runner published a delta for this request.')
    expectLine(summary, 'Error code: timeout')
    expect(summary).not.toContain('Nothing has recorded')
  })
})

describe('buildSummary, the sentence under the headline', () => {
  it('prints the runner’s own message verbatim, never a phrasing of its own', () => {
    const message = 'Account `alice` on `whm-lab-1` is suspended. 3 sessions were closed.'

    expectLine(recorded({ message, exit_code: 0 }), message)
  })

  it('has no line at all when the payload carried no message, or carried no sentence', () => {
    // `after` is a `Record<string, unknown>`, so a number, a `null` or blank space under that key
    // is representable and none of them is a sentence. The headline is unaffected either way — the
    // verdict is measured rather than written.
    for (const after of [{}, { message: 42 }, { message: null }, { message: '  ' }]) {
      const lines = recorded(after).split('\n')

      expect(lines[2]).toBe('Suspend Account — Approved, Completed')
      expect(lines[3]).toBe('')
    }
  })
})

describe('buildSummary, the three stamps and the one zone', () => {
  it('names the zone exactly once, in a heading, and leaves every stamp bare', () => {
    const zoned = recorded()
      .split('\n')
      .filter((line) => /Jakarta|WIB|\+07/.test(line))

    expect(zoned).toEqual(['When — all times Jakarta (WIB)'])
  })

  it('carries three stamps and nothing derived from them', () => {
    const summary = recorded()
    const stamps = summary.split('\n').filter((line) => /\d{4}-\d\d-\d\d \d\d:\d\d:\d\d/.test(line))

    expect(stamps).toHaveLength(3)
    expect(summary).not.toContain('Run status')
    expect(summary).not.toContain('Run started')
    // The elapsed time was a second victim of two hosts' clocks disagreeing, and it is stated on
    // neither flavour now.
    expect(summary).not.toContain('2.4s')
  })

  it('states the approval window while the request is pending', () => {
    const pending = buildSummary(
      card({ status: 'PENDING', decidedAt: null, run: null, csrf: 'token' }),
    ).text

    expect(pending).toContain('Approval window ends: 2026-09-09 14:03:01')
    expectRow(pending, 'Decided: no decision recorded')
    expectRow(pending, 'Finished: nothing ran')
  })

  it('drops the window from an expired request, which carries a decision stamp of its own', () => {
    // The gate is the status, never a missing `decidedAt`: the expiry sweep writes one when it
    // flips a row, so that condition would drop the line from the one state it exists for.
    const expired = buildSummary(card({ status: 'EXPIRED', run: null })).text

    expect(expired).not.toContain('Approval window ends')
    expectRow(expired, 'Decided: 2026-09-09 13:52:10')
  })
})

it('carries the identifier an operator quotes, and leaves the three an admin looks up', () => {
  // The audit list is keyed on the run id, so it is the one string that gets an answer out of
  // somebody who can open the admin panel. The request id, the conversation reference and the
  // LibreChat account all render in that panel's drawer and are not what a ticket needs.
  const summary = recorded()

  expectRow(summary, 'Run id: 7c2f0a11-0000-4000-8000-000000000001')
  expectRow(summary, 'Requested by: ops@example.com')
  expectRow(summary, 'Tool: whm_suspend_account')
  for (const value of [REQUEST_ID, CONVERSATION_REF, LIBRECHAT_USER_ID]) {
    expect(summary).not.toContain(value)
    expect(buildSummary(card({ receipt: null })).html).not.toContain(value)
  }
})

describe('buildSummary, the html flavour', () => {
  it('says the same things as the text flavour', () => {
    const summary = buildSummary(
      card({ receipt: { ok: true, before: {}, after: {}, errorCode: null, delta: delta() } }),
    ).html

    const headings = [
      'Suspend Account — Approved, Completed',
      'What changed',
      'When — all times Jakarta (WIB)',
      'For support',
    ]
    for (const head of headings) expect(summary).toContain(`<strong>${head}</strong>`)
    expect(summary).toContain('<li>suspended: no → yes</li>')
    expect(summary).toContain('<li>Requested: 2026-09-09 13:48:01</li>')
  })

  it('escapes API-supplied values, which the copy component injects as markup', () => {
    const message = '<img src=x onerror="alert(1)">&'
    const summary = buildSummary(
      card({
        receipt: { ok: true, before: {}, after: { message }, errorCode: null, delta: delta() },
      }),
    )

    expect(summary.html).toContain('&lt;img src=x onerror="alert(1)"&gt;&amp;')
    expect(summary.html).not.toContain('<img')
    // The text flavour is never markup, so it carries the value as typed.
    expectLine(summary.text, message)
  })
})
