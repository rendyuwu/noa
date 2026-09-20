'use client'

import { useCallback, useMemo, useState } from 'react'
import { PageHeader } from '@gio/bigsu-app-shell'
import { Button, DataTable } from '@gio/bigsu-ui'

import { fetchToolRunDetail, fetchToolRuns } from '@/lib/admin/audit/audit-api'
import { activeToolFilterCount, buildToolRunQuery } from '@/lib/admin/audit/audit-model'
import { useAuditDetail } from '@/lib/admin/audit/use-audit-detail'
import { useAuditList } from '@/lib/admin/audit/use-audit-list'
import {
  DEFAULT_TOOL_FILTERS,
  type AuditToolRunDetail,
  type AuditToolRunListItem,
  type ToolRunFilters,
} from '@/lib/admin/audit/types'

import { ToolRunFilterBar } from './audit-filters'
import { AuditPagination } from './audit-pagination'
import { ToolRunDetailDrawer } from './tool-run-detail-drawer'
import { buildToolRunColumns } from './tool-run-columns'

const TOOL_COLUMNS = buildToolRunColumns()

// The audit administration page. One view — the `tool_runs` trail, READ and
// CHANGE alike — server-filtered and cursor-paged: the controller owns the query
// state, the DataTable stays presentational, and the standalone Pagination drives
// server paging off the cursor the API returns.
//
// It was ported with two tabs. The other one listed action requests and linked to
// a receipt page, both against routes the admin API's contract does not name and NOA does not
// serve, so every load 404'd; the tab went with the transports rather than being
// stubbed. With one view there is no tab strip and no URL/pushState dance
// to keep in step with it — `/admin/audit` is the whole surface, which is where
// the nav already pointed.
//
// A row opens a read-only detail drawer. The trail is append-only and its writers
// are the MCP tool path and the approval executor, so there is no create, edit or
// delete affordance anywhere on this page.
export function AuditAdminPage() {
  // Bound straight to the tool-runs endpoint and query builder: the generic
  // controller owns the paging, the race guard and the cursor stack, and the
  // active-filter count is only read here to pick the empty state's copy.
  const toolRuns = useAuditList<AuditToolRunListItem, ToolRunFilters>({
    enabled: true,
    defaultFilters: DEFAULT_TOOL_FILTERS,
    errorFallback: 'Unable to load tool runs',
    buildQuery: buildToolRunQuery,
    fetchPage: fetchToolRuns,
  })
  const filterCount = useMemo(
    () => activeToolFilterCount(toolRuns.activeFilters),
    [toolRuns.activeFilters],
  )
  const toolRunDetail = useAuditDetail<AuditToolRunDetail>(
    fetchToolRunDetail,
    'Unable to load tool run detail',
  )

  const [selectedToolRun, setSelectedToolRun] = useState<AuditToolRunListItem | null>(null)

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
        description="Every MCP tool execution NOA recorded — read-only tools and approved changes alike. The audit trail is append-only."
      />

      <div className="mt-6 flex flex-col gap-4">
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
            toolRuns.loadError ? { message: toolRuns.loadError, onRetry: toolRuns.reload } : undefined
          }
          onRowClick={openToolRun}
          rowActions={(row) => [
            { label: 'View details', icon: 'externalLink', onSelect: () => openToolRun(row) },
          ]}
          emptyState={
            filterCount > 0
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
      </div>

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
