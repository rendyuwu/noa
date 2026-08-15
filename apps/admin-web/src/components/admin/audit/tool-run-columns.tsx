'use client'

import { createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import {
  formatCreated,
  formatDuration,
  humanizeToolName,
  resolveRiskBadge,
  resolveToolRunStatus,
} from '@/lib/admin/audit/audit-format'
import type { AuditToolRunListItem } from '@/lib/admin/audit/types'

import { AuditStatusCell } from './audit-status-cell'

// DataTable columns for the tool-run audit list (issue #111). The tool run id
// renders through createIdColumn (full, monospace, copyable). Status renders
// through StatusChip; tool risk through a Badge. createdAt keeps its raw
// accessorKey so the sort orders by the real timestamp.
export function buildToolRunColumns(): DataTableProps<AuditToolRunListItem>['columns'] {
  return [
    createIdColumn<AuditToolRunListItem>({ accessorKey: 'toolRunId', header: 'Tool run' }),
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
      id: 'duration',
      header: 'Duration',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-text-secondary">
          {formatDuration(row.original.durationMs)}
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
      cell: ({ row }) => <AuditStatusCell view={resolveToolRunStatus(row.original.status)} />,
    },
  ]
}
