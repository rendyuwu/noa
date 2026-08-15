'use client'

import { useMemo, useState } from 'react'
import { PageHeader } from '@gio/bigsu-app-shell'
import { Button, DataTable, FilterBar } from '@gio/bigsu-ui'
import { BigsuIcon } from '@gio/bigsu-icons'

import { useProxmoxServers } from '@/lib/admin/proxmox/use-proxmox-servers'

import { buildServerColumns } from './server-columns'
import { ServerDetailDrawer } from './server-detail-drawer'
import { ServerFormDialog } from './server-form-dialog'

// The Proxmox servers administration page (issue #109), built on the proven
// Users/Roles/WHM composition: PageHeader, FilterBar, DataTable, with the detail
// Drawer and create/edit dialog hosted alongside. Filtering is client-side over
// the loaded list (small, admin-only dataset); the table ships loading, empty,
// error/retry, sort, row actions, full monospace identifiers, and standard status
// vocabulary. The controller owns every race guard. No secret is ever rendered.
export function ProxmoxServersPage() {
  const {
    servers,
    loading,
    loadError,
    reload,
    selectedServer,
    selectServer,
    validateResultById,
    validateBusyId,
    deleteBusyId,
    createServer,
    updateServer,
    deleteServer,
    validateServer,
  } = useProxmoxServers()

  const [search, setSearch] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)

  const columns = useMemo(() => buildServerColumns(validateResultById), [validateResultById])

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    if (!needle) return servers
    return servers.filter(
      (server) =>
        server.name.toLowerCase().includes(needle) ||
        server.base_url.toLowerCase().includes(needle) ||
        server.id.toLowerCase().includes(needle),
    )
  }, [servers, search])

  const hasFilters = search.trim() !== ''
  const clearFilters = () => setSearch('')

  return (
    <>
      <PageHeader
        breadcrumb={[{ label: 'Administration', href: '/admin' }, { label: 'Proxmox servers' }]}
        title="Proxmox servers"
        description="Manage Proxmox API credentials, SSL verification, and connection validation. The API token secret is stored encrypted and never displayed after save."
      />

      <div className="mt-6 flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <FilterBar
            className="min-w-0 flex-1"
            search={{
              value: search,
              onChange: setSearch,
              placeholder: 'Search by name, URL, or ID',
            }}
            onClear={hasFilters ? clearFilters : undefined}
          />
          <Button variant="secondary" onClick={() => void reload()} disabled={loading}>
            <BigsuIcon name="refresh" size="sm" aria-hidden />
            Refresh
          </Button>
          <Button onClick={() => setCreateOpen(true)}>
            <BigsuIcon name="create" size="sm" aria-hidden />
            Add server
          </Button>
        </div>

        <DataTable
          columns={columns}
          data={rows}
          getRowId={(server) => server.id}
          loading={loading}
          error={loadError ? { message: loadError, onRetry: () => void reload() } : undefined}
          onRowClick={(server) => selectServer(server.id)}
          rowActions={(server) => [
            {
              label: 'View details',
              icon: 'externalLink',
              onSelect: () => selectServer(server.id),
            },
          ]}
          pagination={{ pageSize: 10 }}
          emptyState={
            hasFilters
              ? {
                  title: 'No matching servers',
                  description: 'Adjust the search to see more results.',
                  action: (
                    <Button size="sm" variant="outline" onClick={clearFilters}>
                      Clear filters
                    </Button>
                  ),
                }
              : {
                  title: 'No Proxmox servers yet',
                  description: 'Add a Proxmox server to manage virtualization infrastructure.',
                  action: (
                    <Button size="sm" onClick={() => setCreateOpen(true)}>
                      Add server
                    </Button>
                  ),
                }
          }
        />
      </div>

      <ServerFormDialog
        open={createOpen}
        mode="create"
        existingServer={null}
        onOpenChangeAction={setCreateOpen}
        onSubmitAction={createServer}
      />

      <ServerFormDialog
        open={editOpen}
        mode="update"
        existingServer={selectedServer}
        onOpenChangeAction={setEditOpen}
        onSubmitAction={(body) =>
          selectedServer
            ? updateServer(selectedServer.id, body)
            : Promise.resolve({ ok: false as const, message: 'No server selected', current: false })
        }
      />

      <ServerDetailDrawer
        server={selectedServer}
        validateResult={selectedServer ? validateResultById[selectedServer.id] : undefined}
        validateBusy={selectedServer ? validateBusyId === selectedServer.id : false}
        deleteBusy={selectedServer ? deleteBusyId === selectedServer.id : false}
        onCloseAction={() => selectServer(null)}
        onEditAction={() => setEditOpen(true)}
        onValidateAction={validateServer}
        onDeleteAction={deleteServer}
      />
    </>
  )
}
