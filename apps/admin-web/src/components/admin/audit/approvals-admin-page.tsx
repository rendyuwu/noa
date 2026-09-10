'use client'

import { useCallback, useState } from 'react'
import { PageHeader } from '@gio/bigsu-app-shell'
import { Button, DataTable } from '@gio/bigsu-ui'

import { fetchActionReceipt, fetchActionRequestDetail } from '@/lib/admin/audit/audit-api'
import { useAuditActionRequests } from '@/lib/admin/audit/use-audit-action-requests'
import { useAuditDetail } from '@/lib/admin/audit/use-audit-detail'
import type {
  AuditActionReceipt,
  AuditActionRequestDetail,
  AuditActionRequestListItem,
} from '@/lib/admin/audit/types'

import { ActionRequestFilterBar } from './audit-filters'
import { AuditPagination } from './audit-pagination'
import { ActionRequestDetailDrawer } from './action-request-detail-drawer'
import { buildActionRequestColumns } from './action-request-columns'

const ACTION_REQUEST_COLUMNS = buildActionRequestColumns()

// The CHANGE authorisation surface (§I.admin-api). Its own page rather than a
// tab beside `/admin/audit`: T55 removed the tab strip when the ported second
// tab turned out to call routes NOA did not serve, and the recorded call was
// that `/admin/audit` is *the* audit view rather than a container for tabs. Two
// addresses for two questions keeps that true — what ran, and who authorised it
// — and neither view has to stay in step with the other's URL state.
//
// Read-only, all the way down. Every row here has already been decided by an
// operator on the approval card or by the expiry sweep; the one writer of a
// terminal status is on the far side of that boundary, so there is no approve,
// deny or retry affordance anywhere on this page and the API offers none to
// call.
//
// Two lazy loaders rather than one. The detail carries the reason and the gate
// context; the receipt is a separate route because a denied, expired or pending
// request has none, and folding it into the detail would make the detail fail
// for most rows. The receipt is only fetched when the row says it has one, so
// the common "no run was started" case costs no request and shows no error.
export function ApprovalsAdminPage() {
  const requests = useAuditActionRequests(true)
  const requestDetail = useAuditDetail<AuditActionRequestDetail>(
    fetchActionRequestDetail,
    'Unable to load approval request detail',
  )
  const receiptDetail = useAuditDetail<AuditActionReceipt>(
    fetchActionReceipt,
    'Unable to load the receipt for this request',
  )

  const [selected, setSelected] = useState<AuditActionRequestListItem | null>(null)

  const openRequest = useCallback(
    (row: AuditActionRequestListItem) => {
      setSelected(row)
      requestDetail.load(row.actionRequestId)
      if (row.hasReceipt) receiptDetail.load(row.actionRequestId)
    },
    [receiptDetail, requestDetail],
  )

  const selectedId = selected?.actionRequestId

  return (
    <>
      <PageHeader
        breadcrumb={[{ label: 'Administration', href: '/admin' }, { label: 'Approvals' }]}
        title="Approvals"
        description="Every change NOA asked an operator to authorise — the decision, the reason they gave, and what the change did. Read-only."
      />

      <div className="mt-6 flex flex-col gap-4">
        <ActionRequestFilterBar
          draft={requests.draft}
          setDraft={requests.setDraft}
          onApply={requests.applyFilters}
          onClear={requests.clearFilters}
        />
        <div className="flex justify-end">
          <Button size="sm" onClick={requests.applyFilters} disabled={requests.loading}>
            Apply filters
          </Button>
        </div>
        <DataTable
          columns={ACTION_REQUEST_COLUMNS}
          data={requests.items}
          getRowId={(row) => row.actionRequestId}
          loading={requests.loading}
          error={
            requests.loadError ? { message: requests.loadError, onRetry: requests.reload } : undefined
          }
          onRowClick={openRequest}
          rowActions={(row) => [
            { label: 'View details', icon: 'externalLink', onSelect: () => openRequest(row) },
          ]}
          emptyState={
            requests.filterCount > 0
              ? {
                  title: 'No matching approval requests',
                  description: 'Adjust the filters to see more results.',
                  action: (
                    <Button size="sm" variant="outline" onClick={requests.clearFilters}>
                      Clear filters
                    </Button>
                  ),
                }
              : {
                  title: 'No approval requests',
                  description: 'Changes awaiting or holding an operator decision will appear here.',
                }
          }
        />
        <AuditPagination
          itemCount={requests.items.length}
          pageIndex={requests.pageIndex}
          canGoPrev={requests.canGoPrev}
          canGoNext={requests.canGoNext}
          onPrev={requests.goPrev}
          onNext={requests.goNext}
        />
      </div>

      <ActionRequestDetailDrawer
        row={selected}
        detail={selectedId ? requestDetail.detailsById[selectedId] : undefined}
        detailLoading={Boolean(selectedId) && requestDetail.loadingId === selectedId}
        detailError={selectedId && requestDetail.errorId === selectedId ? requestDetail.error : null}
        receipt={selectedId ? receiptDetail.detailsById[selectedId] : undefined}
        receiptLoading={Boolean(selectedId) && receiptDetail.loadingId === selectedId}
        receiptError={selectedId && receiptDetail.errorId === selectedId ? receiptDetail.error : null}
        onClose={() => setSelected(null)}
      />
    </>
  )
}
