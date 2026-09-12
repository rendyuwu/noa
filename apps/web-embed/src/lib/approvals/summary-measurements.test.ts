/**
 * What the copied block says about the **measurement** a CHANGE runner published.
 *
 * The record's own shape — headline, sentence, stamps, identifiers, both flavours — is
 * `summary.test.ts` beside this file, and that file asserts one whole output line for line. Two
 * files because one ran past this package's 300-line ceiling for a `.ts`; the seam is the builder's
 * own, and the cases here are the ones that would otherwise be simplified away by somebody reading
 * a delta as a bag of optional fields rather than as a set of claims.
 *
 * Every assertion is a whole line. The distinctions this builder exists to keep are distinctions
 * between two sentences sharing most of their words — "none, the runner compared" and "not
 * measured" both contain the word a substring match would find — so a `toContain` on a fragment
 * would pass against the fold it is here to catch.
 */

process.env.TZ = 'America/New_York'

import { describe, expect, it } from 'vitest'

import type { ApprovalCard, ApprovalReceipt } from '@/lib/approvals/card'
import type { ChangeDelta } from '@/lib/approvals/delta'

import { type Summary, buildSummary } from './summary'

function card(overrides: Partial<ApprovalCard> = {}): ApprovalCard {
  return {
    actionRequestId: '9f1c2b7e-0000-4000-8000-000000000000',
    toolName: 'whm_suspend_account',
    status: 'APPROVED',
    conversationRef: 'conv-2f8a41',
    requester: { email: 'ops@example.com', librechatUserId: '65f1a0c3d9e4b2a7f0c1d2e3' },
    arguments: { server: 'whm-lab-1', username: 'alice' },
    evidence: {},
    createdAt: '2026-09-09T06:48:01Z',
    expiresAt: '2026-09-09T07:03:01Z',
    decidedAt: '2026-09-09T06:52:10Z',
    run: {
      toolRunId: '7c2f0a11-0000-4000-8000-000000000001',
      status: 'COMPLETED',
      resultSummary: '{"ok": true}',
      createdAt: '2026-09-09T06:52:11Z',
      completedAt: '2026-09-09T06:52:13.400Z',
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

/** One card whose run published this delta. */
function measured(
  over: Partial<ChangeDelta> = {},
  receiptOver: Partial<ApprovalReceipt> = {},
  cardOver: Partial<ApprovalCard> = {},
): Summary {
  const receipt: ApprovalReceipt = {
    ok: true,
    before: {},
    after: {},
    errorCode: null,
    delta: delta(over),
    ...receiptOver,
  }
  return buildSummary(card({ receipt, ...cardOver }))
}

/** Whole-line membership. A substring match cannot separate the sentences this builder draws. */
function expectLine(text: string, line: string): void {
  expect(text.split('\n')).toContain(`  ${line}`)
}

describe('a firewall change driven through several backends', () => {
  const summary = measured(
    {
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
    },
    { ok: false, errorCode: 'postflight_partial' },
    { toolName: 'whm_firewall_release_and_allow' },
  ).text

  it('states each backend on its own line', () => {
    // Driven and answered are two facts about two moments, and they fail apart: a backend that ran
    // the commands and then went quiet is the case one boolean could not state.
    expectLine(summary, 'Backend csf: driven, answered, verdict allowed')
    expectLine(summary, 'Backend firewalld: driven, no answer, error ssh_sudo_required')
  })

  it('names the source that did not answer, never counts it', () => {
    expectLine(summary, 'Unanswered sources: firewalld')
    expect(summary).not.toContain('1 source')
  })

  it('carries the bound the claim rests on, and says it was cut', () => {
    expectLine(
      summary,
      'Evidence bound: 20 entries, truncated — the claim above rests on a capped reading, ' +
        'not on the whole list.',
    )
  })

  it('says what NOA could not look at, and names the cause and the code', () => {
    expectLine(
      summary,
      'Not checked: NOA could not look afterwards, so this is neither confirmed nor ruled out. ' +
        '(postflight_partial)',
    )
    expectLine(summary, 'Error code: postflight_partial')
  })

  it('states the resolved values that have no before twin', () => {
    expectLine(summary, 'New values: expires_at=2026-09-09T08:03:01Z')
  })
})

describe('a mail gateway list change', () => {
  const summary = measured(
    {
      identity: {
        server: 'pmg-lab-1',
        action: 'add',
        target: '203.0.113.0/24',
        normalized_target: '203.0.113.0/24',
      },
      changedFields: [],
      listDelta: { added: ['203.0.113.0/24'], removed: [], totalEntries: 47 },
    },
    {},
    { toolName: 'pmg_whitelist' },
  ).text

  it('prints the identity in the order the runner wrote it, machine first', () => {
    // Both spellings of the target, because one of them would report a host where a network was
    // changed.
    expectLine(
      summary,
      'server=pmg-lab-1, action=add, target=203.0.113.0/24, normalized_target=203.0.113.0/24',
    )
  })

  it('states what entered and what left, and the size it was measured against', () => {
    expectLine(summary, 'List entries added: 203.0.113.0/24')
    expectLine(summary, 'List entries removed: none')
    expectLine(summary, 'List size: 47 entries')
  })
})

describe('the two ways a measurement can be empty', () => {
  const compared = measured({ changedFields: [] }).text
  const unmeasured = measured({ changedFields: null }).text

  it('says nothing moved only when the runner has grounds for saying so', () => {
    expectLine(compared, 'Field changes: none. The runner compared, and nothing moved.')
    expectLine(unmeasured, 'Field changes: not measured. Nothing here says that nothing moved.')
  })

  it('renders the two as different text', () => {
    // The separating case. Without it, both lines above pass against a builder that folds the
    // non-answer into the benign one.
    expect(compared).not.toBe(unmeasured)
  })

  it('separates a list that did not move from a list nobody sized', () => {
    const summary = measured({
      listDelta: { added: [], removed: [], totalEntries: null },
      unanswered: [],
    }).text

    expectLine(summary, 'List entries added: none')
    expectLine(
      summary,
      'List size: not measured. The runner read only the lines matching its target.',
    )
    expectLine(summary, 'Unanswered sources: none. Every source answered.')
  })
})

describe('one renderer for every family of value', () => {
  it('renders booleans as yes and no, wherever the value came from', () => {
    const summary = measured({
      identity: { server: 'whm-lab-1', dedicated_ip: false },
      newValues: { auto_renew: true },
    }).text

    expectLine(summary, 'server=whm-lab-1, dedicated_ip=no')
    expectLine(summary, 'suspended: no → yes')
    expectLine(summary, 'New values: auto_renew=yes')
  })

  it('keeps false, null and a key nobody sent three different answers', () => {
    // The separating set. A renderer folding the absent key into `null`, or either of them into
    // `no`, prints these two lines with the same word on a side where the payloads differ.
    const summary = measured({
      changedFields: [
        { field: 'suspended', old: false, new: true },
        { field: 'reseller', old: undefined, new: null },
      ],
    }).text

    expectLine(summary, 'suspended: no → yes')
    expectLine(summary, 'reseller: not recorded → null')
  })

  it('reaches the arguments of a request that never ran', () => {
    const summary = buildSummary(
      card({ status: 'DENIED', run: null, arguments: { server: 'whm-lab-1', force: true } }),
    ).text

    expectLine(summary, 'Target, as requested: server=whm-lab-1, force=yes')
  })
})

it('records a delivered credential and leaves its one-open link out of the paste', () => {
  const url = 'https://yopass.example.com/#/s/8f2a-one-open'
  const summary = measured(
    { changedFields: null, deliveredCredential: url },
    {},
    { toolName: 'proxmox_reset_vm_password' },
  )

  // The link opens once. In a ticket it is spent by whoever reads the ticket first, and the
  // operator who needs it finds a dead URL.
  expect(summary.text).toContain('A credential was delivered by one-open link')
  expect(summary.text).not.toContain(url)
  expect(summary.html).not.toContain('yopass')
})

it('names the target of a request nothing ran as requested, never as reached', () => {
  const summary = buildSummary(card({ status: 'DENIED', receipt: null, run: null })).text

  expect(summary.split('\n')).toContain('Suspend Account — Denied')
  expectLine(summary, 'Target, as requested: server=whm-lab-1, username=alice')
  expectLine(summary, 'Nothing has recorded what this change did.')
  expect(summary.replace(/\s+/g, ' ')).toContain('Finished: nothing ran')
})
