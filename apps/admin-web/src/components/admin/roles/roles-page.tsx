'use client'

import { useMemo, useState } from 'react'
import { PageHeader } from '@gio/bigsu-app-shell'
import { Button, DataTable, FilterBar } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import { useRoles } from '@/lib/admin/roles/use-roles'

import { CreateRoleDialog } from './create-role-dialog'
import { RoleDetailDrawer } from './role-detail-drawer'
import { roleColumns } from './role-columns'
import type { RoleRow } from './role-row'

// The Roles administration page (issue #107), built on the proven Users
// composition: PageHeader, FilterBar, DataTable, with the detail Drawer and the
// create action hosted alongside. Filtering is client-side over the
// loaded list (small, admin-only dataset); the table ships loading, empty,
// error/retry, sort, row actions, and full monospace role identifiers. The
// controller owns every race guard.
//
// The legacy direct-grant migration control that shipped with the ported panel
// is gone (T65). NOA has no per-user grant table and no migration endpoint —
// permissions flow role → user only, and `PUT /admin/users/{id}/tools` answers
// 410 `direct_tool_grants_disabled` (V75). A button posting to a route that does
// not exist is worse than no button: it reads as a capability.
export function RolesPage() {
  const {
    roles,
    availableTools,
    roleToolCounts,
    loading,
    loadError,
    reload,
    selectedRole,
    selectRole,
    roleTools,
    roleToolsLoading,
    roleToolsError,
    createRole,
    deleteRole,
    saveRoleTools,
  } = useRoles()

  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)

  const rows = useMemo<RoleRow[]>(() => {
    const needle = search.trim().toLowerCase()
    return roles
      .filter((name) => (needle ? name.toLowerCase().includes(needle) : true))
      .map((name) => ({ name, toolCount: roleToolCounts[name] }))
  }, [roles, roleToolCounts, search])

  const hasFilters = search.trim() !== ''
  const clearFilters = () => setSearch('')

  return (
    <>
      <PageHeader
        breadcrumb={[{ label: 'Administration', href: '/admin' }, { label: 'Roles' }]}
        title="Roles"
        description="Manage roles and their tool allowlists."
      />

      <div className="mt-6 flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <FilterBar
            className="min-w-0 flex-1"
            search={{
              value: search,
              onChange: setSearch,
              placeholder: 'Search by role name',
            }}
            onClear={hasFilters ? clearFilters : undefined}
          />
          <Button variant="secondary" onClick={() => void reload()} disabled={loading}>
            <BigsuIcon name="refresh" size="sm" aria-hidden />
            Refresh
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <BigsuIcon name="create" size="sm" aria-hidden />
            Add role
          </Button>
        </div>

        <DataTable
          columns={roleColumns}
          data={rows}
          getRowId={(row) => row.name}
          loading={loading}
          error={loadError ? { message: loadError, onRetry: () => void reload() } : undefined}
          onRowClick={(row) => selectRole(row.name)}
          rowActions={(row) => [
            {
              label: 'Manage allowlist',
              icon: 'externalLink',
              onSelect: () => selectRole(row.name),
            },
          ]}
          pagination={{ pageSize: 10 }}
          emptyState={
            hasFilters
              ? {
                  title: 'No matching roles',
                  description: 'Adjust the search to see more results.',
                  action: (
                    <Button size="sm" variant="outline" onClick={clearFilters}>
                      Clear filters
                    </Button>
                  ),
                }
              : {
                  title: 'No roles yet',
                  description: 'Create a role to define shared access and tool grants.',
                  action: (
                    <Button size="sm" onClick={() => setCreateOpen(true)}>
                      Add role
                    </Button>
                  ),
                }
          }
        />
      </div>

      <CreateRoleDialog
        open={createOpen}
        onOpenChangeAction={setCreateOpen}
        onCreateAction={createRole}
      />

      <RoleDetailDrawer
        role={selectedRole}
        availableTools={availableTools}
        roleTools={roleTools}
        roleToolsLoading={roleToolsLoading}
        roleToolsError={roleToolsError}
        onCloseAction={() => selectRole(null)}
        onSaveToolsAction={saveRoleTools}
        onDeleteAction={deleteRole}
      />
    </>
  )
}
