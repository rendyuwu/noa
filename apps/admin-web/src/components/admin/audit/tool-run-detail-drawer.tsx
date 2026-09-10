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
  humanizeToolName,
  resolveRiskBadge,
  resolveToolRunStatus,
} from '@/lib/admin/audit/audit-format'
import type { AuditToolRunDetail, AuditToolRunListItem } from '@/lib/admin/audit/types'

import { AuditStatusCell } from './audit-status-cell'
import { AuditIdRow, DetailRow, JsonBlock } from './audit-detail-rows'

// Contextual detail for one tool run (T55). A Drawer inspects a run alongside
// the list without losing it — the audit trail is append-only and its writers
// are elsewhere, so there is no edit or delete affordance here. Technical
// identifiers render full and monospace, never truncated, so an operator can
// copy and grep them.
//
// The arguments block is redacted by the API at the moment the row is *written*,
// so sensitive tool arguments never reach the DOM, and there is no export path
// off this panel.
//
// Three ported rows are gone with the columns behind them: "Thread" (NOA has no
// threads), "Action request" (an approved change's run links the other way) and
// "Error" — a sanitised error code lands in `resultSummary`, which is why T35
// left `tool_runs` without an `error` column. "Conversation ref" takes the first
// slot: a grouping label, null unless LibreChat supplies one.
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
        </dl>

        <section className="flex flex-col gap-2 border-t border-border-default pt-5">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Identifiers
          </span>
          <dl className="flex flex-col gap-2 text-sm">
            <AuditIdRow label="Tool run" value={row.toolRunId} />
            <AuditIdRow label="Conversation ref" value={row.conversationRef} />
            <AuditIdRow label="Requester" value={detail?.requestedByUserId} />
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
            <JsonBlock value={detail.args} />
          ) : (
            <p className="text-sm text-text-secondary">No arguments recorded.</p>
          )}
        </section>
      </div>
    </DrawerContent>
  )
}
