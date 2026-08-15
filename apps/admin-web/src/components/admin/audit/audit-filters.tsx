'use client'

import type { Dispatch, SetStateAction } from 'react'

import { DatePicker, FilterBar, FormField, Input, Select } from '@gio/bigsu-ui'

import type { ActionFilters, ToolRunFilters } from '@/lib/admin/audit/types'

// Server-driven filter bars for the audit surfaces (issue #111). FilterBar owns
// only layout; the page owns the draft filter state and applies it against the
// server query. Every control is explicitly labelled (FormField / aria-label),
// and the search input maps to the free-text "tool name" server filter. The
// bars stay presentational — Apply/Clear are the page's, wired via onClear.

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

const ACTION_STATUS_OPTIONS = [
  { value: '', label: 'Any status' },
  { value: 'PENDING', label: 'Pending' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'DENIED', label: 'Denied' },
]

const TERMINAL_PHASE_OPTIONS = [
  { value: '', label: 'Any phase' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'denied', label: 'Denied' },
]

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

export function ActionRequestFilterBar({
  draft,
  setDraft,
  onApply,
  onClear,
}: {
  draft: ActionFilters
  setDraft: Dispatch<SetStateAction<ActionFilters>>
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
        options={ACTION_STATUS_OPTIONS}
        value={draft.status}
        onValueChange={(value) => setDraft((prev) => ({ ...prev, status: value }))}
      />
      <Select
        aria-label="Terminal phase"
        className="w-44"
        options={TERMINAL_PHASE_OPTIONS}
        value={draft.terminalPhase}
        onValueChange={(value) => setDraft((prev) => ({ ...prev, terminalPhase: value }))}
      />
      <FormField label="Requested by" htmlFor="audit-requested-by" className="w-52">
        <Input
          id="audit-requested-by"
          placeholder="email contains…"
          value={draft.requestedByEmail}
          onChange={(event) =>
            setDraft((prev) => ({ ...prev, requestedByEmail: event.target.value }))
          }
        />
      </FormField>
      <FormField label="Thread ID" htmlFor="audit-thread" className="w-56">
        <Input
          id="audit-thread"
          className="font-mono text-xs"
          placeholder="uuid"
          value={draft.threadId}
          onChange={(event) => setDraft((prev) => ({ ...prev, threadId: event.target.value }))}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onApply()
          }}
        />
      </FormField>
    </FilterBar>
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
      <FormField label="Thread ID" htmlFor="tool-run-thread" className="w-56">
        <Input
          id="tool-run-thread"
          className="font-mono text-xs"
          placeholder="uuid"
          value={draft.threadId}
          onChange={(event) => setDraft((prev) => ({ ...prev, threadId: event.target.value }))}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onApply()
          }}
        />
      </FormField>
    </FilterBar>
  )
}
