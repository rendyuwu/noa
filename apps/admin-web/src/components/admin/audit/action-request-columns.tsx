'use client'

import { createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import {
  formatCreated,
  humanizeToolName,
  resolveActionStatus,
  resolveRiskBadge,
} from '@/lib/admin/audit/audit-format'
import type { AuditActionRequestListItem } from '@/lib/admin/audit/types'

import { AuditStatusCell } from './audit-status-cell'

// DataTable columns for the action-request audit list (issue #111). The action
// request id renders through createIdColumn so the full identifier stays
// monospace and copyable — audit identifiers are never truncated. The status
// column renders through StatusChip (via AuditStatusCell), tool risk through a
// Badge, and createdAt keeps its raw accessorKey so the header sort orders by
// the real ISO timestamp while the cell shows the friendly form.
export function buildActionRequestColumns(): DataTableProps<AuditActionRequestListItem>['columns'] {
  return [
    createIdColumn<AuditActionRequestListItem>({
      accessorKey: 'actionRequestId',
      header: 'Action request',
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
      id: 'risk',
      header: 'Risk',
      cell: ({ row }) => {
        const risk = resolveRiskBadge(row.original.risk)
        return <AuditStatusCell view={{ kind: 'badge', label: risk.label, variant: risk.variant }} />
      },
    },
    {
      accessorKey: 'requestedByEmail',
      header: 'Requested by',
      cell: ({ row }) => (
        <span className="text-sm text-text-secondary">
          {row.original.requestedByEmail?.trim() || 'Unknown'}
        </span>
      ),
    },
    {
      accessorKey: 'createdAt',
      header: 'Created',
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
      id: 'status',
      header: 'Status',
      cell: ({ row }) => <AuditStatusCell view={resolveActionStatus(row.original)} />,
    },
  ]
}
