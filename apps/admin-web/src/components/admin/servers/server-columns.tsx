'use client'

import { Badge, StatusChip, createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import { formatRelativeTime } from '@/lib/admin/shared/relative-time'
import { deriveServerValidationStatus } from '@/lib/admin/shared/server-status'
import type { ValidateResultLike } from '@/lib/admin/shared/servers-controller-types'

import type { AdminServer, ServerColumnSpec } from './types'

// DataTable columns for every admin servers list. The identifier renders through
// createIdColumn so the full server id stays monospace and copyable; validation
// lifecycle renders through StatusChip (standard vocabulary), while a vertical's
// configuration labels render through Badge — a StatusChip carries workflow
// status, a Badge carries a configuration fact, and they are not interchangeable.
// updated_at keeps its raw accessorKey so the header sort orders by the real
// timestamp while the cell shows the friendly relative form. No secret is ever
// rendered; only presence and derived state reach a cell.
export function buildServerColumns<
  TServer extends AdminServer,
  TValidate extends ValidateResultLike,
>(
  spec: ServerColumnSpec<TServer>,
  validateResultById: Record<string, TValidate>,
): DataTableProps<TServer>['columns'] {
  const { rowBadge } = spec
  return [
    createIdColumn<TServer>({ header: 'Server ID' }),
    {
      accessorKey: 'name',
      header: 'Server',
      cell: ({ row }) => {
        // Verticals with a row badge stack name+badge over the subtitle; the rest
        // keep the tighter two-line cell that has no badge slot to reserve.
        const badge = rowBadge?.(row.original) ?? null
        return rowBadge ? (
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm font-medium text-text-primary">
                {row.original.name}
              </span>
              {badge ? <Badge variant={badge.variant}>{badge.label}</Badge> : null}
            </div>
            <span className="truncate text-sm text-text-secondary">
              {spec.rowSubtitle(row.original)}
            </span>
          </div>
        ) : (
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-sm font-medium text-text-primary">
              {row.original.name}
            </span>
            <span className="truncate text-sm text-text-secondary">
              {spec.rowSubtitle(row.original)}
            </span>
          </div>
        )
      },
    },
    {
      id: 'validation',
      header: 'Validation',
      cell: ({ row }) => (
        <StatusChip status={deriveServerValidationStatus(validateResultById[row.original.id])} />
      ),
    },
    ...spec.extraColumns,
    {
      accessorKey: 'updated_at',
      header: 'Updated',
      cell: ({ row }) => (
        <span className="text-sm text-text-secondary">
          {formatRelativeTime(row.original.updated_at, '—')}
        </span>
      ),
    },
  ]
}
