import type { ActionFilters, ToolRunFilters } from './types'
import { AUDIT_PAGE_SIZE } from './types'

// Query construction + filter accounting for the audit surfaces (issue #111).
// The API is server-paged (cursor) and server-filtered, so the page keeps its
// filter state here and hands the built query string to the transport. Date
// inputs are day-granular in the UI but the API expects ISO instants, so a
// `from` day becomes the start of that UTC day and a `to` day the end of it.

export function normalizeDateRange(filters: {
  fromDate: string
  toDate: string
}): { from?: string; to?: string } {
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

export function activeActionFilterCount(filters: ActionFilters): number {
  return [
    filters.fromDate,
    filters.toDate,
    filters.toolName,
    filters.status,
    filters.terminalPhase,
    filters.threadId,
    filters.requestedByEmail,
  ].filter((value) => value.trim()).length
}

export function activeToolFilterCount(filters: ToolRunFilters): number {
  return [
    filters.fromDate,
    filters.toDate,
    filters.toolName,
    filters.status,
    filters.risk,
    filters.threadId,
    filters.requestedByEmail,
  ].filter((value) => value.trim()).length
}

export function buildActionQuery(filters: ActionFilters, cursor: string | null): string {
  const params = new URLSearchParams()
  params.set('limit', String(AUDIT_PAGE_SIZE))
  if (cursor) params.set('cursor', cursor)
  if (filters.toolName.trim()) params.set('toolName', filters.toolName.trim())
  if (filters.status.trim()) params.set('status', filters.status.trim())
  if (filters.terminalPhase.trim()) params.set('terminalPhase', filters.terminalPhase.trim())
  if (filters.threadId.trim()) params.set('threadId', filters.threadId.trim())
  if (filters.requestedByEmail.trim()) params.set('requestedByEmail', filters.requestedByEmail.trim())
  const { from, to } = normalizeDateRange(filters)
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  return params.toString()
}

export function buildToolRunQuery(filters: ToolRunFilters, cursor: string | null): string {
  const params = new URLSearchParams()
  params.set('limit', String(AUDIT_PAGE_SIZE))
  if (cursor) params.set('cursor', cursor)
  if (filters.toolName.trim()) params.set('toolName', filters.toolName.trim())
  if (filters.status.trim()) params.set('status', filters.status.trim())
  if (filters.risk.trim()) params.set('risk', filters.risk.trim())
  if (filters.threadId.trim()) params.set('threadId', filters.threadId.trim())
  if (filters.requestedByEmail.trim()) params.set('requestedByEmail', filters.requestedByEmail.trim())
  const { from, to } = normalizeDateRange(filters)
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  return params.toString()
}
