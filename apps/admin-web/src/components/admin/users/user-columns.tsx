'use client'

import { Badge, StatusChip, createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import type { AdminUser } from '@/lib/admin/users/types'
import {
  deriveUserStatus,
  formatDate,
  formatRelativeTime,
} from '@/lib/admin/users/user-status'
import { coerceStringArray } from '@/lib/admin/users/users-api'

// DataTable columns for the Users list (issue #106). The identifier renders
// through createIdColumn so the full user id stays monospace and copyable (never
// truncated); status renders through StatusChip so it looks identical to every
// other BIGSU app; roles are non-status labels, so they use Badge, not StatusChip.
// created/lastLogin keep their raw accessorKey so the header sort orders by the
// real timestamp while the cell shows the friendly form.
export const userColumns: DataTableProps<AdminUser>['columns'] = [
  createIdColumn<AdminUser>({ header: 'User ID' }),
  {
    accessorKey: 'email',
    header: 'User',
    cell: ({ row }) => {
      const user = row.original
      const subtitle =
        user.display_name && user.display_name !== user.email ? user.display_name : null
      return (
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-medium text-text-primary">{user.email}</span>
          {subtitle ? (
            <span className="truncate text-sm text-text-secondary">{subtitle}</span>
          ) : null}
        </div>
      )
    },
  },
  {
    id: 'status',
    header: 'Status',
    cell: ({ row }) => <StatusChip status={deriveUserStatus(row.original)} />,
  },
  {
    id: 'roles',
    header: 'Roles',
    cell: ({ row }) => {
      const roles = coerceStringArray(row.original.roles)
      if (roles.length === 0) {
        return <span className="text-sm text-text-secondary">No roles</span>
      }
      return (
        <div className="flex flex-wrap gap-1.5">
          {roles.map((role) => (
            <Badge key={role}>{role}</Badge>
          ))}
        </div>
      )
    },
  },
  {
    accessorKey: 'created_at',
    header: 'Created',
    cell: ({ row }) => (
      <span className="text-sm text-text-secondary">{formatDate(row.original.created_at)}</span>
    ),
  },
  {
    accessorKey: 'last_login_at',
    header: 'Last login',
    cell: ({ row }) => (
      <span className="text-sm text-text-secondary">
        {formatRelativeTime(row.original.last_login_at)}
      </span>
    ),
  },
]
