// Audit admin wire types (issue #111). These mirror the FastAPI
// `/admin/audit/*` contract (camelCase aliases) exactly — action-request and
// tool-run list items, the tool-run detail (redacted args), and the cursor list
// envelopes. The API redacts sensitive tool arguments server-side, so `args`
// arrives already sanitised; the client never un-redacts.

export type AuditTab = 'action-requests' | 'tool-runs'

export type AuditActionRequestListItem = {
  actionRequestId: string
  threadId: string
  toolRunId?: string | null
  receiptId?: string | null
  toolName: string
  risk: string
  status: string
  requestedByEmail?: string | null
  decidedAt?: string | null
  createdAt: string
  updatedAt: string
  terminalPhase?: string | null
  hasReceipt: boolean
}

export type AuditToolRunListItem = {
  toolRunId: string
  threadId: string
  actionRequestId?: string | null
  toolName: string
  risk?: string | null
  status: string
  requestedByEmail?: string | null
  createdAt: string
  completedAt?: string | null
  durationMs?: number | null
  resultSummary?: string | null
  error?: string | null
}

export type AuditToolRunDetail = AuditToolRunListItem & {
  args: Record<string, unknown>
}

export type AuditToolRunSummary = {
  id: string
  status: string
  createdAt: string
  completedAt?: string | null
}

export type AuditActionReceiptSummary = {
  terminalPhase: string
  createdAt: string
}

export type AuditActionRequestDetail = {
  actionRequestId: string
  threadId: string
  toolName: string
  risk: string
  status: string
  args: Record<string, unknown>
  requestedByEmail?: string | null
  decidedByEmail?: string | null
  decidedAt?: string | null
  createdAt: string
  updatedAt: string
  toolRuns: AuditToolRunSummary[]
  receipt?: AuditActionReceiptSummary | null
}

export type ListAuditActionRequestsResponse = {
  items: AuditActionRequestListItem[]
  nextCursor?: string | null
}

export type ListAuditToolRunsResponse = {
  items: AuditToolRunListItem[]
  nextCursor?: string | null
}

export type ActionFilters = {
  fromDate: string
  toDate: string
  toolName: string
  status: string
  terminalPhase: string
  threadId: string
  requestedByEmail: string
}

export type ToolRunFilters = {
  fromDate: string
  toDate: string
  toolName: string
  status: string
  risk: string
  threadId: string
  requestedByEmail: string
}

export const DEFAULT_ACTION_FILTERS: ActionFilters = {
  fromDate: '',
  toDate: '',
  toolName: '',
  status: '',
  terminalPhase: '',
  threadId: '',
  requestedByEmail: '',
}

export const DEFAULT_TOOL_FILTERS: ToolRunFilters = {
  fromDate: '',
  toDate: '',
  toolName: '',
  status: '',
  risk: '',
  threadId: '',
  requestedByEmail: '',
}

export const AUDIT_PAGE_SIZE = 50
