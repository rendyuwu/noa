'use client'

import { Badge, StatusChip, createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import type { ValidateWhmServerResponse, WhmServer } from '@/lib/admin/whm/types'
import {
  deriveWhmSshStatus,
  deriveWhmValidationStatus,
  formatWhmRelativeTime,
  getWhmResellerBadge,
  getWhmTlsBadge,
} from '@/lib/admin/whm/whm-status'

// DataTable columns for the WHM servers list (issue #108). The identifier renders
// through createIdColumn so the full server id stays monospace and copyable;
// validation and SSH lifecycle render through StatusChip (standard vocabulary),
// while TLS verification — a configuration label, not a workflow status — renders
// through Badge. updated_at keeps its raw accessorKey so the header sort orders by
// the real timestamp while the cell shows the friendly relative form. The Server
// cell also carries a "Reseller credential" Badge, shown only on `true` rows
// (§V.109) — an operator scanning the list should not have to open every row to
// tell a scoped credential from root.
export function buildServerColumns(
  validateResultById: Record<string, ValidateWhmServerResponse>,
): DataTableProps<WhmServer>['columns'] {
  return [
    createIdColumn<WhmServer>({ header: 'Server ID' }),
    {
      accessorKey: 'name',
      header: 'Server',
      cell: ({ row }) => {
        const reseller = getWhmResellerBadge(row.original)
        return (
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm font-medium text-text-primary">
                {row.original.name}
              </span>
              {reseller ? <Badge variant={reseller.variant}>{reseller.label}</Badge> : null}
            </div>
            <span className="truncate text-sm text-text-secondary">{row.original.base_url}</span>
          </div>
        )
      },
    },
    {
      id: 'validation',
      header: 'Validation',
      cell: ({ row }) => (
        <StatusChip status={deriveWhmValidationStatus(validateResultById[row.original.id])} />
      ),
    },
    {
      id: 'tls',
      header: 'TLS',
      cell: ({ row }) => {
        const tls = getWhmTlsBadge(row.original)
        return <Badge variant={tls.variant}>{tls.label}</Badge>
      },
    },
    {
      id: 'ssh',
      header: 'SSH',
      cell: ({ row }) => <StatusChip status={deriveWhmSshStatus(row.original)} />,
    },
    {
      accessorKey: 'updated_at',
      header: 'Updated',
      cell: ({ row }) => (
        <span className="text-sm text-text-secondary">
          {formatWhmRelativeTime(row.original.updated_at)}
        </span>
      ),
    },
  ]
}
