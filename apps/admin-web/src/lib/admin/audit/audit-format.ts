import type { BadgeVariant, StatusChipStatus } from '@gio/bigsu-ui'

import type { AuditActionRequestListItem } from './types'

// Presentation mapping for the audit surfaces (issue #111). Two rules from the
// acceptance criteria drive this module:
//
//  1. Workflow statuses render through StatusChip's fixed 11-value vocabulary.
//     Non-workflow descriptors (tool risk) render through Badge.
//  2. Reviewed BIGSU StatusChip mappings — not the raw domain enum, and not the
//     API's own label. The confirmation projection (GH #104) maps an APPROVED /
//     EXECUTING confirmation to "In Progress", but "In Progress" is NOT one of
//     the 11 standard StatusChip statuses. Copying it would be a compile error
//     and a vocabulary drift, so we correct the conflict here: an APPROVED
//     action-request with no terminal receipt is shown as the nearest standard
//     status, "Approved" (a decision was made). The real terminal outcome, when
//     one exists, always comes from the receipt's terminalPhase and wins.

export type AuditStatusView =
  | { kind: 'status'; status: StatusChipStatus }
  | { kind: 'badge'; label: string; variant: BadgeVariant }

const asStatus = (status: StatusChipStatus): AuditStatusView => ({ kind: 'status', status })

// Resolve an action-request row to its StatusChip. The receipt's terminal phase
// is the authoritative outcome and takes precedence over the request status.
export function resolveActionStatus(item: AuditActionRequestListItem): AuditStatusView {
  const phase = (item.terminalPhase ?? '').trim().toLowerCase()
  if (phase === 'completed') return asStatus('Completed')
  if (phase === 'failed') return asStatus('Failed')
  if (phase === 'denied') return asStatus('Rejected')

  const status = (item.status ?? '').trim().toUpperCase()
  if (status === 'PENDING') return asStatus('Pending')
  if (status === 'APPROVED') return asStatus('Approved')
  if (status === 'DENIED') return asStatus('Rejected')
  return { kind: 'badge', label: status || 'Unknown', variant: 'neutral' }
}

// Resolve a tool-run row to its StatusChip. A STARTED run is executing (live),
// which maps to the "Active" lifecycle status — there is no "Running" standard
// status, and "Active" is the nearest honest fit for an in-flight execution.
export function resolveToolRunStatus(status: string): AuditStatusView {
  const normalized = (status ?? '').trim().toUpperCase()
  if (normalized === 'COMPLETED') return asStatus('Completed')
  if (normalized === 'FAILED') return asStatus('Failed')
  if (normalized === 'STARTED') return asStatus('Active')
  return { kind: 'badge', label: normalized || 'Unknown', variant: 'neutral' }
}

// Tool risk is a category label, not a workflow status, so it renders through
// Badge. CHANGE is a mutating tool (warning tone); READ is read-only (neutral).
export function resolveRiskBadge(value?: string | null): { label: string; variant: BadgeVariant } {
  const normalized = (value ?? '').trim().toUpperCase()
  if (normalized === 'CHANGE') return { label: 'Change', variant: 'warning' }
  if (normalized === 'READ') return { label: 'Read', variant: 'neutral' }
  return { label: normalized || 'Unknown', variant: 'neutral' }
}

const KNOWN_TOOL_PREFIXES = ['proxmox_', 'whm_', 'pmg_']
const TOOL_WORD_OVERRIDES: Record<string, string> = {
  csf: 'CSF',
  whm: 'WHM',
  rbac: 'RBAC',
  ldap: 'LDAP',
  vm: 'VM',
  pmg: 'PMG',
}

// Turn a raw tool name (e.g. "whm_create_account") into a readable label. The
// full raw name still renders in monospace elsewhere; this is the human column.
export function humanizeToolName(value: string): string {
  const raw = value.trim()
  const withoutPrefix = KNOWN_TOOL_PREFIXES.reduce(
    (current, prefix) => (current.startsWith(prefix) ? current.slice(prefix.length) : current),
    raw,
  )
  const words = withoutPrefix
    .split(/[_-]+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase()
      return TOOL_WORD_OVERRIDES[lower] ?? lower.charAt(0).toUpperCase() + lower.slice(1)
    })
  return words.length === 0 ? raw : words.join(' ')
}

function parseIsoDate(value: unknown): Date | null {
  if (typeof value !== 'string' || !value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function formatRelativeTime(date: Date): string {
  const diffMs = Date.now() - date.getTime()
  if (!Number.isFinite(diffMs) || diffMs < 0) return ''
  const diffSeconds = Math.floor(diffMs / 1000)
  if (diffSeconds < 60) return 'just now'
  const diffMinutes = Math.floor(diffSeconds / 60)
  if (diffMinutes < 60) return `${diffMinutes}m ago`
  const diffHours = Math.floor(diffMinutes / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 30) return `${diffDays}d ago`
  return ''
}

export type FormattedTimestamp = { primary: string; secondary: string; title: string }

export function formatCreated(value: unknown): FormattedTimestamp {
  const date = parseIsoDate(value)
  if (!date) return { primary: '—', secondary: '', title: '' }
  const primary = new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: '2-digit',
    year: 'numeric',
  }).format(date)
  const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date)
  const relative = formatRelativeTime(date)
  return { primary, secondary: relative ? `${time} · ${relative}` : time, title: date.toISOString() }
}

export function formatDuration(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  if (value < 1000) return `${value}ms`
  return `${(value / 1000).toFixed(1)}s`
}

export function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
