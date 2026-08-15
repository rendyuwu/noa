'use client'

import { Badge, StatusChip, createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import type { ProxmoxServer, ValidateProxmoxServerResponse } from '@/lib/admin/proxmox/types'
import {
  deriveProxmoxValidationStatus,
  formatProxmoxRelativeTime,
  getProxmoxTlsBadge,
  getProxmoxTokenSecretBadge,
} from '@/lib/admin/proxmox/proxmox-status'

// DataTable columns for the Proxmox servers list (issue #109). The identifier
// renders through createIdColumn so the full server id stays monospace and
// copyable; validation renders through StatusChip (standard vocabulary), while
// TLS verification and stored-token presence — configuration labels, not workflow
// statuses — render through Badge. updated_at keeps its raw accessorKey so the
// header sort orders by the real timestamp while the cell shows the friendly
// relative form.
export function buildServerColumns(
  validateResultById: Record<string, ValidateProxmoxServerResponse>,
): DataTableProps<ProxmoxServer>['columns'] {
  return [
    createIdColumn<ProxmoxServer>({ header: 'Server ID' }),
    {
      accessorKey: 'name',
      header: 'Server',
      cell: ({ row }) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-medium text-text-primary">{row.original.name}</span>
          <span className="truncate text-sm text-text-secondary">{row.original.base_url}</span>
        </div>
      ),
    },
    {
      id: 'validation',
      header: 'Validation',
      cell: ({ row }) => (
        <StatusChip status={deriveProxmoxValidationStatus(validateResultById[row.original.id])} />
      ),
    },
    {
      id: 'tls',
      header: 'TLS',
      cell: ({ row }) => {
        const tls = getProxmoxTlsBadge(row.original)
        return <Badge variant={tls.variant}>{tls.label}</Badge>
      },
    },
    {
      id: 'token',
      header: 'Token secret',
      cell: ({ row }) => {
        const token = getProxmoxTokenSecretBadge(row.original)
        return <Badge variant={token.variant}>{token.label}</Badge>
      },
    },
    {
      accessorKey: 'updated_at',
      header: 'Updated',
      cell: ({ row }) => (
        <span className="text-sm text-text-secondary">
          {formatProxmoxRelativeTime(row.original.updated_at)}
        </span>
      ),
    },
  ]
}
