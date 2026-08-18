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
