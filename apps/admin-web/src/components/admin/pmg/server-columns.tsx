'use client'

import { Badge, StatusChip, createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import type { PmgServer, ValidatePmgServerResponse } from '@/lib/admin/pmg/types'
import {
  derivePmgSshStatus,
  derivePmgValidationStatus,
  formatPmgRelativeTime,
  getPmgFingerprintBadge,
} from '@/lib/admin/pmg/pmg-status'

// DataTable columns for the PMG servers list (issue #110). The identifier renders
// through createIdColumn so the full server id stays monospace and copyable;
// validation and SSH lifecycle render through StatusChip (standard vocabulary),
// while host-key pinning — a configuration label, not a workflow status — renders
// through Badge. updated_at keeps its raw accessorKey so the header sort orders by
// the real timestamp while the cell shows the friendly relative form. PMG is
// SSH-only, so the fingerprint is the transport-security signal (no TLS column).
export function buildServerColumns(
  validateResultById: Record<string, ValidatePmgServerResponse>,
): DataTableProps<PmgServer>['columns'] {
  return [
    createIdColumn<PmgServer>({ header: 'Server ID' }),
    {
      accessorKey: 'name',
      header: 'Server',
      cell: ({ row }) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-medium text-text-primary">{row.original.name}</span>
          <span className="truncate text-sm text-text-secondary">{row.original.ssh_host}</span>
        </div>
      ),
    },
    {
      id: 'validation',
      header: 'Validation',
      cell: ({ row }) => (
        <StatusChip status={derivePmgValidationStatus(validateResultById[row.original.id])} />
      ),
    },
    {
      id: 'ssh',
      header: 'SSH',
      cell: ({ row }) => <StatusChip status={derivePmgSshStatus(row.original)} />,
    },
    {
      id: 'fingerprint',
      header: 'Host key',
      cell: ({ row }) => {
        const fingerprint = getPmgFingerprintBadge(row.original)
        return <Badge variant={fingerprint.variant}>{fingerprint.label}</Badge>
      },
    },
    {
      accessorKey: 'updated_at',
      header: 'Updated',
      cell: ({ row }) => (
        <span className="text-sm text-text-secondary">
          {formatPmgRelativeTime(row.original.updated_at)}
        </span>
      ),
    },
  ]
}
