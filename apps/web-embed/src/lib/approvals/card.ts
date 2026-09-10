/**
 * The approval card, as this app models it (§T.41, §I.embed).
 *
 * One shape, parsed once, at the edge of the app. The API's body is snake_case because it is an
 * HTTP contract shared with the spec (§I.embed); everything inside this package reads the
 * camelCase view below, so a field rename upstream breaks in `parseApprovalCard` rather than in
 * whichever component happened to read it.
 *
 * **Parsing is permissive and rendering is fail-closed**, and those are not in tension. A body
 * missing a field renders that field as unknown, because a card that throws is a blank iframe and
 * V38 is explicit that a blank card is not an acceptable state. What is never permissive is
 * whether the operator may *act*: `canDecide` demands a PENDING status **and** a token, so
 * anything unrecognised — an unknown status string, an absent `csrf` — renders read-only.
 *
 * There is no `reason` field here and the API sends none. The reason is typed into
 * this card and travels outward only; nothing renders one back.
 *
 * The **receipt** is the one field that arrives late: it is `null` until something has recorded
 * what the change did, and then it carries two halves that are never merged (§T.42(b), V46,
 * DECISIONS §6.5). Modelled as two fields rather than one summary string for that reason — the
 * shape is where "do not collapse this into 'done'" is enforced, not the component.
 */

/** The four `ActionRequestStatus` values the API can send. */
export const APPROVAL_STATUSES = ['PENDING', 'APPROVED', 'DENIED', 'EXPIRED'] as const

export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number]

/** The three `ToolRunStatus` values. Separate set on purpose — the two never mix. */
export const RUN_STATUSES = ['STARTED', 'COMPLETED', 'FAILED'] as const

export type RunStatus = (typeof RUN_STATUSES)[number]

/** The execution an approval started, if one has. */
export type ApprovalRun = {
  toolRunId: string
  status: string
  resultSummary: string | null
  createdAt: string
  completedAt: string | null
}

/** Who asked for the change, and from where. */
export type ApprovalRequester = {
  email: string
  librechatUserId: string
}

/**
 * What the change did, in the two halves it was written as (§T.38, §T.42 — V46).
 *
 * DECISIONS §6.5 is the requirement: an operator reads back the state they authorised against
 * **and** what the change did to it, each on its own, never collapsed into a single "done". So
 * `before` and `after` are two fields here and two blocks on the card — modelling them as one
 * string would make the collapse a rendering decision, and it is not one that is available.
 *
 * `ok` comes off the receipt rather than being inferred from `after` having keys: the API lifted
 * it from the runner's own envelope, and a second opinion here is a second answer to whether the
 * change worked.
 */
export type ApprovalReceipt = {
  ok: boolean
  /** The gate-time preflight the operator approved against. */
  before: Record<string, unknown>
  /** What the runner answered, redacted by the writer and carried, never re-derived. */
  after: Record<string, unknown>
  /** The named cause when the change did not complete. `null` when there is none. */
  errorCode: string | null
}

export type ApprovalCard = {
  actionRequestId: string
  toolName: string
  /**
   * Kept as a `string`, not narrowed to `ApprovalStatus`. A status this build does not know
   * about must render as itself and be undecidable, which is what `canDecide` enforces; casting
   * it into the union would make an unknown value look like a known one.
   */
  status: string
  conversationRef: string | null
  requester: ApprovalRequester
  /** Redacted at the gate and carried, never re-derived here. */
  arguments: Record<string, unknown>
  /** The in-process preflight — the before-state this card exists to show. */
  evidence: Record<string, unknown>
  createdAt: string
  expiresAt: string
  decidedAt: string | null
  run: ApprovalRun | null
  /** What the run recorded, once something has. `null` until then. */
  receipt: ApprovalReceipt | null
  /** Server-minted, session- and request-bound. `null` once nothing may be decided. */
  csrf: string | null
}

/**
 * One read of one card, however it turned out (§T.41, §T.42).
 *
 * Lives here rather than beside either reader because there are now two of them: the server-side
 * load `lib/approvals/detail.ts` does before the page renders, and the browser-side poll
 * `lib/approvals/poll.ts` repeats until the run is terminal. Both answer the same four
 * questions and the card component switches on the result once — a second union would be a second
 * set of states for the same read, free to grow a fifth on one side only.
 *
 * **Four kinds because four of them render differently.** A 401 is V38's "cannot authenticate
 * here", a 404 is V27's single answer for absent / another operator's / a deleted requester's, and
 * anything else is "could not load" — which is neither, and must never be shown as a card with
 * empty fields.
 */
export type ApprovalCardLoad =
  | { kind: 'card'; card: ApprovalCard }
  | { kind: 'unauthenticated' }
  | { kind: 'not-found' }
  | { kind: 'unavailable'; status: number }

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNullableString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

function parseRequester(value: unknown): ApprovalRequester {
  const requester = asRecord(value)
  return {
    email: asString(requester['email']),
    librechatUserId: asString(requester['librechat_user_id']),
  }
}

function parseRun(value: unknown): ApprovalRun | null {
  if (!isRecord(value)) return null

  const toolRunId = asString(value['tool_run_id'])
  // No id, no run: a run the card cannot name is one nothing can poll (§T.42).
  if (!toolRunId) return null

  return {
    toolRunId,
    status: asString(value['status']),
    resultSummary: asNullableString(value['result_summary']),
    createdAt: asString(value['created_at']),
    completedAt: asNullableString(value['completed_at']),
  }
}

/**
 * The receipt block, or `null` when the API sent none (§T.42 — V46, V38).
 *
 * `null` means "nothing has recorded what this change did", which is the truth until T38's
 * executor or its reaper writes one — and the card renders no outcome section rather than an
 * empty one. Anything that is not an object lands here too: a body the API cannot send is not a
 * reason to blank the frame, and the halves render as "nothing recorded" instead.
 *
 * `ok` is `true` only for a literal `true`, matching the API's own comparison. Fail-closed is
 * the only safe direction for "did this change work" — a truthy string read as success would
 * tell an operator a change landed on the evidence that something non-boolean was in the field.
 */
function parseReceipt(value: unknown): ApprovalReceipt | null {
  if (!isRecord(value)) return null

  const errorCode = asString(value['error_code'])
  return {
    ok: value['ok'] === true,
    before: asRecord(value['before']),
    after: asRecord(value['after']),
    // Empty string reads as no code: the API sends `null` when there is none, and a blank one
    // would render as a labelled row saying nothing.
    errorCode: errorCode === '' ? null : errorCode,
  }
}

/**
 * The API body as an `ApprovalCard`, or `null` if it is not one.
 *
 * `null` for a body with no `action_request_id`: that is the one field every other part of the
 * page hangs off — the decision POST's path, the poll (§T.42), the CSRF binding — so a card
 * without it is not a card, and pretending otherwise would render live buttons aimed at nothing.
 */
export function parseApprovalCard(value: unknown): ApprovalCard | null {
  if (!isRecord(value)) return null

  const actionRequestId = asString(value['action_request_id'])
  if (!actionRequestId) return null

  return {
    actionRequestId,
    toolName: asString(value['tool_name']),
    status: asString(value['status']),
    conversationRef: asNullableString(value['conversation_ref']),
    requester: parseRequester(value['requester']),
    arguments: asRecord(value['arguments']),
    evidence: asRecord(value['evidence']),
    createdAt: asString(value['created_at']),
    expiresAt: asString(value['expires_at']),
    decidedAt: asNullableString(value['decided_at']),
    run: parseRun(value['run']),
    receipt: parseReceipt(value['receipt']),
    csrf: asNullableString(value['csrf']),
  }
}

/**
 * Whether this card may be approved or denied from here.
 *
 * Both halves are required, and neither is the security boundary: the decision endpoint checks
 * the status under a row lock and verifies the token itself. What this decides
 * is whether an operator is *shown* a live button — and a button that cannot succeed is worse
 * than no button, because it reads as an action that was refused rather than one that was never
 * available.
 */
export function canDecide(card: ApprovalCard): boolean {
  return card.status === 'PENDING' && typeof card.csrf === 'string' && card.csrf.length > 0
}

const STATUS_LABELS: Record<ApprovalStatus, string> = {
  PENDING: 'Awaiting your decision',
  APPROVED: 'Approved',
  DENIED: 'Denied',
  EXPIRED: 'Expired without an answer',
}

/**
 * A human label for a status, falling back to the raw value.
 *
 * The fallback is the point: a status this build has never heard of is shown verbatim rather
 * than as "Unknown", so an operator reading the card and an administrator reading the row are
 * looking at the same word.
 */
export function statusLabel(status: string): string {
  return STATUS_LABELS[status as ApprovalStatus] ?? status
}
