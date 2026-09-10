'use client'

import type { ReactNode } from 'react'

import { formatJson } from '@/lib/admin/audit/audit-format'

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

// A JSON value as text, never as markup. Both audit drawers render arbitrary
// stored blobs — tool arguments on one, the gate context and both receipt
// halves on the other — and a `yopass_url` can appear anywhere inside them. That
// URL is consumed once, so a hover preview, a prefetch or a mis-click spends the
// operator's own delivery and nobody can read the password afterwards: it renders
// as characters in a monospace block and never as an anchor (C15, V49).
//
// One component rather than one `<pre>` per drawer, and that is the point of it
// (V66). The rule above is enforced where the markup is written, so a second
// renderer cannot ship guarded by nothing, and the next JSON surface has one
// obvious thing to reuse instead of a `<pre>` to copy.
export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-md border border-border-default bg-surface p-3 font-mono text-xs text-text-primary">
      {formatJson(value)}
    </pre>
  )
}
