import { describe, expect, it } from 'vitest'

import {
  APPROVAL_STATUSES,
  type ApprovalCard,
  canDecide,
  parseApprovalCard,
  statusLabel,
} from './card'

/**
 * Parsing the API's card body (§T.41).
 *
 * The two properties worth separating here are opposites, and both are deliberate: parsing is
 * permissive so a missing field renders as unknown rather than as a blank iframe, while
 * `canDecide` is fail-closed so anything unrecognised renders read-only.
 */

const BODY = {
  action_request_id: '9f1c2b7e-0000-4000-8000-000000000000',
  tool_name: 'whm_suspend_account',
  status: 'PENDING',
  conversation_ref: '1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12',
  requester: { email: 'operator@noa.internal', librechat_user_id: 'librechat-user-1' },
  arguments: { server_ref: 'alpha', account: 'acmeco' },
  evidence: { account: 'acmeco', suspended: false },
  created_at: '2026-08-08T09:00:00+00:00',
  expires_at: '2026-08-08T10:00:00+00:00',
  decided_at: null,
  run: null,
  receipt: null,
  csrf: 'v1.1786000000.signature',
}

/** What the API sends once a change has recorded an outcome (§T.38, V46). */
const RECEIPT = {
  ok: true,
  before: { suspended: false },
  after: { suspended: true, suspended_at: '2026-08-08T09:31:00+00:00' },
  error_code: null,
}

function parsed(overrides: Record<string, unknown> = {}): ApprovalCard {
  const card = parseApprovalCard({ ...BODY, ...overrides })
  if (card === null) throw new Error('expected a card')
  return card
}

describe('parseApprovalCard', () => {
  it('maps every field the API sends', () => {
    const card = parsed()

    expect(card.actionRequestId).toBe(BODY.action_request_id)
    expect(card.toolName).toBe('whm_suspend_account')
    expect(card.status).toBe('PENDING')
    expect(card.conversationRef).toBe(BODY.conversation_ref)
    expect(card.requester).toEqual({
      email: 'operator@noa.internal',
      librechatUserId: 'librechat-user-1',
    })
    expect(card.arguments).toEqual(BODY.arguments)
    expect(card.evidence).toEqual(BODY.evidence)
    expect(card.createdAt).toBe(BODY.created_at)
    expect(card.expiresAt).toBe(BODY.expires_at)
    expect(card.decidedAt).toBeNull()
    expect(card.run).toBeNull()
    expect(card.receipt).toBeNull()
    expect(card.csrf).toBe(BODY.csrf)
  })

  it('carries the evidence the card exists to show', () => {
    // Asserted on a value, not on a key: the before-state is the one thing on this row the model
    // is never told, and the card is the only surface that renders it.
    expect(parsed({ evidence: { before_state: 'suspended=false' } }).evidence).toEqual({
      before_state: 'suspended=false',
    })
  })

  it('maps a run when there is one', () => {
    const card = parsed({
      status: 'APPROVED',
      decided_at: '2026-08-08T09:30:00+00:00',
      run: {
        tool_run_id: 'run-1',
        status: 'STARTED',
        result_summary: null,
        created_at: '2026-08-08T09:30:00+00:00',
        completed_at: null,
      },
      csrf: null,
    })

    expect(card.run).toEqual({
      toolRunId: 'run-1',
      status: 'STARTED',
      resultSummary: null,
      createdAt: '2026-08-08T09:30:00+00:00',
      completedAt: null,
    })
  })

  it('drops a run it cannot name', () => {
    // A run with no id is one nothing can poll (§T.42), so it is not a run.
    expect(parsed({ run: { status: 'STARTED' } }).run).toBeNull()
  })

  it('keeps the receipt as two halves (§T.42(b), V46, DECISIONS §6.5)', () => {
    // The requirement is that before and after stay separable all the way to the render. A parser
    // that merged them, or kept only the one it thought was the outcome, goes red here — and the
    // two payloads share no value, so this cannot pass by carrying one of them twice.
    const card = parsed({ receipt: RECEIPT })

    expect(card.receipt).toEqual({
      ok: true,
      before: { suspended: false },
      after: { suspended: true, suspended_at: '2026-08-08T09:31:00+00:00' },
      errorCode: null,
    })
    expect(card.receipt?.before).not.toEqual(card.receipt?.after)
  })

  it('carries the named cause when a change did not complete', () => {
    const card = parsed({
      receipt: { ...RECEIPT, ok: false, error_code: 'ssh_sudo_required' },
    })

    expect(card.receipt?.ok).toBe(false)
    expect(card.receipt?.errorCode).toBe('ssh_sudo_required')
    // The half that must not disappear on a failure: what the operator authorised against.
    expect(card.receipt?.before).toEqual({ suspended: false })
  })

  it.each([null, undefined, 'done', 42, []])('reads %o as no receipt', (receipt: unknown) => {
    // `null` is what the API sends until something records an outcome, and anything else is a body
    // it cannot send — both render as no outcome section rather than an empty one.
    expect(parsed({ receipt }).receipt).toBeNull()
  })

  it('fails closed on an ok field that is not true', () => {
    // A truthy string read as success would tell an operator a change landed on the evidence that
    // something non-boolean was in the field. Halves that are not objects render as empty.
    const card = parsed({ receipt: { ok: 'yes', before: 'not-a-mapping', after: null } })

    expect(card.receipt?.ok).toBe(false)
    expect(card.receipt?.before).toEqual({})
    expect(card.receipt?.after).toEqual({})
    expect(card.receipt?.errorCode).toBeNull()
  })

  it('renders unknown fields as unknown rather than throwing', () => {
    // V38: a blank card is not an acceptable state, and neither is a stack trace in an iframe.
    const card = parsed({
      tool_name: undefined,
      conversation_ref: undefined,
      requester: 'not-an-object',
      arguments: ['not', 'a', 'mapping'],
      evidence: null,
    })

    expect(card.toolName).toBe('')
    expect(card.conversationRef).toBeNull()
    expect(card.requester).toEqual({ email: '', librechatUserId: '' })
    expect(card.arguments).toEqual({})
    expect(card.evidence).toEqual({})
  })

  it.each([null, undefined, 42, 'a string', [], {}, { action_request_id: '' }])(
    'refuses %o as a card',
    (body: unknown) => {
      // No id, no card: the id is the decision path, the poll target and the CSRF binding, so a
      // body without one would render live buttons aimed at nothing.
      expect(parseApprovalCard(body)).toBeNull()
    },
  )

  it('never produces a reason field', () => {
    // The API sends none. This asserts the parser does not invent one either — a `reason` key
    // here would be the first place a render path could read the operator's own words back.
    const card = parsed({ reason: 'smuggled in by a body that should not carry one' })

    expect('reason' in card).toBe(false)
    expect(JSON.stringify(card)).not.toContain('smuggled')
  })
})

describe('canDecide', () => {
  it('is true for a PENDING card with a token', () => {
    expect(canDecide(parsed())).toBe(true)
  })

  it.each(['APPROVED', 'DENIED', 'EXPIRED', 'SOMETHING_NEW'])(
    'is false for status %s',
    (status: string) => {
      // Including a status this build has never heard of: fail-closed is the only safe direction
      // for "may this operator act".
      expect(canDecide(parsed({ status }))).toBe(false)
    },
  )

  it.each([null, undefined, ''])('is false when the token is %o', (csrf: unknown) => {
    // The API sends `null` once nothing may be decided. A card without a live token must not show
    // live buttons — the POST would only ever be refused.
    expect(canDecide(parsed({ csrf }))).toBe(false)
  })
})

describe('statusLabel', () => {
  it.each([...APPROVAL_STATUSES])('has a sentence for %s', (status: string) => {
    expect(statusLabel(status)).not.toBe('')
    expect(statusLabel(status)).not.toBe(status)
  })

  it('shows an unknown status verbatim', () => {
    // So the operator reading the card and the administrator reading the row are looking at the
    // same word, rather than at "Unknown".
    expect(statusLabel('SOMETHING_NEW')).toBe('SOMETHING_NEW')
  })
})
