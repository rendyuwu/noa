/**
 * One card as an `ApprovalCard`, for the two suites that assert what the copied block says.
 *
 * Shared rather than rebuilt on both sides, unlike the body-shaped fixtures beside this file: those
 * go through the real parser and describe what the API sends, and a second copy of a *wire* body
 * could describe a card the API can never produce. This one is already the parsed shape and its
 * only job is to be the same card in both suites, so that a case moved between them keeps meaning
 * the same thing.
 */

import type { ApprovalCard } from '@/lib/approvals/card'
import type { ChangeDelta } from '@/lib/approvals/delta'

export const REQUEST_ID = '9f1c2b7e-0000-4000-8000-000000000000'
export const RUN_ID = '7c2f0a11-0000-4000-8000-000000000001'
export const LIBRECHAT_USER_ID = '65f1a0c3d9e4b2a7f0c1d2e3'
export const CONVERSATION_REF = 'conv-2f8a41'

/** The account gate's evidence: two composed strings, a machine, and no heading to draw a block. */
export const EVIDENCE: Record<string, unknown> = {
  headline: 'Suspend an account — alice',
  asked: 'suspend the alice account on whm-lab-1',
  server: 'whm-lab-1',
}

/** A firewall gate's: a heading, the firewall's own lines, and the bound they were read under. */
export const FIREWALL_EVIDENCE: Record<string, unknown> = {
  headline: 'Unblock an IP — 203.0.113.44',
  asked: 'remove 203.0.113.44 from the deny lists on whm-lab-1 and allow it for 60 minutes',
  evidence_heading: 'Why it was blocked',
  server: 'whm-lab-1',
  firewall: {
    matches: ['DENY  203.0.113.44 # lfd', 'DENY  203.0.113.44 # manual'],
    total_matches: 34,
    truncated: true,
  },
}

export function summaryCard(overrides: Partial<ApprovalCard> = {}): ApprovalCard {
  return {
    actionRequestId: REQUEST_ID,
    toolName: 'whm_suspend_account',
    status: 'APPROVED',
    conversationRef: CONVERSATION_REF,
    requester: { email: 'ops@example.com', librechatUserId: LIBRECHAT_USER_ID },
    arguments: { server: 'whm-lab-1', username: 'alice' },
    evidence: EVIDENCE,
    createdAt: '2026-09-09T06:48:01Z',
    expiresAt: '2026-09-09T07:03:01Z',
    decidedAt: '2026-09-09T06:52:10Z',
    run: {
      toolRunId: RUN_ID,
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

/** A confirmed reading — the state whose corner carries no qualification at all. */
export function summaryDelta(overrides: Partial<ChangeDelta> = {}): ChangeDelta {
  return {
    identity: { server: 'whm-lab-1', username: 'alice' },
    verification: 'verified',
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
