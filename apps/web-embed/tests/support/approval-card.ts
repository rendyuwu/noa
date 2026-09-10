import { type ApprovalCard, parseApprovalCard } from '@/lib/approvals/card'

/**
 * One approval card, in the shapes the specs need (§T.41, §T.42).
 *
 * Two suites read a card now — the poll (`lib/approvals/poll.test.ts`) and the card that runs it
 * (`app/approvals/[id]/card-view.test.tsx`) — and a second copy of this body would be a second
 * answer to what the API sends, free to drift on one side only. The e2e stub
 * (`e2e/support/upstream-stub.mjs`) holds the same shape for the browser lane; it is a separate
 * process and cannot import this, so the two are kept deliberately alike.
 *
 * Bodies are snake_case because that is the HTTP contract (§I.embed), and they go through the real
 * `parseApprovalCard` rather than being hand-built as `ApprovalCard` values — a fixture that
 * bypassed the parser could describe a card the API can never send.
 */

export const CARD_ID = '9f1c2b7e-0000-4000-8000-000000000000'
export const CARD_CSRF = 'v1.1786000000.signature'
export const TOOL_RUN_ID = '5c2f1a90-0000-4000-8000-000000000001'

/** A request still awaiting its answer: live token, no run, nothing decided. */
const PENDING_BODY: Record<string, unknown> = {
  action_request_id: CARD_ID,
  tool_name: 'whm_suspend_account',
  status: 'PENDING',
  conversation_ref: '1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12',
  requester: { email: 'operator@noa.internal', librechat_user_id: 'librechat-user-1' },
  arguments: { server_ref: 'alpha', account: 'acmeco' },
  evidence: { suspended: false, domain: 'acme.example' },
  created_at: '2026-08-08T09:00:00+00:00',
  expires_at: '2026-08-08T10:00:00+00:00',
  decided_at: null,
  run: null,
  receipt: null,
  csrf: CARD_CSRF,
}

/**
 * The after-state half of a receipt (§T.38, §T.42(b)).
 *
 * Shares no value with `evidence` above, deliberately: the claim these suites make is that the
 * card renders *both* halves, and a fixture whose halves overlapped could not tell that from one
 * rendering the same half twice.
 */
export const RECEIPT_AFTER: Record<string, unknown> = {
  ok: true,
  suspended: true,
  suspended_at: '2026-08-08T09:31:00+00:00',
}

/** What the API sends under `receipt` once a change has recorded an outcome. */
export function receiptBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    // The gate-time preflight, the same payload the pending card carries as `evidence` — that is
    // what T38's writer copies onto the receipt.
    before: { suspended: false, domain: 'acme.example' },
    after: RECEIPT_AFTER,
    error_code: null,
    ...overrides,
  }
}

/** The `tool_runs` half of the card. `STARTED` by default: that is what an approve opens. */
export function runBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    tool_run_id: TOOL_RUN_ID,
    status: 'STARTED',
    result_summary: null,
    created_at: '2026-08-08T09:30:00+00:00',
    completed_at: null,
    ...overrides,
  }
}

export function cardBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...PENDING_BODY, ...overrides }
}

/**
 * An approved request and the run it started.
 *
 * `csrf` is `null` because the API sends none once nothing may be decided — a fixture that
 * kept the token would let a spec pass against a card showing live buttons after the decision.
 */
export function approvedBody(
  runOverrides: Record<string, unknown> = {},
  receipt: Record<string, unknown> | null = null,
): Record<string, unknown> {
  return cardBody({
    status: 'APPROVED',
    decided_at: '2026-08-08T09:30:00+00:00',
    csrf: null,
    run: runBody(runOverrides),
    // `null` by default, because that is what a card carries while its run is still in flight:
    // the receipt lands with the terminal write, not with the decision.
    receipt,
  })
}

export function approvalCard(overrides: Record<string, unknown> = {}): ApprovalCard {
  const card = parseApprovalCard(cardBody(overrides))
  if (card === null) throw new Error('fixture body is not a card')
  return card
}
