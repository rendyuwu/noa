'use client'

import { createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import { formatCreated, humanizeToolName, resolveActionRequestStatus } from '@/lib/admin/audit/audit-format'
import type { AuditActionRequestListItem } from '@/lib/admin/audit/types'

import { AuditStatusCell } from './audit-status-cell'

// DataTable columns for the CHANGE authorisation list (the admin API's contract). Same
// shape as the tool-run columns beside it, over the other half of the story:
// that list says what ran, this says who authorised it.
//
// `reason` is deliberately not a column. It is prose an operator typed — often
// several sentences, unbounded by design because no machine writes it — and a
// truncated cell would turn the field the whole approval design turns on into a
// fragment. It renders whole in the drawer.
//
// "Receipt" is a presence bit, not a link: the receipt is one more click inside
// the drawer, and a column of identical links would spend a column on saying
// "yes" ten times.
export function buildActionRequestColumns(): DataTableProps<AuditActionRequestListItem>['columns'] {
  return [
    createIdColumn<AuditActionRequestListItem>({
      accessorKey: 'actionRequestId',
      header: 'Request',
    }),
    {
      accessorKey: 'toolName',
      header: 'Tool',
      cell: ({ row }) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-medium text-text-primary">
            {humanizeToolName(row.original.toolName)}
          </span>
          <span className="truncate font-mono text-xs text-text-secondary">
            {row.original.toolName}
          </span>
        </div>
      ),
    },
    {
      id: 'requestedByEmail',
      header: 'Requested by',
      cell: ({ row }) => (
        <span className="truncate text-sm text-text-secondary">
          {row.original.requestedByEmail?.trim() || '—'}
        </span>
      ),
    },
    {
      accessorKey: 'createdAt',
      header: 'Opened',
      cell: ({ row }) => {
        const created = formatCreated(row.original.createdAt)
        return (
          <span className="text-sm text-text-secondary" title={created.title}>
            {created.primary}
            {created.secondary ? ` · ${created.secondary}` : ''}
          </span>
        )
      },
    },
    {
      id: 'decidedAt',
      header: 'Decided',
      cell: ({ row }) => {
        const decided = row.original.decidedAt ? formatCreated(row.original.decidedAt) : null
        return (
          <span className="text-sm text-text-secondary" title={decided?.title ?? ''}>
            {decided ? decided.primary : '—'}
          </span>
        )
      },
    },
    {
      id: 'hasReceipt',
      header: 'Receipt',
      cell: ({ row }) => (
        <span className="text-sm text-text-secondary">
          {row.original.hasReceipt ? 'Recorded' : '—'}
        </span>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => (
        <AuditStatusCell view={resolveActionRequestStatus(row.original.status)} />
      ),
    },
  ]
}
