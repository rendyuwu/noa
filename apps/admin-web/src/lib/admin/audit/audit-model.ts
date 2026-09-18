import type { ActionRequestFilters, ToolRunFilters } from './types'
import { AUDIT_PAGE_SIZE } from './types'

// Query construction + filter accounting for the audit list. The API is
// server-paged (keyset cursor) and server-filtered, so the page keeps its filter
// state here and hands the built query string to the transport.
//
// TOOL_RUN_QUERY_KEYS is the whole set of parameters the API accepts — the same
// seven `apps/api/tests/test_admin_audit_routes.py::FILTER_QUERIES` walks, plus
// `limit` and `cursor`. Kept as a named list so the offered set and the accepted
// set are each written once and can be checked against each other, rather than
// drifting apart across a dozen call sites.
//
// Date inputs are day-granular in the UI but the API takes ISO instants, so a
// `from` day becomes the start of that UTC day and a `to` day the end of it.

export const TOOL_RUN_QUERY_KEYS = [
  'toolName',
  'status',
  'risk',
  'conversationRef',
  'requestedByEmail',
  'from',
  'to',
] as const

// Not exported: both query builders below are its only callers, and the day-to-instant
// expansion is only ever observable as the `from`/`to` parameters they emit.
function normalizeDateRange(filters: { fromDate: string; toDate: string }): {
  from?: string
  to?: string
} {
  const fromDate = filters.fromDate.trim()
  const toDate = filters.toDate.trim()
  const result: { from?: string; to?: string } = {}
  if (fromDate) {
    const from = new Date(`${fromDate}T00:00:00.000Z`)
    if (!Number.isNaN(from.getTime())) result.from = from.toISOString()
  }
  if (toDate) {
    const to = new Date(`${toDate}T23:59:59.999Z`)
    if (!Number.isNaN(to.getTime())) result.to = to.toISOString()
  }
  return result
}

export function activeToolFilterCount(filters: ToolRunFilters): number {
  return [
    filters.fromDate,
    filters.toDate,
    filters.toolName,
    filters.status,
    filters.risk,
    filters.conversationRef,
    filters.requestedByEmail,
  ].filter((value) => value.trim()).length
}

export function buildToolRunQuery(filters: ToolRunFilters, cursor: string | null): string {
  const params = new URLSearchParams()
  params.set('limit', String(AUDIT_PAGE_SIZE))
  if (cursor) params.set('cursor', cursor)
  if (filters.toolName.trim()) params.set('toolName', filters.toolName.trim())
  if (filters.status.trim()) params.set('status', filters.status.trim())
  if (filters.risk.trim()) params.set('risk', filters.risk.trim())
  if (filters.conversationRef.trim()) params.set('conversationRef', filters.conversationRef.trim())
  if (filters.requestedByEmail.trim())
    params.set('requestedByEmail', filters.requestedByEmail.trim())
  const { from, to } = normalizeDateRange(filters)
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  return params.toString()
}

// --- The CHANGE authorisation trail ---
//
// Five parameters, not seven: this list has no `risk` (every row is a CHANGE, so
// the filter would scope nothing) and no `conversationRef` (the label is on the
// row and worth showing, but grouping by it belongs to the tool-run list where
// READs live too). The set is named for the same reason the one above is — the
// offered names and the accepted names are each written once, and
// `apps/api/tests/test_admin_action_request_routes.py::FILTER_QUERIES` walks the
// accepted half.

export const ACTION_REQUEST_QUERY_KEYS = [
  'toolName',
  'status',
  'requestedByEmail',
  'from',
  'to',
] as const

export function activeActionRequestFilterCount(filters: ActionRequestFilters): number {
  return [
    filters.fromDate,
    filters.toDate,
    filters.toolName,
    filters.status,
    filters.requestedByEmail,
  ].filter((value) => value.trim()).length
}

export function buildActionRequestQuery(
  filters: ActionRequestFilters,
  cursor: string | null,
): string {
  const params = new URLSearchParams()
  params.set('limit', String(AUDIT_PAGE_SIZE))
  if (cursor) params.set('cursor', cursor)
  if (filters.toolName.trim()) params.set('toolName', filters.toolName.trim())
  if (filters.status.trim()) params.set('status', filters.status.trim())
  if (filters.requestedByEmail.trim())
    params.set('requestedByEmail', filters.requestedByEmail.trim())
  const { from, to } = normalizeDateRange(filters)
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  return params.toString()
}
