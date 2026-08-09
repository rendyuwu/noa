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
 * There is no `reason` field here and the API sends none (C8, V15, V43). The reason is typed into
 * this card and travels outward only; nothing renders one back.
 */

/** The four `ActionRequestStatus` values the API can send (V20). */
export const APPROVAL_STATUSES = ['PENDING', 'APPROVED', 'DENIED', 'EXPIRED'] as const

export type ApprovalStatus = (typeof APPROVAL_STATUSES)[number]

/** The three `ToolRunStatus` values (V20). Separate set on purpose — the two never mix. */
export const RUN_STATUSES = ['STARTED', 'COMPLETED', 'FAILED'] as const

export type RunStatus = (typeof RUN_STATUSES)[number]

/** The execution an approval started, if one has (V29, V47). */
export type ApprovalRun = {
  toolRunId: string
  status: string
  resultSummary: string | null
  createdAt: string
  completedAt: string | null
}

/** Who asked for the change, and from where (V35). */
export type ApprovalRequester = {
  email: string
  librechatUserId: string
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
  /** Redacted at the gate (V8) and carried, never re-derived here. */
  arguments: Record<string, unknown>
  /** The in-process preflight (C9, V17) — the before-state this card exists to show. */
  evidence: Record<string, unknown>
  createdAt: string
  expiresAt: string
  decidedAt: string | null
  run: ApprovalRun | null
  /** Server-minted, session- and request-bound (V39). `null` once nothing may be decided. */
  csrf: string | null
}

/**
 * One read of one card, however it turned out (§T.41, §T.42).
 *
 * Lives here rather than beside either reader because there are now two of them: the server-side
 * load `lib/approvals/detail.ts` does before the page renders, and the browser-side poll
 * `lib/approvals/poll.ts` repeats until the run is terminal (V29). Both answer the same four
 * questions and the card component switches on the result once — a second union would be a second
 * set of states for the same read, free to grow a fifth on one side only (V66).
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
    csrf: asNullableString(value['csrf']),
  }
}

/**
 * Whether this card may be approved or denied from here (V38, V39).
 *
 * Both halves are required, and neither is the security boundary: the decision endpoint checks
 * the status under a row lock (V28, V32) and verifies the token itself (V39). What this decides
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
