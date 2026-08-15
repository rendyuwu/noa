'use client'

import { useCallback, useEffect, useState } from 'react'
import { PageHeader } from '@gio/bigsu-app-shell'
import { Button, DataTable, Tabs, TabsList, TabsTrigger } from '@gio/bigsu-ui'

import { fetchActionRequestDetail, fetchToolRunDetail } from '@/lib/admin/audit/audit-api'
import { useAuditActionRequests } from '@/lib/admin/audit/use-audit-action-requests'
import { useAuditDetail } from '@/lib/admin/audit/use-audit-detail'
import { useAuditToolRuns } from '@/lib/admin/audit/use-audit-tool-runs'
import type {
  AuditActionRequestDetail,
  AuditActionRequestListItem,
  AuditTab,
  AuditToolRunDetail,
  AuditToolRunListItem,
} from '@/lib/admin/audit/types'

import { ActionRequestDetailDrawer } from './action-request-detail-drawer'
import { buildActionRequestColumns } from './action-request-columns'
import { ActionRequestFilterBar, ToolRunFilterBar } from './audit-filters'
import { AuditPagination } from './audit-pagination'
import { ToolRunDetailDrawer } from './tool-run-detail-drawer'
import { buildToolRunColumns } from './tool-run-columns'

const ACTION_COLUMNS = buildActionRequestColumns()
const TOOL_COLUMNS = buildToolRunColumns()

function pathForTab(tab: AuditTab): string {
  return tab === 'tool-runs' ? '/admin/audit/tool-runs' : '/admin/audit'
}

function tabFromPathname(pathname: string): AuditTab {
  return pathname.endsWith('/tool-runs') ? 'tool-runs' : 'action-requests'
}

// The audit administration page (issue #111). Two peer views of the same audit
// history — action requests and tool runs — switch through Tabs, and the active
// tab is reflected in the URL (pushState) so deep links and the browser
// back/forward buttons land on the right view (popstate resync). Each tab owns
// its own server-paged, server-filtered controller; the DataTable stays
// presentational while the controller owns the query state and the standalone
// cursor Pagination owns server paging. A row opens a read-only detail drawer —
// audit history is append-only, so there is no create/edit/delete affordance.
export function AuditAdminPage({ initialTab = 'action-requests' }: { initialTab?: AuditTab }) {
  const [activeTab, setActiveTab] = useState<AuditTab>(initialTab)

  const actions = useAuditActionRequests(activeTab === 'action-requests')
  const toolRuns = useAuditToolRuns(activeTab === 'tool-runs')

  const actionDetail = useAuditDetail<AuditActionRequestDetail>(
    fetchActionRequestDetail,
    'Unable to load action request detail',
  )
  const toolRunDetail = useAuditDetail<AuditToolRunDetail>(
    fetchToolRunDetail,
    'Unable to load tool run detail',
  )

  const [selectedAction, setSelectedAction] = useState<AuditActionRequestListItem | null>(null)
  const [selectedToolRun, setSelectedToolRun] = useState<AuditToolRunListItem | null>(null)

  // The active tab resyncs from the URL on browser back/forward — the setState
  // runs inside the popstate handler, never synchronously at render. initialTab
  // is only the mount default (the two routes remount with their own value), so
  // no prop-sync effect is needed.
  useEffect(() => {
    const syncFromHistory = () => setActiveTab(tabFromPathname(window.location.pathname))
    window.addEventListener('popstate', syncFromHistory)
    return () => window.removeEventListener('popstate', syncFromHistory)
  }, [])

  const switchTab = useCallback(
    (tab: AuditTab) => {
      if (tab === activeTab) return
      setActiveTab(tab)
      setSelectedAction(null)
      setSelectedToolRun(null)
      if (typeof window !== 'undefined' && window.location.pathname !== pathForTab(tab)) {
        window.history.pushState(null, '', pathForTab(tab))
      }
    },
    [activeTab],
  )

  const openAction = useCallback(
    (row: AuditActionRequestListItem) => {
      setSelectedAction(row)
      actionDetail.load(row.actionRequestId)
    },
    [actionDetail],
  )

  const openToolRun = useCallback(
    (row: AuditToolRunListItem) => {
      setSelectedToolRun(row)
      toolRunDetail.load(row.toolRunId)
    },
    [toolRunDetail],
  )

  return (
    <>
      <PageHeader
        breadcrumb={[{ label: 'Administration', href: '/admin' }, { label: 'Audit' }]}
        title="Audit"
        description="Review action-request approvals, tool executions, and receipts across all threads. Audit history is append-only and read-only."
      />

      <div className="mt-6 flex flex-col gap-4">
        <Tabs value={activeTab} onValueChange={(value) => switchTab(value as AuditTab)}>
          <TabsList>
            <TabsTrigger value="action-requests">Action requests</TabsTrigger>
            <TabsTrigger value="tool-runs">Tool runs</TabsTrigger>
          </TabsList>
        </Tabs>

        {activeTab === 'action-requests' ? (
          <>
            <ActionRequestFilterBar
              draft={actions.draft}
              setDraft={actions.setDraft}
              onApply={actions.applyFilters}
              onClear={actions.clearFilters}
            />
            <div className="flex justify-end">
              <Button size="sm" onClick={actions.applyFilters} disabled={actions.loading}>
                Apply filters
              </Button>
            </div>
            <DataTable
              columns={ACTION_COLUMNS}
              data={actions.items}
              getRowId={(row) => row.actionRequestId}
              loading={actions.loading}
              error={
                actions.loadError
                  ? { message: actions.loadError, onRetry: actions.reload }
                  : undefined
              }
              onRowClick={openAction}
              rowActions={(row) => [
                { label: 'View details', icon: 'externalLink', onSelect: () => openAction(row) },
              ]}
              emptyState={
                actions.filterCount > 0
                  ? {
                      title: 'No matching action requests',
                      description: 'Adjust the filters to see more results.',
                      action: (
                        <Button size="sm" variant="outline" onClick={actions.clearFilters}>
                          Clear filters
                        </Button>
                      ),
                    }
                  : {
                      title: 'No audit events',
                      description: 'Approval receipts and audit activity will appear here.',
                    }
              }
            />
            <AuditPagination
              itemCount={actions.items.length}
              pageIndex={actions.pageIndex}
              canGoPrev={actions.canGoPrev}
              canGoNext={actions.canGoNext}
              onPrev={actions.goPrev}
              onNext={actions.goNext}
            />
          </>
        ) : (
          <>
            <ToolRunFilterBar
              draft={toolRuns.draft}
              setDraft={toolRuns.setDraft}
              onApply={toolRuns.applyFilters}
              onClear={toolRuns.clearFilters}
            />
            <div className="flex justify-end">
              <Button size="sm" onClick={toolRuns.applyFilters} disabled={toolRuns.loading}>
                Apply filters
              </Button>
            </div>
            <DataTable
              columns={TOOL_COLUMNS}
              data={toolRuns.items}
              getRowId={(row) => row.toolRunId}
              loading={toolRuns.loading}
              error={
                toolRuns.loadError
                  ? { message: toolRuns.loadError, onRetry: toolRuns.reload }
                  : undefined
              }
              onRowClick={openToolRun}
              rowActions={(row) => [
                { label: 'View details', icon: 'externalLink', onSelect: () => openToolRun(row) },
              ]}
              emptyState={
                toolRuns.filterCount > 0
                  ? {
                      title: 'No matching tool runs',
                      description: 'Adjust the filters to see more results.',
                      action: (
                        <Button size="sm" variant="outline" onClick={toolRuns.clearFilters}>
                          Clear filters
                        </Button>
                      ),
                    }
                  : {
                      title: 'No tool runs',
                      description: 'READ and CHANGE tool executions will appear here.',
                    }
              }
            />
            <AuditPagination
              itemCount={toolRuns.items.length}
              pageIndex={toolRuns.pageIndex}
              canGoPrev={toolRuns.canGoPrev}
              canGoNext={toolRuns.canGoNext}
              onPrev={toolRuns.goPrev}
              onNext={toolRuns.goNext}
            />
          </>
        )}
      </div>

      <ActionRequestDetailDrawer
        row={selectedAction}
        detail={selectedAction ? actionDetail.detailsById[selectedAction.actionRequestId] : undefined}
        loading={Boolean(selectedAction) && actionDetail.loadingId === selectedAction?.actionRequestId}
        error={
          selectedAction && actionDetail.errorId === selectedAction.actionRequestId
            ? actionDetail.error
            : null
        }
        onClose={() => setSelectedAction(null)}
      />

      <ToolRunDetailDrawer
        row={selectedToolRun}
        detail={selectedToolRun ? toolRunDetail.detailsById[selectedToolRun.toolRunId] : undefined}
        loading={Boolean(selectedToolRun) && toolRunDetail.loadingId === selectedToolRun?.toolRunId}
        error={
          selectedToolRun && toolRunDetail.errorId === selectedToolRun.toolRunId
            ? toolRunDetail.error
            : null
        }
        onClose={() => setSelectedToolRun(null)}
      />
    </>
  )
}
