'use client'

import {
  Drawer,
  DrawerContent,
  DrawerDescription,
  DrawerHeader,
  DrawerTitle,
} from '@gio/bigsu-ui'

import {
  formatCreated,
  formatDuration,
  formatJson,
  humanizeToolName,
  resolveRiskBadge,
  resolveToolRunStatus,
} from '@/lib/admin/audit/audit-format'
import type { AuditToolRunDetail, AuditToolRunListItem } from '@/lib/admin/audit/types'

import { AuditStatusCell } from './audit-status-cell'
import { AuditIdRow, DetailRow } from './audit-detail-rows'

// Contextual detail for one tool run (issue #111). A Drawer inspects a run
// alongside the list without losing it — audit history is read-only, so there
// is no edit or delete affordance here. Technical identifiers (thread, run,
// action) render full and monospace. The arguments block is server-redacted
// before it reaches the client, so sensitive tool arguments never appear in the
// DOM, and there is no export path off this panel.
export function ToolRunDetailDrawer({
  row,
  detail,
  loading,
  error,
  onClose,
}: {
  row: AuditToolRunListItem | null
  detail?: AuditToolRunDetail
  loading: boolean
  error: string | null
  onClose: () => void
}) {
  return (
    <Drawer open={row !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      {row ? (
        <ToolRunDetailContent
          key={row.toolRunId}
          row={row}
          detail={detail}
          loading={loading}
          error={error}
        />
      ) : null}
    </Drawer>
  )
}

function ToolRunDetailContent({
  row,
  detail,
  loading,
  error,
}: {
  row: AuditToolRunListItem
  detail?: AuditToolRunDetail
  loading: boolean
  error: string | null
}) {
  const created = formatCreated(row.createdAt)
  const risk = resolveRiskBadge(row.risk)

  return (
    <DrawerContent>
      <DrawerHeader>
        <DrawerTitle>{humanizeToolName(row.toolName)}</DrawerTitle>
        <DrawerDescription>{row.requestedByEmail?.trim() || 'Unknown requester'}</DrawerDescription>
      </DrawerHeader>

      <div className="flex flex-col gap-6">
        <section className="flex flex-wrap items-center gap-2">
          <AuditStatusCell view={resolveToolRunStatus(row.status)} />
          <AuditStatusCell view={{ kind: 'badge', label: risk.label, variant: risk.variant }} />
        </section>

        <dl className="flex flex-col gap-2 text-sm">
          <DetailRow label="Tool" value={<span className="font-mono text-xs">{row.toolName}</span>} />
          <DetailRow label="Created" value={created.title || '—'} />
          <DetailRow
            label="Completed"
            value={row.completedAt ? formatCreated(row.completedAt).title : '—'}
          />
          <DetailRow label="Duration" value={formatDuration(row.durationMs)} />
          <DetailRow label="Result" value={row.resultSummary || '—'} />
          <DetailRow label="Error" value={row.error || '—'} />
        </dl>

        <section className="flex flex-col gap-2 border-t border-border-default pt-5">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Identifiers
          </span>
          <dl className="flex flex-col gap-2 text-sm">
            <AuditIdRow label="Thread" value={row.threadId} />
            <AuditIdRow label="Tool run" value={row.toolRunId} />
            <AuditIdRow label="Action request" value={row.actionRequestId} />
          </dl>
        </section>

        <section className="flex flex-col gap-2 border-t border-border-default pt-5">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Arguments
          </span>
          {loading ? (
            <p className="text-sm text-text-secondary">Loading arguments…</p>
          ) : error ? (
            <p className="text-sm text-status-danger" role="alert">
              {error}
            </p>
          ) : detail ? (
            <pre className="max-h-72 overflow-auto rounded-md border border-border-default bg-surface p-3 font-mono text-xs text-text-primary">
              {formatJson(detail.args)}
            </pre>
          ) : (
            <p className="text-sm text-text-secondary">No arguments recorded.</p>
          )}
        </section>
      </div>
    </DrawerContent>
  )
}
