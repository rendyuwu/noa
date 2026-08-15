'use client'

import { createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import type { RoleRow } from './role-row'

// DataTable columns for the Roles list (issue #107). A role is identified by its
// name, so the identifier renders through createIdColumn against `name` — the
// full name stays monospace and copyable, matching the Users table convention
// (operators grep these). The tool count renders its async state honestly: a
// dash until the background count for that role has resolved, then a pluralized
// count.
export const roleColumns: DataTableProps<RoleRow>['columns'] = [
  createIdColumn<RoleRow>({ accessorKey: 'name', header: 'Role' }),
  {
    id: 'tools',
    header: 'Tools',
    cell: ({ row }) => {
      const count = row.original.toolCount
      return (
        <span className="text-sm text-text-secondary">
          {typeof count === 'number' ? `${count} tool${count === 1 ? '' : 's'} assigned` : '—'}
        </span>
      )
    },
  },
]
