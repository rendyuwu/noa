// Audit wire types. These mirror the FastAPI `/admin/audit/tool-runs` contract
// (camelCase) exactly — the list item, the detail (redacted args) and the cursor
// envelope. The API redacts sensitive tool arguments at the moment it *writes*
// the row, so `args` arrives already sanitised; nothing here ever un-redacts.
//
// Ported from the old repo with an action-requests tab, a receipts page and
// three fields NOA's `tool_runs` table does not have — `threadId` (NOA dropped
// threads), `error` (a sanitised error code lands in `resultSummary` instead)
// and `actionRequestId` (an approved change's run links the other way). All of
// them are gone: T55 ships the two routes §I.admin-api names, and a control
// bound to a column that does not exist renders an em dash forever.
//
// `conversationRef` replaces `threadId`. It is a *label*, never a scope: the MCP
// call carries no conversation id, so it arrives as an optional header and is
// null unless `librechat.yaml` supplies one. Free text, not a uuid.

export type AuditToolRunListItem = {
  toolRunId: string
  toolName: string
  risk: string
  status: string
  conversationRef?: string | null
  requestedByEmail?: string | null
  resultSummary?: string | null
  createdAt: string
  completedAt?: string | null
  durationMs?: number | null
}

export type AuditToolRunDetail = AuditToolRunListItem & {
  requestedByUserId?: string | null
  args: Record<string, unknown>
}

export type ListAuditToolRunsResponse = {
  items: AuditToolRunListItem[]
  nextCursor?: string | null
}

export type ToolRunFilters = {
  fromDate: string
  toDate: string
  toolName: string
  status: string
  risk: string
  conversationRef: string
  requestedByEmail: string
}

export const DEFAULT_TOOL_FILTERS: ToolRunFilters = {
  fromDate: '',
  toDate: '',
  toolName: '',
  status: '',
  risk: '',
  conversationRef: '',
  requestedByEmail: '',
}

export const AUDIT_PAGE_SIZE = 50

// --- The CHANGE authorisation trail (§I.admin-api) ---
//
// The other half of the audit surface: `tool_runs` above says what ran, these
// say who authorised it and why. `reason` is the field that made this vertical
// worth building — it is the operator's own words, written at decision time,
// and until the `/admin/action-requests` routes shipped nothing anywhere could
// read it back.
//
// `reason` and `approvalContext` are on the detail and deliberately not on the
// list item: a fifty-row page would carry fifty JSONB payloads to draw six
// columns. `approvalContext` is `unknown`-valued rather than a named shape
// because the keys under `evidence` are whichever tool's own preflight
// vocabulary — a typed model here would have to be widened by every tool ever
// added, which is the same call `args` makes above.

export type AuditActionRequestListItem = {
  actionRequestId: string
  toolName: string
  status: string
  requestedByEmail?: string | null
  conversationRef?: string | null
  createdAt: string
  expiresAt: string
  decidedAt?: string | null
  toolRunId?: string | null
  hasReceipt: boolean
}

export type AuditActionRequestDetail = AuditActionRequestListItem & {
  reason?: string | null
  approvalContext: Record<string, unknown>
}

// Both halves as the executor wrote them, plus the runner's own delta. `delta`
// is `null` when the runner stated none, and that absence is the only thing
// separating an executor refusal from a runner failure — both are `ok: false` —
// so it is a nullable field here and never defaulted to an empty object.
export type AuditActionReceipt = {
  actionRequestId: string
  toolRunId?: string | null
  createdAt: string
  ok: boolean
  before: Record<string, unknown>
  after: Record<string, unknown>
  errorCode?: string | null
  delta?: Record<string, unknown> | null
}

export type ListAuditActionRequestsResponse = {
  items: AuditActionRequestListItem[]
  nextCursor?: string | null
}

export type ActionRequestFilters = {
  fromDate: string
  toDate: string
  toolName: string
  status: string
  requestedByEmail: string
}

export const DEFAULT_ACTION_REQUEST_FILTERS: ActionRequestFilters = {
  fromDate: '',
  toDate: '',
  toolName: '',
  status: '',
  requestedByEmail: '',
}
