'use client'

import type { Dispatch, SetStateAction } from 'react'

import { DatePicker, FilterBar, FormField, Input, Select } from '@gio/bigsu-ui'

import type { ActionRequestFilters, ToolRunFilters } from '@/lib/admin/audit/types'

// Server-driven filter bar for the audit list. FilterBar owns only layout;
// the page owns the draft filter state and applies it against the server query.
// Every control is explicitly labelled (FormField / aria-label), and the search
// input maps to the "tool name" server filter. The bar stays presentational —
// Apply/Clear are the page's, wired via onApply/onClear.
//
// Every control here maps to a parameter the API accepts
// (`TOOL_RUN_QUERY_KEYS`). The ported bar also offered a "Thread ID" box and a
// terminal-phase select; NOA has no threads and serves no receipt route, so a
// thread filter would have narrowed on a column that does not exist. The
// replacement is `conversationRef` — free text, because the value comes from
// LibreChat's own conversation id via a header and is a grouping label,
// never a uuid NOA mints.

const YMD = 'yyyy-MM-dd'

function toDayInput(value: string): Date | undefined {
  if (!value) return undefined
  const date = new Date(`${value}T00:00:00.000Z`)
  return Number.isNaN(date.getTime()) ? undefined : date
}

function fromDayInput(date: Date | undefined): string {
  if (!date || Number.isNaN(date.getTime())) return ''
  return date.toISOString().slice(0, 10)
}

const TOOL_STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  { value: 'STARTED', label: 'Started' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'FAILED', label: 'Failed' },
]

const RISK_OPTIONS = [
  { value: '', label: 'Any risk' },
  { value: 'READ', label: 'Read-only' },
  { value: 'CHANGE', label: 'Change' },
]

function DateRangeFields({
  fromDate,
  toDate,
  onFrom,
  onTo,
}: {
  fromDate: string
  toDate: string
  onFrom: (value: string) => void
  onTo: (value: string) => void
}) {
  return (
    <>
      <FormField label="From" htmlFor="audit-from" className="w-40">
        <DatePicker
          id="audit-from"
          aria-label="From date"
          dateFormat={YMD}
          value={toDayInput(fromDate)}
          onChange={(date) => onFrom(fromDayInput(date))}
        />
      </FormField>
      <FormField label="To" htmlFor="audit-to" className="w-40">
        <DatePicker
          id="audit-to"
          aria-label="To date"
          dateFormat={YMD}
          value={toDayInput(toDate)}
          onChange={(date) => onTo(fromDayInput(date))}
        />
      </FormField>
    </>
  )
}

export function ToolRunFilterBar({
  draft,
  setDraft,
  onApply,
  onClear,
}: {
  draft: ToolRunFilters
  setDraft: Dispatch<SetStateAction<ToolRunFilters>>
  onApply: () => void
  onClear: () => void
}) {
  return (
    <FilterBar
      search={{
        value: draft.toolName,
        onChange: (value) => setDraft((prev) => ({ ...prev, toolName: value })),
        placeholder: 'Search by tool name',
      }}
      onClear={onClear}
    >
      <DateRangeFields
        fromDate={draft.fromDate}
        toDate={draft.toDate}
        onFrom={(value) => setDraft((prev) => ({ ...prev, fromDate: value }))}
        onTo={(value) => setDraft((prev) => ({ ...prev, toDate: value }))}
      />
      <Select
        aria-label="Status"
        className="w-40"
        options={TOOL_STATUS_OPTIONS}
        value={draft.status}
        onValueChange={(value) => setDraft((prev) => ({ ...prev, status: value }))}
      />
      <Select
        aria-label="Risk"
        className="w-40"
        options={RISK_OPTIONS}
        value={draft.risk}
        onValueChange={(value) => setDraft((prev) => ({ ...prev, risk: value }))}
      />
      <FormField label="Requested by" htmlFor="tool-run-requested-by" className="w-52">
        <Input
          id="tool-run-requested-by"
          placeholder="email contains…"
          value={draft.requestedByEmail}
          onChange={(event) =>
            setDraft((prev) => ({ ...prev, requestedByEmail: event.target.value }))
          }
        />
      </FormField>
      <FormField label="Conversation ref" htmlFor="tool-run-conversation" className="w-56">
        <Input
          id="tool-run-conversation"
          className="font-mono text-xs"
          placeholder="conversation label"
          value={draft.conversationRef}
          onChange={(event) => setDraft((prev) => ({ ...prev, conversationRef: event.target.value }))}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onApply()
          }}
        />
      </FormField>
    </FilterBar>
  )
}

// The same bar over the authorisation trail's five parameters
// (`ACTION_REQUEST_QUERY_KEYS`). Two of the tool-run controls are absent and
// their absence is the point: `risk` would scope nothing, because every row here
// is a CHANGE by construction; `conversationRef` is a grouping label worth
// filtering on where READs live too, and this list is not that. The date fields
// are the same component, not a second copy of the day-to-instant conversion.
//
// The four decision values below mirror `ActionRequestStatus` in
// `core/db/lifecycle.py`, and that mirror is hand-kept and unbound across the
// language boundary: nothing reads the Python enum against this list, and
// nothing in this package can. Said out loud rather than left silent, because a
// fifth member added there would leave this dropdown quietly offering four and
// those rows unfilterable (V119 takes a stated gap; it does not take a silent
// one). The test beside this file pins the TypeScript half only, so a careless
// edit here goes red and a deliberate one has to be made against the enum on
// purpose. Contrast `ACTION_REQUEST_QUERY_KEYS`, which is asserted on both sides
// of the boundary and is what a bound mirror looks like.
export const ACTION_REQUEST_STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  { value: 'PENDING', label: 'Pending' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'DENIED', label: 'Denied' },
  { value: 'EXPIRED', label: 'Expired' },
]

export function ActionRequestFilterBar({
  draft,
  setDraft,
  onApply,
  onClear,
}: {
  draft: ActionRequestFilters
  setDraft: Dispatch<SetStateAction<ActionRequestFilters>>
  onApply: () => void
  onClear: () => void
}) {
  return (
    <FilterBar
      search={{
        value: draft.toolName,
        onChange: (value) => setDraft((prev) => ({ ...prev, toolName: value })),
        placeholder: 'Search by tool name',
      }}
      onClear={onClear}
    >
      <DateRangeFields
        fromDate={draft.fromDate}
        toDate={draft.toDate}
        onFrom={(value) => setDraft((prev) => ({ ...prev, fromDate: value }))}
        onTo={(value) => setDraft((prev) => ({ ...prev, toDate: value }))}
      />
      <Select
        aria-label="Status"
        className="w-40"
        options={ACTION_REQUEST_STATUS_OPTIONS}
        value={draft.status}
        onValueChange={(value) => setDraft((prev) => ({ ...prev, status: value }))}
      />
      <FormField label="Requested by" htmlFor="action-request-requested-by" className="w-52">
        <Input
          id="action-request-requested-by"
          placeholder="email contains…"
          value={draft.requestedByEmail}
          onChange={(event) =>
            setDraft((prev) => ({ ...prev, requestedByEmail: event.target.value }))
          }
          onKeyDown={(event) => {
            if (event.key === 'Enter') onApply()
          }}
        />
      </FormField>
    </FilterBar>
  )
}
