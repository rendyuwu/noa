'use client'

import { createIdColumn, type DataTableProps } from '@gio/bigsu-ui'

import {
  formatCreated,
  formatDuration,
  humanizeToolName,
  resolveRiskBadge,
  resolveToolRunStatus,
} from '@/lib/admin/audit/audit-format'
import type { AuditToolRunListItem } from '@/lib/admin/audit/types'

import { AuditStatusCell } from './audit-status-cell'

// DataTable columns for the tool-run audit list (issue #111). The tool run id
// renders through createIdColumn (full, monospace, copyable). Status renders
// through StatusChip; tool risk through a Badge. createdAt keeps its raw
// accessorKey so the sort orders by the real timestamp.
//
// Four columns were added once the list stopped being the only audit view:
// requester, conversation ref, completed and result. Every one of them was
// already on the wire — `AuditToolRunListItemResponse` has returned all four
// since the route shipped — and none of them was drawn, so an administrator had
// to open a drawer per row to answer "who ran this" or "what did it say". No new
// endpoint, no new field, and the cheapest half of making the audit trail
// legible: pure rendering over data the API was already paying to serialise.
//
// `resultSummary` is truncated in the cell and carried whole in the `title`. It
// is capped at the write (`core.audit.summaries`), so what arrives here may
// already end in an ellipsis; shortening it again in the cell must not read as
// that cap, which is why the full stored string stays reachable on hover and in
// the drawer.
export function buildToolRunColumns(): DataTableProps<AuditToolRunListItem>['columns'] {
  return [
    createIdColumn<AuditToolRunListItem>({ accessorKey: 'toolRunId', header: 'Tool run' }),
    {
      accessorKey: 'toolName',
      header: 'Tool',
      cell: ({ row }) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-sm font-medium text-text-primary">
            {humanizeToolName(row.original.toolName)}
          </span>
          <span className="truncate font-mono text-xs text-text-secondary">
            {row.original.toolName}
          </span>
        </div>
      ),
    },
    {
      id: 'risk',
      header: 'Risk',
      cell: ({ row }) => {
        const risk = resolveRiskBadge(row.original.risk)
        return <AuditStatusCell view={{ kind: 'badge', label: risk.label, variant: risk.variant }} />
      },
    },
    {
      id: 'requestedByEmail',
      header: 'Requester',
      cell: ({ row }) => (
        <span className="truncate text-sm text-text-secondary">
          {row.original.requestedByEmail?.trim() || '—'}
        </span>
      ),
    },
    {
      id: 'conversationRef',
      header: 'Conversation',
      cell: ({ row }) => (
        <span className="truncate font-mono text-xs text-text-secondary">
          {row.original.conversationRef?.trim() || '—'}
        </span>
      ),
    },
    {
      id: 'duration',
      header: 'Duration',
      cell: ({ row }) => (
        <span className="font-mono text-xs text-text-secondary">
          {formatDuration(row.original.durationMs)}
        </span>
      ),
    },
    {
      accessorKey: 'createdAt',
      header: 'Created',
      cell: ({ row }) => {
        const created = formatCreated(row.original.createdAt)
        return (
          <span className="text-sm text-text-secondary" title={created.title}>
            {created.primary}
            {created.secondary ? ` · ${created.secondary}` : ''}
          </span>
        )
      },
    },
    {
      // "Finished", not "Completed": the Status column two cells over renders
      // the literal word "Completed" for a run that succeeded, and a header
      // spelled the same as one of its neighbour's values reads as a filter on
      // that value. The drawer keeps "Completed" — there is no status column
      // beside it there.
      id: 'completedAt',
      header: 'Finished',
      cell: ({ row }) => {
        const completed = row.original.completedAt
          ? formatCreated(row.original.completedAt)
          : null
        return (
          <span className="text-sm text-text-secondary" title={completed?.title ?? ''}>
            {completed ? completed.primary : '—'}
          </span>
        )
      },
    },
    {
      id: 'resultSummary',
      header: 'Result',
      cell: ({ row }) => {
        const summary = row.original.resultSummary?.trim() ?? ''
        return (
          <span
            className="block max-w-[22rem] truncate font-mono text-xs text-text-secondary"
            title={summary}
          >
            {summary || '—'}
          </span>
        )
      },
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => <AuditStatusCell view={resolveToolRunStatus(row.original.status)} />,
    },
  ]
}
