'use client'

import { useMemo, useState } from 'react'
import { PageHeader } from '@gio/bigsu-app-shell'
import { Button, DataTable, FilterBar, Select } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import type { VerifiedUser } from '@/lib/auth/use-verified-auth'
import { useUsers } from '@/lib/admin/users/use-users'
import { deriveUserStatus } from '@/lib/admin/users/user-status'

import { userColumns } from './user-columns'
import { UserDetailDrawer } from './user-detail-drawer'

const STATUS_FILTERS = [
  { value: 'all', label: 'All statuses' },
  { value: 'Active', label: 'Active' },
  { value: 'Inactive', label: 'Inactive' },
  { value: 'Pending', label: 'Pending' },
]

// The Users administration page (issue #106): the first concrete BIGSU admin
// vertical. Page order follows the standard — PageHeader, FilterBar, DataTable —
// with the detail Drawer hosted alongside. Filtering is client-side over the
// loaded list (small, admin-only dataset); the table still ships loading,
// empty, error/retry, sort, row actions, stable ids and monospace identifiers.
export function UsersPage({ me }: { me: VerifiedUser }) {
  const {
    users,
    availableRoles,
    loading,
    loadError,
    reload,
    selectedUser,
    selectUser,
    saveRoles,
    setActive,
    removeUser,
  } = useUsers()

  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return users.filter((user) => {
      if (status !== 'all' && deriveUserStatus(user) !== status) return false
      if (!needle) return true
      return (
        user.email.toLowerCase().includes(needle) ||
        (user.display_name ? user.display_name.toLowerCase().includes(needle) : false) ||
        user.id.toLowerCase().includes(needle)
      )
    })
  }, [users, search, status])

  const hasFilters = search.trim() !== '' || status !== 'all'
  const clearFilters = () => {
    setSearch('')
    setStatus('all')
  }

  return (
    <>
      <PageHeader
        breadcrumb={[{ label: 'Administration', href: '/admin' }, { label: 'Users' }]}
        title="Users"
        description="Manage user accounts, activation, roles, and access."
      />

      <div className="mt-6 flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <FilterBar
            className="min-w-0 flex-1"
            search={{
              value: search,
              onChange: setSearch,
              placeholder: 'Search by email, name, or ID',
            }}
            onClear={hasFilters ? clearFilters : undefined}
          >
            <Select
              aria-label="Status"
              className="w-44"
              options={STATUS_FILTERS}
              value={status}
              onValueChange={setStatus}
            />
          </FilterBar>
          <Button variant="secondary" onClick={() => void reload()} disabled={loading}>
            <BigsuIcon name="refresh" size="sm" aria-hidden />
            Refresh
          </Button>
        </div>

        <DataTable
          columns={userColumns}
          data={rows}
          getRowId={(user) => user.id}
          loading={loading}
          error={loadError ? { message: loadError, onRetry: () => void reload() } : undefined}
          onRowClick={(user) => selectUser(user.id)}
          rowActions={(user) => [
            {
              label: 'View details',
              icon: 'externalLink',
              onSelect: () => selectUser(user.id),
            },
          ]}
          pagination={{ pageSize: 10 }}
          emptyState={
            hasFilters
              ? {
                  title: 'No matching users',
                  description: 'Adjust the search or status filter to see more results.',
                  action: (
                    <Button size="sm" variant="outline" onClick={clearFilters}>
                      Clear filters
                    </Button>
                  ),
                }
              : {
                  title: 'No users yet',
                  description: 'Users appear here after they sign in or are provisioned.',
                }
          }
        />
      </div>

      <UserDetailDrawer
        user={selectedUser}
        me={me}
        allUsers={users}
        availableRoles={availableRoles}
        onCloseAction={() => selectUser(null)}
        onSaveRolesAction={saveRoles}
        onSetActiveAction={setActive}
        onDeleteAction={removeUser}
      />
    </>
  )
}
