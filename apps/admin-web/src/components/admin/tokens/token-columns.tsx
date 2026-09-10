'use client'

import { StatusChip, createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import { formatRelativeTime } from '@/lib/admin/shared/relative-time'
import { deriveTokenStatus, formatBinding } from '@/lib/admin/tokens/token-status'
import type { McpToken } from '@/lib/admin/tokens/types'

// DataTable columns for the MCP token list (§T76). The identifier column is the
// `token_prefix`, not the row id: the prefix is what an operator can match
// against the value pasted into LibreChat's `customUserVars`, and createIdColumn
// keeps it monospace so it can be read character-by-character against a config
// file. The row id still exists (it is what revoke addresses) but it identifies
// nothing the operator holds, so it is not a column.
//
// There is no plaintext column and there cannot be one — `McpToken` has no such
// field (types.ts) and the API never returns it after the mint.
//
// `last_used_at` and `created_at` keep their raw accessorKey so the header sort
// orders by the real ISO timestamp while the cell shows the friendly relative
// form; `formatRelativeTime` renders a never-used token as `Never`, identical to
// the Users table.
export const tokenColumns: DataTableProps<McpToken>['columns'] = [
  createIdColumn<McpToken>({ accessorKey: 'token_prefix', header: 'Token' }),
  {
    accessorKey: 'label',
    header: 'Label',
    cell: ({ row }) => (
      <span className="text-sm text-text-primary">
        {row.original.label ?? <span className="text-text-secondary">—</span>}
      </span>
    ),
  },
  {
    accessorKey: 'created_at',
    header: 'Created',
    cell: ({ row }) => (
      <span className="text-sm text-text-secondary">
        {formatRelativeTime(row.original.created_at)}
      </span>
    ),
  },
  {
    accessorKey: 'last_used_at',
    header: 'Last used',
    cell: ({ row }) => (
      <span className="text-sm text-text-secondary">
        {formatRelativeTime(row.original.last_used_at)}
      </span>
    ),
  },
  {
    id: 'status',
    header: 'Status',
    cell: ({ row }) => <StatusChip status={deriveTokenStatus(row.original)} />,
  },
  {
    id: 'binding',
    header: 'LibreChat identity',
    cell: ({ row }) => (
      <span className="truncate font-mono text-xs text-text-secondary">
        {formatBinding(row.original)}
      </span>
    ),
  },
]
