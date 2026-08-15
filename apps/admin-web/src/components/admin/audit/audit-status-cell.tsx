'use client'

import { Badge, StatusChip } from '@gio/bigsu-ui'

import type { AuditStatusView } from '@/lib/admin/audit/audit-format'

// Render an audit status view (issue #111): a workflow status becomes a
// StatusChip (fixed 11-value vocabulary), while a non-workflow descriptor
// becomes a Badge. This keeps the "StatusChip for workflow status, Badge for
// categories" rule in exactly one place across every audit table and drawer.
export function AuditStatusCell({ view }: { view: AuditStatusView }) {
  if (view.kind === 'status') return <StatusChip status={view.status} />
  return <Badge variant={view.variant}>{view.label}</Badge>
}
