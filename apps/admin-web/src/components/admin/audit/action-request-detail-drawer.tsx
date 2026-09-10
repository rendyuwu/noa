'use client'

import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from '@gio/bigsu-ui'

import {
  formatCreated,
  humanizeToolName,
  resolveActionRequestStatus,
} from '@/lib/admin/audit/audit-format'
import type {
  AuditActionReceipt,
  AuditActionRequestDetail,
  AuditActionRequestListItem,
} from '@/lib/admin/audit/types'

import { AuditStatusCell } from './audit-status-cell'
import { AuditIdRow, DetailRow, JsonBlock } from './audit-detail-rows'

// Contextual detail for one CHANGE authorisation, per the admin API's contract. A Drawer, like
// the tool-run one beside it, and for the same reason: the trail is append-only
// and its writers are the approval gate, the decision path and the executor, so
// there is no edit or delete affordance anywhere here.
//
// Three blocks, and the order is the order an auditor reads them in: **why** the
// change was authorised, **what NOA knew** when it asked, and **what the change
// did**. The first is `action_requests.reason` and it is the reason this whole
// vertical exists — it has been written since the approval gate shipped and,
// until these routes, was readable by nothing.
//
// The gate context and both receipt halves render as JSON in a monospace block
// rather than as named rows. The keys under `evidence` are whichever tool's own
// preflight vocabulary and the halves are two different tools' vocabularies
// again, so a named-row renderer would have to be widened by every tool ever
// added — and the one that was not widened would silently stop showing a field.
// The block shows whatever is there.
//
// **Nothing in those blocks becomes a link.** A `yopass_url` can appear in the
// after-half or in the delta's delivered credential, and that URL is consumed
// once: a hover preview, a prefetch or a mis-click spends the operator's own
// delivery and the password is then unreachable by anyone. Rendering it as text
// costs nothing and is a criterion, not a preference. The rule is enforced in
// `JsonBlock` (`audit-detail-rows.tsx`), which is the single renderer both audit
// drawers hand every blob to.
//
// **Proposal note for the design system.** BIGSU's two approval-detail
// components were both considered for this panel and neither is used. Their
// docs say to use them on *every* approval detail page, so the deviation is
// deliberate and recorded here rather than left to read as an oversight.
//
// `ApprovalStepper` renders an ordered chain and maps each step onto four fixed
// statuses — `completed | current | pending | rejected` — a mapping its docs
// state is intentionally not customizable. NOA's decision is one operator
// answering once, so the chain is a single step; and one of NOA's four outcomes
// has no target in that set. An EXPIRED request was not rejected — nobody
// answered it — and it is not pending once the clock has run out, so rendering
// it would mean picking a status that says something false. That is the same
// call the list makes when it draws an expiry as a Badge instead of borrowing
// "Rejected". The stepper's usage rules also put Approve / Reject / Request
// Changes beside it, and this surface has no decision affordance by
// construction: the one writer of a terminal status is the operator's approval
// card, not this app.
//
// `AuditTrail` wants a sequence of actor/action/timestamp events with detail in
// `metadata`, typed `Record<string, string>` and rendered as flat monospace
// `key=value` rows. Its docs name before/after values as the canonical use of
// that map — but the before/after here are two arbitrary JSON documents written
// by whichever tool ran, nested and unbounded in shape, and the nesting is
// precisely what the approval card could not show and this surface exists to
// show. Flattening them into string pairs would drop it; stringifying them puts
// a whole document in one `key=value` row. There is also no sequence to render:
// `action_requests` records the ask and the answer, not a history.
//
// So no new visual pattern is invented here — the panel is composed from
// shipped BIGSU primitives (`Drawer`, `Badge`/`StatusChip` through
// `AuditStatusCell`) plus plain definition lists and the shared `JsonBlock`. A
// single-decision detail block, or an `AuditTrail` whose metadata renders
// nested values verbatim, would make this panel a caller.
export function ActionRequestDetailDrawer({
  row,
  detail,
  detailLoading,
  detailError,
  receipt,
  receiptLoading,
  receiptError,
  onClose,
}: {
  row: AuditActionRequestListItem | null
  detail?: AuditActionRequestDetail
  detailLoading: boolean
  detailError: string | null
  receipt?: AuditActionReceipt
  receiptLoading: boolean
  receiptError: string | null
  onClose: () => void
}) {
  return (
    <Drawer open={row !== null} onOpenChange={(open) => (open ? undefined : onClose())}>
      {row ? (
        <ActionRequestDetailContent
          key={row.actionRequestId}
          row={row}
          detail={detail}
          detailLoading={detailLoading}
          detailError={detailError}
          receipt={receipt}
          receiptLoading={receiptLoading}
          receiptError={receiptError}
        />
      ) : null}
    </Drawer>
  )
}

function ActionRequestDetailContent({
  row,
  detail,
  detailLoading,
  detailError,
  receipt,
  receiptLoading,
  receiptError,
}: {
  row: AuditActionRequestListItem
  detail?: AuditActionRequestDetail
  detailLoading: boolean
  detailError: string | null
  receipt?: AuditActionReceipt
  receiptLoading: boolean
  receiptError: string | null
}) {
  const opened = formatCreated(row.createdAt)

  return (
    <DrawerContent>
      <DrawerHeader>
        <DrawerTitle>{humanizeToolName(row.toolName)}</DrawerTitle>
        <DrawerDescription>{row.requestedByEmail?.trim() || 'Unknown requester'}</DrawerDescription>
      </DrawerHeader>

      <div className="flex flex-col gap-6">
        <section className="flex flex-wrap items-center gap-2">
          <AuditStatusCell view={resolveActionRequestStatus(row.status)} />
        </section>

        <section className="flex flex-col gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Reason
          </span>
          <ReasonBlock
            status={row.status}
            reason={detail?.reason}
            loading={detailLoading}
            error={detailError}
            loaded={Boolean(detail)}
          />
        </section>

        <dl className="flex flex-col gap-2 text-sm">
          <DetailRow label="Tool" value={<span className="font-mono text-xs">{row.toolName}</span>} />
          <DetailRow label="Opened" value={opened.title || '—'} />
          <DetailRow
            label="Decided"
            value={row.decidedAt ? formatCreated(row.decidedAt).title : '—'}
          />
          <DetailRow label="Expires" value={formatCreated(row.expiresAt).title || '—'} />
        </dl>

        <section className="flex flex-col gap-2 border-t border-border-default pt-5">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Identifiers
          </span>
          <dl className="flex flex-col gap-2 text-sm">
            <AuditIdRow label="Request" value={row.actionRequestId} />
            <AuditIdRow label="Tool run" value={row.toolRunId} />
            <AuditIdRow label="Conversation ref" value={row.conversationRef} />
            <AuditIdRow
              label="LibreChat account"
              value={librechatAccount(detail, detailLoading, detailError)}
            />
          </dl>
        </section>

        <section className="flex flex-col gap-2 border-t border-border-default pt-5">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Approval context
          </span>
          {detailLoading ? (
            <p className="text-sm text-text-secondary">Loading approval context…</p>
          ) : detailError ? (
            <p className="text-sm text-status-danger" role="alert">
              {detailError}
            </p>
          ) : detail ? (
            <JsonBlock value={detail.approvalContext} />
          ) : (
            <p className="text-sm text-text-secondary">No approval context recorded.</p>
          )}
        </section>

        <section className="flex flex-col gap-2 border-t border-border-default pt-5">
          <span className="text-xs font-medium uppercase tracking-wide text-text-secondary">
            Receipt
          </span>
          <ReceiptBlock
            hasReceipt={row.hasReceipt}
            receipt={receipt}
            loading={receiptLoading}
            error={receiptError}
          />
        </section>
      </div>
    </DrawerContent>
  )
}

// The LibreChat account rides in with the detail fetch rather than with the
// list row, so this cell has three states and not two. An empty value renders
// as an em dash, and an em dash under "LibreChat account" says *there is none* —
// an absence claim manufactured out of an answer that has not arrived yet.
// A source that could not answer gets named beside the verdict instead of
// folding into the benign value, which is the same treatment the reason
// and approval-context blocks below already give the same fetch.
function librechatAccount(
  detail: AuditActionRequestDetail | undefined,
  loading: boolean,
  error: string | null,
): string {
  if (loading) return 'Loading…'
  if (error) return 'Unavailable'
  if (!detail) return 'Not loaded'

  const requester = detail.approvalContext?.requester
  if (requester && typeof requester === 'object' && 'librechat_user_id' in requester) {
    return String((requester as Record<string, unknown>).librechat_user_id ?? '')
  }
  return ''
}

// The operator's own words, or an explicit statement of why there are none.
// "Nobody has decided yet" and "the request expired unanswered" are two facts,
// and neither of them is an empty box: a blank panel under a "Reason" heading
// reads as a rendering failure, which is the one thing it must never look like.
function ReasonBlock({
  status,
  reason,
  loading,
  error,
  loaded,
}: {
  status: string
  reason?: string | null
  loading: boolean
  error: string | null
  loaded: boolean
}) {
  if (loading) return <p className="text-sm text-text-secondary">Loading reason…</p>
  if (error)
    return (
      <p className="text-sm text-status-danger" role="alert">
        {error}
      </p>
    )
  if (!loaded) return <p className="text-sm text-text-secondary">Open this request to load it.</p>

  const text = reason?.trim() ?? ''
  if (text) {
    return <p className="whitespace-pre-wrap break-words text-sm text-text-primary">{text}</p>
  }

  const normalized = status.trim().toUpperCase()
  if (normalized === 'PENDING') {
    return <p className="text-sm text-text-secondary">Not decided yet, so no reason was given.</p>
  }
  if (normalized === 'EXPIRED') {
    return (
      <p className="text-sm text-text-secondary">
        This request expired unanswered, so nobody gave a reason.
      </p>
    )
  }
  return <p className="text-sm text-text-secondary">No reason recorded.</p>
}

// What the change did, in the halves it was written as. `delta` absent is a
// fact, not a gap: the runner stated none, which is what separates an executor
// refusal from a runner failure — both of which are `ok: false`, so the field is
// reported as absent rather than drawn as an empty object.
function ReceiptBlock({
  hasReceipt,
  receipt,
  loading,
  error,
}: {
  hasReceipt: boolean
  receipt?: AuditActionReceipt
  loading: boolean
  error: string | null
}) {
  if (!hasReceipt) {
    return (
      <p className="text-sm text-text-secondary">
        No run was started for this request, so there is no receipt.
      </p>
    )
  }
  if (loading) return <p className="text-sm text-text-secondary">Loading receipt…</p>
  if (error)
    return (
      <p className="text-sm text-status-danger" role="alert">
        {error}
      </p>
    )
  if (!receipt) return <p className="text-sm text-text-secondary">No receipt loaded.</p>

  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-2 text-sm">
        <DetailRow label="Outcome" value={receipt.ok ? 'Succeeded' : 'Failed'} />
        <DetailRow
          label="Error code"
          value={
            receipt.errorCode ? (
              <span className="font-mono text-xs">{receipt.errorCode}</span>
            ) : (
              '—'
            )
          }
        />
      </dl>
      <LabelledJson label="Change" value={receipt.delta} absent="The runner stated no change." />
      <LabelledJson label="Before" value={receipt.before} absent="No before-state recorded." />
      <LabelledJson label="After" value={receipt.after} absent="No after-state recorded." />
    </div>
  )
}

function LabelledJson({
  label,
  value,
  absent,
}: {
  label: string
  value: unknown
  absent: string
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-text-secondary">{label}</span>
      {value === null || value === undefined ? (
        <p className="text-sm text-text-secondary">{absent}</p>
      ) : (
        <JsonBlock value={value} />
      )}
    </div>
  )
}
