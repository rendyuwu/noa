'use client'

import type { Dispatch, SetStateAction } from 'react'

import { DatePicker, FilterBar, FormField, Input, Select } from '@gio/bigsu-ui'

import type { ToolRunFilters } from '@/lib/admin/audit/types'

// Server-driven filter bar for the audit list (T55). FilterBar owns only layout;
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
// LibreChat's own conversation id via a header (T57) and is a grouping label,
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
