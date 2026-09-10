/**
 * The block an operator pastes into a ticket.
 *
 * The first case asserts the **whole** text output, line for line. Everything after it asserts
 * whole lines rather than substrings, because the distinctions this builder exists to preserve are
 * distinctions between two sentences sharing most of their words: "none, the runner compared" and
 * "not measured" both contain the word a substring match would find.
 *
 * Timestamps are compared as rendered strings, offset included — safe, because the instants are
 * fixtures and this file reads the wall clock nowhere. The process zone is moved off Jakarta for
 * the reason `lib/format/jakarta-time.test.ts` moves it: a stamp matching on a machine already in
 * the zone proves nothing about a fixed one.
 */

process.env.TZ = 'America/New_York'

import { describe, expect, it } from 'vitest'

import type { ApprovalCard, ApprovalReceipt } from '@/lib/approvals/card'
import type { ChangeDelta } from '@/lib/approvals/delta'

import { buildSummary } from './summary'

const REQUEST_ID = '9f1c2b7e-0000-4000-8000-000000000000'
const RUN_ID = '7c2f0a11-0000-4000-8000-000000000001'
const CREATED = '2026-09-09T06:48:01Z'
const EXPIRES = '2026-09-09T07:03:01Z'
const DECIDED = '2026-09-09T06:52:10Z'
const RUN_STARTED = '2026-09-09T06:52:11Z'
const RUN_DONE = '2026-09-09T06:52:13.400Z'

function card(overrides: Partial<ApprovalCard> = {}): ApprovalCard {
  return {
    actionRequestId: REQUEST_ID,
    toolName: 'whm_suspend_account',
    status: 'APPROVED',
    conversationRef: 'conv-2f8a41',
    requester: { email: 'ops@example.com', librechatUserId: '65f1a0c3d9e4b2a7f0c1d2e3' },
    arguments: { server: 'whm-lab-1', username: 'alice' },
    evidence: {},
    createdAt: CREATED,
    expiresAt: EXPIRES,
    decidedAt: DECIDED,
    run: {
      toolRunId: RUN_ID,
      status: 'COMPLETED',
      resultSummary: '{"ok": true}',
      createdAt: RUN_STARTED,
      completedAt: RUN_DONE,
    },
    receipt: null,
    csrf: null,
    ...overrides,
  }
}

function receipt(delta: ChangeDelta | null, over: Partial<ApprovalReceipt> = {}): ApprovalReceipt {
  return { ok: true, before: {}, after: {}, errorCode: null, delta, ...over }
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

/** Whole-line membership. A substring match cannot separate the sentences this builder draws. */
function expectLine(text: string, line: string): void {
  expect(text.split('\n')).toContain(`  ${line}`)
}

it('renders an approved account change exactly, whole output', () => {
  expect(buildSummary(card({ receipt: receipt(delta()) })).text).toBe(
    [
      'NOA approval record',
      '',
      'Change',
      '  Tool: whm_suspend_account',
      '  Status: Approved',
      '  Target: server=whm-lab-1, username=alice',
      '',
      'What moved',
      '  Verification: verified',
      '  suspended: false → true',
      '',
      'Result',
      '  The change completed.',
      '  Unanswered sources: not measured.',
      '',
      'Timing',
      '  Requested: 2026-09-09 13:48:01 +07:00',
      '  Approval window ends: 2026-09-09 14:03:01 +07:00',
      '  Decided: 2026-09-09 13:52:10 +07:00',
      '  Run status: COMPLETED',
      '  Run started: 2026-09-09 13:52:11 +07:00',
      '  Run completed: 2026-09-09 13:52:13 +07:00 (2.4s)',
      '',
      'Reference',
      '  Request id: 9f1c2b7e-0000-4000-8000-000000000000',
      '  Run id: 7c2f0a11-0000-4000-8000-000000000001',
      '  LibreChat account: ops@example.com (user 65f1a0c3d9e4b2a7f0c1d2e3)',
      '  Conversation: conv-2f8a41',
    ].join('\n'),
  )
})

describe('buildSummary, a firewall change driven through several backends', () => {
  const summary = buildSummary(
    card({
      toolName: 'whm_firewall_release_and_allow',
      arguments: { server: 'whm-lab-1', target: '203.0.113.44' },
      receipt: receipt(
        delta({
          identity: { server: 'whm-lab-1', target: '203.0.113.44' },
          verification: 'unavailable',
          verificationCause: 'postflight_partial',
          changedFields: null,
          backends: [
            { name: 'csf', driven: true, answered: true, verdict: 'allowed', errorCode: null },
            {
              name: 'firewalld',
              driven: true,
              answered: false,
              verdict: null,
              errorCode: 'ssh_sudo_required',
            },
          ],
          unanswered: ['firewalld'],
          newValues: { expires_at: '2026-09-09T08:03:01Z' },
          bound: { total: 20, truncated: true },
        }),
        { ok: false, errorCode: 'postflight_partial' },
      ),
    }),
  )

  it('states each backend on its own line', () => {
    expectLine(summary.text, 'Backend csf: driven, answered, verdict allowed')
    expectLine(summary.text, 'Backend firewalld: driven, no answer, error ssh_sudo_required')
  })

  it('names the source that did not answer, never counts it', () => {
    expectLine(summary.text, 'Unanswered sources: firewalld')
    expect(summary.text).not.toContain('1 source')
  })

  it('carries the bound the claim rests on, and says it was cut', () => {
    expectLine(
      summary.text,
      'Evidence bound: 20 entries, truncated — the claim above rests on a capped reading, ' +
        'not on the whole list.',
    )
  })

  it('does not report a failed change as completed', () => {
    expectLine(summary.text, 'The change did not complete.')
    expectLine(summary.text, 'Error code: postflight_partial')
    expectLine(summary.text, 'Verification: unavailable (postflight_partial)')
  })

  it('states the resolved values that have no before twin', () => {
    expectLine(summary.text, 'New values: expires_at=2026-09-09T08:03:01Z')
  })
})

describe('buildSummary, a mail gateway list change', () => {
  const summary = buildSummary(
    card({
      toolName: 'pmg_whitelist',
      receipt: receipt(
        delta({
          identity: {
            server: 'pmg-lab-1',
            action: 'add',
            target: '203.0.113.0/24',
            normalized_target: '203.0.113.0/24',
          },
          changedFields: [],
          listDelta: { added: ['203.0.113.0/24'], removed: [], totalEntries: 47 },
        }),
      ),
    }),
  )

  it('prints the identity in the order the runner wrote it, machine first', () => {
    expectLine(
      summary.text,
      'Target: server=pmg-lab-1, action=add, target=203.0.113.0/24, ' +
        'normalized_target=203.0.113.0/24',
    )
  })

  it('states what entered and what left, and the size it was measured against', () => {
    expectLine(summary.text, 'List entries added: 203.0.113.0/24')
    expectLine(summary.text, 'List entries removed: none')
    expectLine(summary.text, 'List size: 47 entries')
  })
})

describe('buildSummary, the two ways a measurement can be empty', () => {
  const compared = buildSummary(card({ receipt: receipt(delta({ changedFields: [] })) }))
  const unmeasured = buildSummary(card({ receipt: receipt(delta({ changedFields: null })) }))

  it('says nothing moved only when the runner has grounds for saying so', () => {
    expectLine(compared.text, 'Field changes: none. The runner compared, and nothing moved.')
    expectLine(unmeasured.text, 'Field changes: not measured. Nothing here says that nothing moved.')
  })

  it('renders the two as different text', () => {
    // The separating case. Without it, both lines above pass against a builder that folds the
    // non-answer into the benign one.
    expect(compared.text).not.toBe(unmeasured.text)
  })

  it('separates a list that did not move from a list nobody sized', () => {
    const summary = buildSummary(
      card({
        receipt: receipt(
          delta({ listDelta: { added: [], removed: [], totalEntries: null }, unanswered: [] }),
        ),
      }),
    )

    expectLine(summary.text, 'List entries added: none')
    expectLine(
      summary.text,
      'List size: not measured. The runner read only the lines matching its target.',
    )
    expectLine(summary.text, 'Unanswered sources: none. Every source answered.')
  })
})

it('records a delivered credential and leaves its one-open link out of the paste', () => {
  const url = 'https://yopass.example.com/#/s/8f2a-one-open'
  const summary = buildSummary(
    card({
      toolName: 'proxmox_reset_vm_password',
      receipt: receipt(delta({ changedFields: null, deliveredCredential: url })),
    }),
  )

  // The link opens once. In a ticket it is spent by whoever reads the ticket first, and the
  // operator who needs it finds a dead URL.
  expect(summary.text).toContain('A credential was delivered by one-open link')
  expect(summary.text).not.toContain(url)
  expect(summary.html).not.toContain('yopass')
})

describe('buildSummary, a request nothing ran', () => {
  const summary = buildSummary(card({ status: 'DENIED', receipt: null, run: null }))

  // A denied request never ran, and nothing here may read as a machine that was reached.
  it('names the target as requested, and every absence as an absence', () => {
    expectLine(summary.text, 'Target, as requested: server=whm-lab-1, username=alice')
    expectLine(summary.text, 'Status: Denied')
    expectLine(summary.text, 'Nothing has recorded what this change did.')
    expectLine(summary.text, 'Not measured. No runner published a delta for this request.')
    expectLine(summary.text, 'Run: none started')
    expectLine(summary.text, 'Run id: none')
  })

  it('still carries the timestamps and the request id a ticket is searched by', () => {
    expectLine(summary.text, 'Requested: 2026-09-09 13:48:01 +07:00')
    expectLine(summary.text, `Request id: ${REQUEST_ID}`)
  })
})

describe('buildSummary, the html flavour', () => {
  it('says the same things as the text flavour', () => {
    const summary = buildSummary(card({ receipt: receipt(delta()) }))

    const headings = ['Change', 'What moved', 'Result', 'Timing', 'Reference']
    for (const head of headings) expect(summary.html).toContain(`<strong>${head}</strong>`)
    expect(summary.html).toContain('<li>suspended: false → true</li>')
    expect(summary.html).toContain('<li>Requested: 2026-09-09 13:48:01 +07:00</li>')
  })

  it('escapes API-supplied values, which the copy component injects as markup', () => {
    const summary = buildSummary(
      card({ conversationRef: '<img src=x onerror="alert(1)">&', receipt: receipt(delta()) }),
    )

    expect(summary.html).toContain('&lt;img src=x onerror="alert(1)"&gt;&amp;')
    expect(summary.html).not.toContain('<img')
    // The text flavour is never markup, so it carries the value as typed.
    expectLine(summary.text, 'Conversation: <img src=x onerror="alert(1)">&')
  })
})
