import { describe, expect, it } from 'vitest'

import {
  formatCreated,
  formatDuration,
  formatJson,
  humanizeToolName,
  resolveActionStatus,
  resolveRiskBadge,
  resolveToolRunStatus,
} from './audit-format'
import type { AuditActionRequestListItem } from './types'

function actionItem(over: Partial<AuditActionRequestListItem>): AuditActionRequestListItem {
  return {
    actionRequestId: 'a',
    threadId: 't',
    toolName: 'whm_create_account',
    risk: 'CHANGE',
    status: 'PENDING',
    createdAt: '2026-07-01T00:00:00.000Z',
    updatedAt: '2026-07-01T00:00:00.000Z',
    hasReceipt: false,
    ...over,
  }
}

describe('resolveActionStatus', () => {
  it('maps a completed terminal phase to the Completed StatusChip', () => {
    const view = resolveActionStatus(actionItem({ terminalPhase: 'completed', status: 'APPROVED' }))
    expect(view).toEqual({ kind: 'status', status: 'Completed' })
  })

  it('lets the terminal phase win over the request status', () => {
    // An APPROVED request whose receipt failed reads as Failed, not Approved.
    const view = resolveActionStatus(actionItem({ terminalPhase: 'failed', status: 'APPROVED' }))
    expect(view).toEqual({ kind: 'status', status: 'Failed' })
  })

  it('corrects the API "In Progress" conflict: APPROVED without a receipt renders Approved', () => {
    const view = resolveActionStatus(actionItem({ status: 'APPROVED', terminalPhase: null }))
    expect(view).toEqual({ kind: 'status', status: 'Approved' })
  })

  it('maps PENDING and DENIED to standard statuses', () => {
    expect(resolveActionStatus(actionItem({ status: 'PENDING' }))).toEqual({
      kind: 'status',
      status: 'Pending',
    })
    expect(resolveActionStatus(actionItem({ status: 'DENIED' }))).toEqual({
      kind: 'status',
      status: 'Rejected',
    })
  })

  it('falls back to a neutral Badge for an unknown status', () => {
    expect(resolveActionStatus(actionItem({ status: 'WEIRD', terminalPhase: null }))).toEqual({
      kind: 'badge',
      label: 'WEIRD',
      variant: 'neutral',
    })
  })
})

describe('resolveToolRunStatus', () => {
  it('maps the standard tool-run statuses through StatusChip', () => {
    expect(resolveToolRunStatus('COMPLETED')).toEqual({ kind: 'status', status: 'Completed' })
    expect(resolveToolRunStatus('FAILED')).toEqual({ kind: 'status', status: 'Failed' })
    // STARTED is in-flight → the nearest standard status is Active.
    expect(resolveToolRunStatus('STARTED')).toEqual({ kind: 'status', status: 'Active' })
  })

  it('falls back to a neutral Badge for an unknown status', () => {
    expect(resolveToolRunStatus('QUEUED')).toEqual({
      kind: 'badge',
      label: 'QUEUED',
      variant: 'neutral',
    })
  })
})

describe('resolveRiskBadge', () => {
  it('renders CHANGE as a warning badge and READ as neutral', () => {
    expect(resolveRiskBadge('CHANGE')).toEqual({ label: 'Change', variant: 'warning' })
    expect(resolveRiskBadge('READ')).toEqual({ label: 'Read', variant: 'neutral' })
  })

  it('handles missing risk', () => {
    expect(resolveRiskBadge(null)).toEqual({ label: 'Unknown', variant: 'neutral' })
  })
})

describe('humanizeToolName', () => {
  it('strips the known prefix and titlecases the words', () => {
    expect(humanizeToolName('whm_create_account')).toBe('Create Account')
    expect(humanizeToolName('pmg_whitelist_search')).toBe('Whitelist Search')
  })

  it('preserves acronym overrides', () => {
    expect(humanizeToolName('whm_csf_allow')).toBe('CSF Allow')
  })
})

describe('formatters', () => {
  it('formats a duration in ms and seconds, and dashes missing values', () => {
    expect(formatDuration(250)).toBe('250ms')
    expect(formatDuration(1500)).toBe('1.5s')
    expect(formatDuration(null)).toBe('—')
  })

  it('returns a dash for an unparseable created timestamp', () => {
    expect(formatCreated('not-a-date').primary).toBe('—')
    expect(formatCreated('2026-07-01T00:00:00.000Z').title).toBe('2026-07-01T00:00:00.000Z')
  })

  it('pretty-prints JSON and degrades gracefully on a cycle', () => {
    expect(formatJson({ a: 1 })).toBe('{\n  "a": 1\n}')
    const cyclic: Record<string, unknown> = {}
    cyclic.self = cyclic
    expect(typeof formatJson(cyclic)).toBe('string')
  })
})
