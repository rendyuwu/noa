'use client'

import type { ReactNode } from 'react'

// Shared detail-row primitives for the audit drawers (issue #111). A label/value
// row for facts, and a monospace identifier row for technical identifiers —
// request IDs, run IDs, thread IDs, and targets render full and monospace, never
// truncated, so operators can copy and grep them.

export function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start justify-between gap-4">
      <dt className="shrink-0 text-text-secondary">{label}</dt>
      <dd className="min-w-0 break-words text-right text-text-primary">{value}</dd>
    </div>
  )
}

export function AuditIdRow({ label, value }: { label: string; value?: string | null }) {
  const normalized = value?.trim() ?? ''
  return (
    <div className="flex min-w-0 items-start justify-between gap-4">
      <dt className="shrink-0 text-text-secondary">{label}</dt>
      <dd className="min-w-0 break-all text-right font-mono text-xs text-text-primary">
        {normalized || '—'}
      </dd>
    </div>
  )
}
