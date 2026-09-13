/**
 * The block an operator pastes into a ticket.
 *
 * **Its body is the card's, byte for byte**, and that is asserted at the render rather than here
 * (`app/approvals/[id]/card-parity.test.tsx`): the two must not be two statements of one
 * measurement. What this file holds is what the *block* adds and what it refuses — the absolute
 * stamps under a heading naming the zone, the identity a ticket is answered from, and the link it
 * must never carry.
 *
 * The first case asserts the **whole** text output, line for line, column padding included — so the
 * alignment is measured once, here, and every case after it may ignore it. Everything after asserts
 * whole lines rather than substrings, because the distinctions this builder draws are distinctions
 * between sentences sharing most of their words.
 *
 * Timestamps are compared as rendered strings — safe, because the instants are fixtures and this
 * file reads the wall clock nowhere. The process zone is moved off Jakarta in `vitest.config.ts`
 * under `test.env`, for the reason `lib/format/jakarta-time.test.ts` states: a stamp matching on a
 * machine already in the zone proves nothing about a fixed one.
 */

import { describe, expect, it } from 'vitest'

import type { ApprovalReceipt } from '@/lib/approvals/card'

import { buildSummary } from './summary'
import {
  CONVERSATION_REF,
  FIREWALL_EVIDENCE,
  LIBRECHAT_USER_ID,
  REQUEST_ID,
  summaryCard as card,
  summaryDelta as delta,
} from '../../../tests/support/summary-card'

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

it('renders a completed account change exactly, whole output', () => {
  expect(
    recorded({ headline: 'Account suspended — alice', message: '`alice` is now suspended.' }),
  ).toBe(
    [
      'NOA approval record',
      '',
      'Account suspended — alice — Approved',
      '  `alice` is now suspended.',
      '',
      'When — all times Jakarta (WIB)',
      '  Requested: 2026-09-09 13:48:01',
      '  Decided:   2026-09-09 13:52:10',
      '  Finished:  2026-09-09 13:52:13',
      '',
      'For support',
      '  Requested by: ops@example.com',
      '  Run id:       7c2f0a11-0000-4000-8000-000000000001',
    ].join('\n'),
  )
})

describe('the headline', () => {
  it('never states the decision without what the change then did', () => {
    // A receipt NOA could not verify, pasted as "Approved", folds the non-answer into the benign
    // value — and the headline is the line a ticket is skimmed for.
    const unverified = recorded({}, { ok: false, delta: delta({ verification: 'unavailable' }) })

    expect(unverified.split('\n')).toContain('Suspend an account — alice — Approved · not confirmed')
    expect(recorded().split('\n')).toContain('Suspend an account — alice — Approved')
  })

  it('says nothing was recorded when a finished run left no receipt', () => {
    // The executor died, or the reaper has not written one yet. "Approved" over an empty result
    // reads as a change that happened.
    const summary = buildSummary(card({ receipt: null })).text

    expect(summary.split('\n')).toContain('Suspend an account — alice — Approved · nothing recorded')
  })

  it('says a change is running rather than that it recorded nothing', () => {
    // The separating case, and the state it covers is wider than it looks: an approval with no
    // `tool_runs` row at all counts as running, because the row lands in the same transaction as
    // the decision and a card without one is a read that straddled the commit
    // (`lib/approvals/poll.ts`). "Nothing recorded" there would state that a change that has not
    // started is over.
    const summary = buildSummary(card({ receipt: null, run: null })).text

    expect(summary.split('\n')).toContain('Suspend an account — alice — Approved · running')
  })

  it('states the request under the headline until a runner has written a sentence', () => {
    // A denied request never ran, so the block states what was asked and labels it as such. The
    // line must not read as a machine that was touched, and the imperative is what keeps it from
    // doing so — it is the arguments restated, not a prediction.
    const denied = buildSummary(card({ status: 'DENIED', receipt: null, run: null })).text

    expect(denied.split('\n')).toContain('Suspend an account — alice — Denied')
    expectLine(denied, 'Asked: suspend the alice account on whm-lab-1')
  })
})

describe('the stamps and the one zone', () => {
  it('names the zone exactly once, in a heading, and leaves every stamp bare', () => {
    const zoned = recorded()
      .split('\n')
      .filter((line) => /Jakarta|WIB|\+07/.test(line))

    expect(zoned).toEqual(['When — all times Jakarta (WIB)'])
  })

  it('states the approval window as an instant, which the card deliberately cannot', () => {
    // This surface has a heading naming the zone, so a bare stamp may leave the frame on it. The
    // card states the same window as a span beside the buttons instead.
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

describe('what the block is answered from', () => {
  it('carries the run id once there is a run, and substitutes nothing before', () => {
    // The audit list is keyed on the run id, so it is the one string that gets an answer out of
    // somebody who can open the admin panel. A row reading `none` states an identifier that does
    // not exist, and the action-request id is not put in its place.
    expectRow(recorded(), 'Run id: 7c2f0a11-0000-4000-8000-000000000001')

    const pending = buildSummary(card({ status: 'PENDING', run: null, receipt: null })).text
    expect(pending).not.toContain('Run id')
    expect(pending).not.toContain(REQUEST_ID)
    expectRow(pending, 'Requested by: ops@example.com')
  })

  it('leaves the three identifiers an admin looks up off both flavours', () => {
    const summary = recorded()

    for (const value of [REQUEST_ID, CONVERSATION_REF, LIBRECHAT_USER_ID]) {
      expect(summary).not.toContain(value)
      expect(buildSummary(card({ receipt: null })).html).not.toContain(value)
    }
  })

})

describe('the html flavour', () => {
  it('says the same things as the text flavour', () => {
    const summary = buildSummary(
      card({
        evidence: FIREWALL_EVIDENCE,
        receipt: { ok: true, before: {}, after: {}, errorCode: null, delta: delta() },
      }),
    ).html

    for (const head of [
      'Unblock an IP — 203.0.113.44 — Approved',
      'Why it was blocked',
      'When — all times Jakarta (WIB)',
      'For support',
    ]) {
      expect(summary).toContain(`<strong>${head}</strong>`)
    }
    expect(summary).toContain('<li>DENY  203.0.113.44 # lfd</li>')
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
