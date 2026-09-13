import { type ApprovalCard, parseApprovalCard } from '@/lib/approvals/card'

/**
 * One approval card, in the shapes the specs need.
 *
 * Two suites read a card now — the poll (`lib/approvals/poll.test.ts`) and the card that runs it
 * (`app/approvals/[id]/card-view.test.tsx`) — and a second copy of this body would be a second
 * answer to what the API sends, free to drift on one side only. The e2e stub
 * (`e2e/support/upstream-stub.mjs`) holds the same shape for the browser lane; it is a separate
 * process and cannot import this, so the two are kept deliberately alike.
 *
 * Bodies are snake_case because that is the HTTP contract (the embed app's contract), and they
 * go through the real
 * `parseApprovalCard` rather than being hand-built as `ApprovalCard` values — a fixture that
 * bypassed the parser could describe a card the API can never send.
 */

export const CARD_ID = '9f1c2b7e-0000-4000-8000-000000000000'
export const CARD_CSRF = 'v1.1786000000.signature'
export const TOOL_RUN_ID = '5c2f1a90-0000-4000-8000-000000000001'

/**
 * The gate's own evidence for the account family, as
 * `apps/api/src/noa_api/mcp_tools/whm_account_change.py` writes it.
 *
 * **No `evidence_heading`, and that is the fixture's point as much as the keys that are here.**
 * The account tools read structured fields and have no vendor free-form text to head, so the gate
 * omits the key and the card must draw no block at all — not an empty heading over nothing. The
 * firewall shape beside this one is the opposite case.
 */
export const CARD_EVIDENCE: Record<string, unknown> = {
  headline: 'Suspend an account — acmeco',
  asked: 'suspend the acmeco account on alpha',
  server: 'alpha',
  suspended: false,
  domain: 'acme.example',
  // WHM echoes the operator's own typed NOA reason back on every later `listaccts` row, so a
  // preflight of a previously-suspended account carries one. Staged here on purpose: the render
  // path reads a closed allowlist of gate keys, and an allowlist only proves it holds while the
  // hazard is actually in front of it. Named again in the absence loop in
  // `src/app/approvals/[id]/card-view-render.test.tsx`.
  suspendreason: 'operator words WHM would echo back',
}

/**
 * A firewall gate's evidence: a heading, the firewall's own lines, and the bound they were read
 * under. Shaped from `whm_firewall_change.py` and `whm_firewall_change_common.py::firewall_state`,
 * whose `matches` is a `list[str]` — the plain lines csf printed, cut of NOA's own comment text.
 */
export const FIREWALL_EVIDENCE: Record<string, unknown> = {
  headline: 'Unblock an IP — 203.0.113.24',
  asked: 'remove 203.0.113.24 from the deny lists on alpha and allow it for 60 minutes',
  evidence_heading: 'Why it was blocked',
  server: 'alpha',
  target: '203.0.113.24',
  firewall: {
    combined_verdict: 'blocked',
    matches: ['DENY  203.0.113.24 # lfd: too many login failures'],
    total_matches: 1,
    truncated: false,
  },
}

/** A request still awaiting its answer: live token, no run, nothing decided. */
const PENDING_BODY: Record<string, unknown> = {
  action_request_id: CARD_ID,
  tool_name: 'whm_suspend_account',
  status: 'PENDING',
  conversation_ref: '1f0c2e5a-7b41-4d2e-9a3c-0b5d8e6f4a12',
  requester: { email: 'operator@noa.internal', librechat_user_id: 'librechat-user-1' },
  arguments: { server_ref: 'alpha', account: 'acmeco' },
  evidence: CARD_EVIDENCE,
  created_at: '2026-08-08T09:00:00+00:00',
  expires_at: '2026-08-08T10:00:00+00:00',
  decided_at: null,
  run: null,
  receipt: null,
  csrf: CARD_CSRF,
}

/**
 * The after-state half of a receipt.
 *
 * The runner's own two strings are here because they are what the card renders: `headline` names
 * what happened where the gate's names what was asked for, and `message` is the sentence under it.
 * Both differ from the gate's wording on purpose — a fixture whose halves read alike could not tell
 * a card that prefers the runner's from one that never looks at it.
 */
export const RECEIPT_AFTER: Record<string, unknown> = {
  ok: true,
  headline: 'Account suspended — acmeco',
  message: 'acmeco is suspended on alpha.',
  suspended: true,
  suspended_at: '2026-08-08T09:31:00+00:00',
}

/** What the API sends under `receipt` once a change has recorded an outcome. */
export function receiptBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    // The gate-time preflight, the same payload the pending card carries as `evidence` — that is
    // what the approved-change executor's writer copies onto the receipt. No surface reads it: the
    // card draws the evidence block off `evidence` in both states, so the block cannot change
    // between deciding and reading back.
    before: CARD_EVIDENCE,
    after: RECEIPT_AFTER,
    error_code: null,
    // What the runner measured. `verified` by default, which is the state whose corner carries no
    // qualification at all — so a spec asserting a qualified corner has to supply its own.
    delta: {
      identity: { server: 'alpha', username: 'acmeco' },
      verification: 'verified',
    },
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
  /** The card's own fields, for a spec whose subject is a family other than the account one. */
  cardOverrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return cardBody({
    status: 'APPROVED',
    decided_at: '2026-08-08T09:30:00+00:00',
    csrf: null,
    run: runBody(runOverrides),
    // `null` by default, because that is what a card carries while its run is still in flight:
    // the receipt lands with the terminal write, not with the decision.
    receipt,
    ...cardOverrides,
  })
}

export function approvalCard(overrides: Record<string, unknown> = {}): ApprovalCard {
  const card = parseApprovalCard(cardBody(overrides))
  if (card === null) throw new Error('fixture body is not a card')
  return card
}
