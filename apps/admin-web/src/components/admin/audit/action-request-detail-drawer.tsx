'use client'

import { useRouter } from 'next/navigation'

import {
  Button,
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from '@gio/bigsu-ui'

import {
  formatCreated,
  formatJson,
  humanizeToolName,
  resolveActionStatus,
  resolveRiskBadge,
  resolveToolRunStatus,
} from '@/lib/admin/audit/audit-format'
import type {
  AuditActionRequestDetail,
  AuditActionRequestListItem,
} from '@/lib/admin/audit/types'

import { AuditStatusCell } from './audit-status-cell'
import { AuditIdRow, DetailRow } from './audit-detail-rows'

// Contextual detail for one action request (issue #111). A Drawer inspects the
// request alongside the list. Audit history is append-only, so the only action
// is a link to the standalone receipt when one exists — never edit or delete.
// The request arguments are server-redacted before they reach the client, so no
// sensitive tool argument appears in the DOM. Its ordered tool runs render as an
// immutable, read-only history.
export function ActionRequestDetailDrawer({
  row,
  detail,
  loading,
  error,
  onClose,
}: {
  row: AuditActionRequestListItem | null
  detail?: AuditActionRequestDetail
  loading: boolean
  error: string | null
  onClose: () => void
}) {
  return (
    <Drawer open={row !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      {row ? (
        <ActionRequestDetailContent
          key={row.actionRequestId}
          row={row}
          detail={detail}
          loading={loading}
          error={error}
        />
      ) : null}
    </Drawer>
  )
}

function ActionRequestDetailContent({
  row,
  detail,
  loading,
  error,
}: {
  row: AuditActionRequestListItem
  detail?: AuditActionRequestDetail
  loading: boolean
  error: string | null
}) {
  const router = useRouter()
  const risk = resolveRiskBadge(row.risk)

  return (
    <DrawerContent>
      <DrawerHeader>
        <DrawerTitle>{humanizeToolName(row.toolName)}</DrawerTitle>
        <DrawerDescription>{row.requestedByEmail?.trim() || 'Unknown requester'}</DrawerDescription>
      </DrawerHeader>

      <div className="flex flex-col gap-6">
        <section className="flex flex-wrap items-center gap-2">
          <AuditStatusCell view={resolveActionStatus(row)} />
          <AuditStatusCell view={{ kind: 'badge', label: risk.label, variant: risk.variant }} />
        </section>

        <dl className="flex flex-col gap-2 text-sm">
          <DetailRow label="Tool" value={<span className="font-mono text-xs">{row.toolName}</span>} />
          <DetailRow label="Created" value={formatCreated(row.createdAt).title || '—'} />
          <DetailRow
            label="Decided"
            value={detail?.decidedAt ? formatCreated(detail.decidedAt).title : '—'}
          />
          <DetailRow label="Decided by" value={detail?.decidedByEmail?.trim() || '—'} />
        </dl>

        <section className="flex flex-col gap-2 border-t border-border-default pt-5">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Identifiers
          </span>
          <dl className="flex flex-col gap-2 text-sm">
            <AuditIdRow label="Thread" value={row.threadId} />
            <AuditIdRow label="Action request" value={row.actionRequestId} />
            <AuditIdRow label="Tool run" value={row.toolRunId} />
            <AuditIdRow label="Receipt" value={row.receiptId} />
          </dl>
        </section>

        <section className="flex flex-col gap-2 border-t border-border-default pt-5">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Tool run history
          </span>
          {loading ? (
            <p className="text-sm text-text-secondary">Loading detail…</p>
          ) : error ? (
            <p className="text-sm text-status-danger" role="alert">
              {error}
            </p>
          ) : detail ? (
            <ActionRequestDetailBody detail={detail} />
          ) : (
            <p className="text-sm text-text-secondary">No detail available.</p>
          )}
        </section>
      </div>

      {row.hasReceipt ? (
        <DrawerFooter>
          <Button
            onClick={() =>
              router.push(`/admin/audit/receipts/${encodeURIComponent(row.actionRequestId)}`)
            }
          >
            View receipt
          </Button>
        </DrawerFooter>
      ) : null}
    </DrawerContent>
  )
}

function ActionRequestDetailBody({ detail }: { detail: AuditActionRequestDetail }) {
  return (
    <div className="flex flex-col gap-4">
      {detail.toolRuns.length === 0 ? (
        <p className="text-sm text-text-secondary">No tool runs recorded for this request.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {detail.toolRuns.map((run) => (
            <li
              key={run.id}
              className="flex items-center justify-between gap-3 rounded-md border border-border-default bg-surface px-3 py-2"
            >
              <span className="min-w-0 break-all font-mono text-xs text-text-primary">{run.id}</span>
              <AuditStatusCell view={resolveToolRunStatus(run.status)} />
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-col gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
          Arguments
        </span>
        <pre className="max-h-72 overflow-auto rounded-md border border-border-default bg-surface p-3 font-mono text-xs text-text-primary">
          {formatJson(detail.args)}
        </pre>
      </div>
    </div>
  )
}
